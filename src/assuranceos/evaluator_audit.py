"""One real audit, over the evaluator's own repository.

The workspace collects. This concludes. Between those two verbs is everything an
audit function is actually paid for, and it is the part a connector demonstration
normally skips: a population that reconciles, a procedure that was released
before it saw the data, an agent that ran the procedure rather than forming an
opinion about it, and a finding a human has to accept before it is anything.

The run is deliberately ordered so each step can only be as strong as the one
before it:

1. **Approve a grant, then collect.** Two streams: the commits that reached the
   default branch in the period, and the pull-request path of each. Both are
   hashed into the evidence vault on arrival under the workspace's own tenant.
2. **Project.** The collected objects become the datasets ``SCM-02`` declares,
   read back out of the vault rather than out of the collector's memory, one
   evidence identifier per row.
3. **Execute the signed procedure**, inside the deterministic sandbox, through
   the agent's ``tests.execute`` tool. The agent cannot choose the population and
   cannot compute the answer; the release decides what effective means and it was
   signed before this repository was named.
4. **Conclude, under the gateway.** The agent's identity is issued for this task
   alone, its envelope grants four read tools, and a tool outside the envelope is
   denied under its own identity and the denial is recorded.
5. **Propose a finding, and stop.** Nothing here decides anything. The finding
   sits at proposed until a person accepts or returns it, which is the same gate
   the demonstration tenant runs through.
6. **Revoke the grant.** The authority ends with the work it authorised.

What this run must never do is flatter the repository it is pointed at. A commit
whose review path could not be established inside the collection budget is a
limitation, not a pass, and the conclusion says ``insufficient_evidence`` rather
than ``effective`` when that happens.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from .briefing import brief_from_lease, organization_brief, render_briefing
from .collection_projection import BoundPopulation, CollectionReader, ProjectionError, project_scm_02
from .connectors.exceptions import ConnectorRateLimitError
from .control_testing.demo import build_service as build_control_test_service
from .db.models import Engagement, EngagementTask
from .db.session import Database
from .evaluator_sandbox import EvaluatorSandbox
from .governance.domain_tools import DomainToolContext, register_domain_tools
from .governance.gateway import AgentGateway, GatewayDenied
from .governance.identity import AgentIdentityIssuer, AgentIdentityVerifier
from .governance.managed_armor import build_model_armor
from .governance.models_client import ModelClient
from .governance.persistence import DatabaseRevocationChecker, GovernanceRecorder
from .governance.runtime import GovernedAgentRuntime, QualityContext
from .governance.telemetry import AgentTracer, TelemetryConfig
from .models import ExecutionEnvelope
from .registry import AgentRegistry

AGENT_ROLE = "operating-effectiveness"
CONTROL_TEST = "SCM-02@1.0.0"

#: What this task may reach. Strictly less than the agent package declares: the
#: envelope grants, the package permits, and the difference is the point.
#: `connector.write` is absent and is probed for afterwards, so the boundary is
#: demonstrated rather than assumed.
GRANTED_TOOLS = ["evidence.query", "tests.execute", "population.reconcile", "exceptions.classify"]


class AuditError(RuntimeError):
    """The audit could not be completed. The message names the step that stopped."""


class _ScriptedAuditClient:
    """Drives the loop when no model is configured, taking a competent model's route.

    Deliberately *not* a fixed pair of replies. The second reply is composed from
    what the tools actually returned, so a run whose tool results were empty
    cannot still produce a confident conclusion -- which is the failure this
    whole path exists to rule out, and the one a two-element script would hide.

    It reports itself as ``scripted`` and the report carries that name, so an
    evaluator reading the result can tell which of the two ran.
    """

    model_name = "scripted"

    def __init__(self, control_test: str = CONTROL_TEST):
        self.test_id = control_test.split("@", 1)[0]
        self.calls: list[str] = []

    def generate(self, *, system_instruction: str, prompt: str, **_: Any) -> Any:
        from .governance.models_client import ScriptedClient

        self.calls.append(prompt)
        if "tool: tests.execute" not in prompt:
            text = json.dumps(
                {
                    "next_action": "use_tools",
                    "tool_calls": [{"tool": "tests.execute", "arguments": {"test_id": self.test_id}}],
                }
            )
        else:
            run = _run_from_prompt(prompt)
            text = json.dumps(_conclusion_from(run))
        return ScriptedClient(replies=[text]).generate(
            system_instruction=system_instruction, prompt=prompt
        )


def _conclusion_from(run: dict[str, Any]) -> dict[str, Any]:
    conclusion = str(run.get("conclusion") or "insufficient_evidence")
    exceptions = run.get("exceptions") or []
    subjects = ", ".join(str(item.get("subject_ref")) for item in exceptions[:4])
    evidence_ids = sorted(
        {str(value) for item in exceptions for value in (item.get("evidence_ids") or []) if value}
    )[:5]
    if conclusion == "ineffective":
        summary = (
            f"The signed {run.get('test_id')} release tested "
            f"{run.get('population_count')} change(s) that reached the default branch "
            f"and returned {run.get('exception_count')} exception(s) ({subjects}). "
            "Changes reached the branch without a reviewed pull request, so the "
            "control did not operate effectively over this period."
        )
    elif conclusion == "effective":
        summary = (
            f"The signed {run.get('test_id')} release tested "
            f"{run.get('population_count')} change(s) and returned no exception. "
            "Every change in the period arrived through a reviewed pull request."
        )
    else:
        summary = (
            f"The signed {run.get('test_id')} release reached no conclusion on "
            f"{run.get('population_count')} change(s): part of the population's review "
            "path was not established, and the result is reported as missing evidence "
            "rather than as a pass."
        )
    return {
        "conclusion": conclusion,
        "summary": summary,
        "evidence_ids": evidence_ids,
        "tool_calls": [],
        "requires_human_approval": True,
    }


def _run_from_prompt(prompt: str) -> dict[str, Any]:
    """Read the tests.execute result back out of the prompt the model was given.

    Parsing the first brace after the marker returns the *arguments* of the call
    rather than its result, which yields a run report full of nulls that still
    looks like a successful read.
    """

    index = prompt.find("tool: tests.execute")
    if index < 0:
        return {}
    block = prompt[index:]
    result_at = block.find("result:")
    if result_at < 0:
        return {}
    block = block[result_at:]
    start = block.find("{")
    if start < 0:
        return {}
    depth = 0
    for offset, character in enumerate(block[start:], start=start):
        depth += character == "{"
        depth -= character == "}"
        if depth == 0:
            try:
                return json.loads(block[start : offset + 1])
            except json.JSONDecodeError:
                return {}
    return {}


@dataclass
class AuditRequest:
    workspace_id: str
    connector_instance_id: str
    repository: str
    period_start: date
    period_end: date
    required_approvals: int = 1


def _iso(value: date) -> str:
    return value.isoformat()


def _instruction(repository: str, period_start: date, period_end: date) -> str:
    return (
        f"Determine whether changes reaching the default branch of {repository} "
        f"between {_iso(period_start)} and {_iso(period_end)} went through a "
        "reviewed pull request. Execute the signed control test SCM-02 over the "
        "collected population rather than reasoning about a sample, then read the "
        "exceptions it produced and say what they mean for this repository's "
        "change process. Cite the run and the evidence you relied on. If the "
        "procedure reports limitations, say so rather than concluding past them."
    )


class WorkspaceAudit:
    """Runs SCM-02 over a repository the evaluator owns, end to end."""

    def __init__(
        self,
        sandbox: EvaluatorSandbox,
        *,
        repository_root: Path,
        model_client: ModelClient | None = None,
        max_tool_rounds: int = 4,
    ):
        self.sandbox = sandbox
        self.database: Database = sandbox.database
        self.repository_root = repository_root
        self.model_client = model_client
        self.max_tool_rounds = max_tool_rounds

    # -- steps -------------------------------------------------------------

    def run(self, request: AuditRequest) -> dict[str, Any]:
        workspace = self.sandbox.get_workspace(request.workspace_id)
        instance = self.sandbox.connector(request.workspace_id, request.connector_instance_id)
        if instance.connector_type != "github":
            raise AuditError(
                "the released change-path procedure is defined over a repository's "
                f"commits; this connector is {instance.connector_type}"
            )
        # Shape first, and before anything reaches the network. A malformed
        # scope that got as far as the pre-flight would spend a provider request
        # discovering what the request line already showed, and on an
        # unauthenticated read those requests are the scarce thing.
        if request.repository.count("/") != 1 or not all(request.repository.split("/")):
            raise AuditError(
                f"{request.repository!r} is not a repository; name it as owner/repository"
            )
        if request.period_end < request.period_start:
            raise AuditError("the period ends before it starts")
        limits = self.sandbox.limits
        span = (request.period_end - request.period_start).days
        if span > limits.audit_period_days:
            raise AuditError(
                f"an evaluator audit covers at most {limits.audit_period_days} days; "
                f"this period is {span}"
            )

        self._preflight(request, authenticated=bool(instance.credential_ref))
        engagement_id, task_id = self._seed_engagement(workspace.tenant_id, request)
        grant = self.sandbox.approve_grant(
            request.workspace_id,
            request.connector_instance_id,
            streams=["commits", "commit_reviews"],
            scope_value=request.repository,
            purpose=(
                f"Change-path audit of {request.repository} for {workspace.company_name}, "
                f"{_iso(request.period_start)} to {_iso(request.period_end)}"
            ),
        )
        try:
            collection = self._collect(request, engagement_id, task_id)
            population = self._project(workspace.tenant_id, collection, request)
            agent = self._run_agent(
                workspace=workspace,
                request=request,
                engagement_id=engagement_id,
                task_id=task_id,
                population=population,
            )
        finally:
            # Whether the run concluded or failed, the authority it was given
            # ends here. A grant left open because a step raised is exactly the
            # condition this product reports against other people.
            revocation = self.sandbox.revoke_grant(
                request.workspace_id,
                grant["grant_id"],
                reason="the collection this grant authorised has finished",
            )

        return {
            "workspace_id": request.workspace_id,
            "tenant_id": workspace.tenant_id,
            "company_name": workspace.company_name,
            "repository": request.repository,
            "period": {"start": _iso(request.period_start), "end": _iso(request.period_end)},
            "engagement_id": engagement_id,
            "task_id": task_id,
            "grant": {**grant, "revoked": revocation},
            "collection": collection,
            "population": {
                "commits": len(population.datasets[0].records),
                "review_rows": len(population.datasets[1].records),
                "notes": population.notes,
                "parameters": population.parameters,
            },
            "control_test": agent["control_test"],
            "agent": agent["agent"],
            "finding": agent["finding"],
        }

    #: Below this many remaining provider requests, the audit refuses to start.
    #: It costs roughly one request per commit, so beginning with a nearly spent
    #: budget produces a half-collected population that reconciles to nothing --
    #: and it produces it several seconds in, after the evaluator has watched a
    #: spinner.
    MINIMUM_REQUEST_BUDGET = 40

    def _preflight(self, request: AuditRequest, *, authenticated: bool) -> None:
        """Ask the provider what is left before spending it.

        GitHub allows sixty requests an hour without a credential, which is
        under the cost of auditing a month of an ordinary repository. Finding
        that out at commit forty is a worse experience than being told now, and
        the difference between the two is the sentence this raises.
        """

        try:
            health = self.sandbox.health(request.workspace_id, request.connector_instance_id)
        except ConnectorRateLimitError as exc:
            # Nothing left at all, so even the health check was refused. The
            # advice is the same and the message should not be about a health
            # check, which is not the thing that went wrong.
            raise AuditError(
                "the provider's request quota for this hour is already spent. "
                + (
                    "Attach a personal access token, which raises the limit to five "
                    "thousand an hour, or wait for the quota to reset."
                    if not authenticated
                    else "Wait for the quota to reset, or narrow the period."
                )
            ) from exc
        except Exception as exc:
            raise AuditError(f"the provider did not answer a health check: {exc}") from exc
        if health.get("status") != "healthy":
            raise AuditError(f"the provider reports {health.get('status')}")
        remaining = (health.get("details") or {}).get("remaining")
        if not isinstance(remaining, int) or remaining >= self.MINIMUM_REQUEST_BUDGET:
            return
        reset = (health.get("details") or {}).get("reset")
        when = ""
        if isinstance(reset, (int, float)) and reset:
            when = (
                " The quota resets at "
                f"{datetime.fromtimestamp(reset, tz=timezone.utc).strftime('%H:%M UTC')}."
            )
        advice = (
            "Attach a personal access token, which raises the limit to five thousand an hour."
            if not authenticated
            else "Narrow the period, or wait for the quota to reset."
        )
        raise AuditError(
            f"the provider has {remaining} request(s) left this hour and this audit needs "
            f"about one per commit, so it would stop part-way through.{when} {advice}"
        )

    # -- collection --------------------------------------------------------

    def _collect(
        self, request: AuditRequest, engagement_id: str, task_id: str
    ) -> dict[str, Any]:
        limits = self.sandbox.limits
        since = datetime.combine(request.period_start, datetime.min.time(), tzinfo=timezone.utc)
        until = datetime.combine(
            request.period_end, datetime.max.time().replace(microsecond=0), tzinfo=timezone.utc
        )
        shared = {
            "since": since.isoformat().replace("+00:00", "Z"),
            "until": until.isoformat().replace("+00:00", "Z"),
            "page_size": 100,
        }
        runs: dict[str, Any] = {}
        for stream, parameters in (
            ("commits", dict(shared)),
            (
                "commit_reviews",
                {**shared, "max_lookups": limits.audit_max_lookups, "include_approvals": True},
            ),
        ):
            try:
                summary = self.sandbox.collect(
                    request.workspace_id,
                    request.connector_instance_id,
                    stream=stream,
                    scope_value=request.repository,
                    engagement_id=engagement_id,
                    task_id=task_id,
                    parameters=parameters,
                    max_pages=limits.max_pages,
                    max_objects=limits.audit_max_commits,
                    max_requests=limits.audit_max_requests,
                )
            except ConnectorRateLimitError as exc:
                # The pre-flight makes this unlikely and cannot make it
                # impossible: the budget is shared with anything else using the
                # same address. What matters is that it arrives as a sentence
                # about quota rather than as a stack trace about transports.
                raise AuditError(
                    "the provider's request quota ran out part-way through collecting "
                    f"{stream}. Attach a personal access token, or narrow the period, and "
                    "run it again. Nothing partial was concluded from."
                ) from exc
            runs[stream] = {
                "run_id": summary.run_id,
                "status": summary.status,
                "objects_seen": summary.objects_seen,
                "objects_ingested": summary.objects_ingested,
                "objects_unchanged": summary.objects_unchanged,
                "pages": summary.metrics.get("pages"),
            }
        if not runs["commits"]["objects_seen"]:
            raise AuditError(
                f"no commit reached the default branch of {request.repository} between "
                f"{_iso(request.period_start)} and {_iso(request.period_end)}; there is "
                "no population to test"
            )
        return runs

    def _project(
        self, tenant_id: str, collection: dict[str, Any], request: AuditRequest
    ) -> BoundPopulation:
        reader = CollectionReader(self.database, self.sandbox.vault, tenant_id)
        try:
            return project_scm_02(
                reader=reader,
                commits_run_id=collection["commits"]["run_id"],
                commit_reviews_run_id=collection["commit_reviews"]["run_id"],
                required_approvals=request.required_approvals,
                period=(request.period_start, request.period_end),
            )
        except ProjectionError as exc:
            raise AuditError(str(exc)) from exc

    # -- the engagement this work belongs to -------------------------------

    def _seed_engagement(self, tenant_id: str, request: AuditRequest) -> tuple[str, str]:
        engagement_id = f"eng_{request.workspace_id[:16]}_scm"
        task_id = f"tsk_{request.workspace_id[:16]}_scm"
        with self.database.transaction() as session:
            engagement = session.get(Engagement, engagement_id)
            if engagement is None:
                session.add(
                    Engagement(
                        engagement_id=engagement_id,
                        tenant_id=tenant_id,
                        code="SCM-EVAL",
                        title=f"Change management — {request.repository}",
                        status="fieldwork",
                        audit_pack_ref="software-change-management@2.0.0",
                        period_start=request.period_start,
                        period_end=request.period_end,
                    )
                )
                session.flush()
            else:
                engagement.status = "fieldwork"
                engagement.period_start = request.period_start
                engagement.period_end = request.period_end
            task = session.get(EngagementTask, task_id)
            if task is None:
                session.add(
                    EngagementTask(
                        task_id=task_id,
                        tenant_id=tenant_id,
                        engagement_id=engagement_id,
                        task_key="test-reviewed-change-path",
                        task_type="agent",
                        definition_version="1.0.0",
                        status="running",
                        assigned_agent_role=AGENT_ROLE,
                        idempotency_key=f"{engagement_id}:test-reviewed-change-path",
                        execution_policy_json={
                            "action": _instruction(
                                request.repository, request.period_start, request.period_end
                            ),
                            "control_test": CONTROL_TEST,
                            "quality_rules": [
                                "Do not conclude on the control without executing the signed procedure.",
                                "Do not call the control effective while the run reports exceptions.",
                                "Report limitations rather than concluding past them.",
                            ],
                        },
                    )
                )
            else:
                task.status = "running"
        return engagement_id, task_id

    # -- the agent ---------------------------------------------------------

    def _run_agent(
        self,
        *,
        workspace: Any,
        request: AuditRequest,
        engagement_id: str,
        task_id: str,
        population: BoundPopulation,
    ) -> dict[str, Any]:
        packages = AgentRegistry(self.repository_root / "agents").load()
        package = packages[AGENT_ROLE]

        signing_key = Ed25519PrivateKey.generate()
        public_pem = signing_key.public_key().public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        recorder = GovernanceRecorder(self.database)
        issuer = AgentIdentityIssuer(private_key=signing_key, key_id="assuranceos-evaluator-audit")
        verifier = AgentIdentityVerifier(
            {"assuranceos-evaluator-audit": public_pem},
            revocations=DatabaseRevocationChecker(recorder, workspace.tenant_id),
        )
        armor = build_model_armor()
        gateway = AgentGateway(identity_verifier=verifier, armor=armor)

        def provide(test_id: str) -> BoundPopulation | None:
            # The task owns its population. A model asking for a different test
            # gets nothing, rather than the corpus of a company it is not
            # auditing -- which is the specific way a bound population leaks.
            return population if test_id.upper() == population.test_id else None

        context = DomainToolContext(
            database=self.database,
            repository_root=self.repository_root,
            vault=self.sandbox.vault,
            control_tests=build_control_test_service(self.database, self.repository_root),
            population_provider=provide,
        )
        bound = register_domain_tools(gateway, package=package, context=context)

        envelope = ExecutionEnvelope(
            task_id=task_id,
            engagement_id=engagement_id,
            tenant_id=workspace.tenant_id,
            agent_role=AGENT_ROLE,
            agent_version=str(package.manifest["version"]),
            purpose=(
                f"operating effectiveness of the reviewed change path on "
                f"{request.repository} over {_iso(request.period_start)} to "
                f"{_iso(request.period_end)}"
            ),
            allowed_evidence_scopes=["engagement", "tenant"],
            allowed_tools=list(GRANTED_TOOLS),
            forbidden_actions=list((package.policy or {}).get("forbidden_actions", [])),
            model_policy="flash",
        )

        instruction, quality = self._brief(workspace.tenant_id, engagement_id, task_id, request)
        tracer = AgentTracer(TelemetryConfig(environment="evaluator"))
        runtime = GovernedAgentRuntime(
            gateway=gateway,
            identity_issuer=issuer,
            model_client=self.model_client or _ScriptedAuditClient(),
            armor=armor,
            telemetry=TelemetryConfig(environment="evaluator"),
            max_tool_rounds=self.max_tool_rounds,
            # A reasoning model spends part of this budget thinking before it
            # writes anything, so the default that suits a short scripted reply
            # leaves a live model without room to finish its own JSON. The
            # symptom is a truncated conclusion sitting on top of a perfectly
            # good signed run.
            max_output_tokens=8192,
        )
        result = runtime.run(
            package=package,
            envelope=envelope,
            instruction=instruction,
            evidence=[],
            quality=quality,
            tracer=tracer,
        )
        recorder.record_chain(
            tracer.chain,
            tenant_id=workspace.tenant_id,
            engagement_id=engagement_id,
            task_id=task_id,
            agent_role=AGENT_ROLE,
        )
        boundary = self._probe_boundary(gateway, issuer, package, envelope, tracer)
        recorder.record_decisions(
            gateway.decisions, audit_events=gateway.audit_events, engagement_id=engagement_id
        )

        executed = [item for item in result.observations if item["outcome"] == "allowed"]
        test_run = next(
            (item["result"] for item in executed if item["tool"] == "tests.execute"), {}
        )
        # What the report says about the run is read back from the run itself,
        # not from the copy that went through the model. Those differ by design
        # -- the model-bound copy carries a digest prefix and a sample of the
        # exceptions -- and a report assembled from it would quietly inherit
        # both bounds.
        canonical = self._canonical_run(
            context.control_tests, workspace.tenant_id, test_run.get("run_id")
        )
        finding = self._propose_finding(
            tenant_id=workspace.tenant_id,
            engagement_id=engagement_id,
            request=request,
            test_run=canonical,
            result=result,
        )
        return {
            "control_test": {
                "test_id": canonical.get("test_id"),
                "version": canonical.get("version"),
                "run_id": canonical.get("run_id"),
                "status": canonical.get("status"),
                "conclusion": canonical.get("conclusion"),
                "population_count": canonical.get("population_count"),
                "population_complete": canonical.get("population_complete"),
                "exception_count": canonical.get("exception_count"),
                "result_manifest_hash": canonical.get("result_manifest_hash"),
                "limitations": canonical.get("limitations") or [],
                "exceptions": (canonical.get("exceptions") or [])[:20],
            },
            "agent": {
                "status": result.status,
                "model": result.model_name,
                "agent_role": AGENT_ROLE,
                "tools_bound": bound,
                "tools_granted": list(GRANTED_TOOLS),
                "tool_rounds": result.tool_rounds,
                "tool_calls_allowed": result.tool_calls,
                "denials": result.denials,
                "conclusion": (result.output or {}).get("conclusion"),
                "summary": (result.output or {}).get("summary") or result.summary,
                "boundary_probe": boundary,
                "trace_id": result.trace_id,
            },
            "finding": finding,
        }

    @staticmethod
    def _canonical_run(service: Any, tenant_id: str, run_id: str | None) -> dict[str, Any]:
        """Read the signed run back from the record it was written to.

        Returns an empty mapping when the agent never got a run, which is the
        honest shape: the report then says nothing about a population instead of
        reporting a partially-filled one.
        """

        if not run_id:
            return {}
        try:
            run = service.get_run(tenant_id, run_id)
        except Exception:
            return {}
        payload = run.model_dump(mode="json") if hasattr(run, "model_dump") else dict(run)
        payload.setdefault("version", payload.get("version") or payload.get("test_version"))
        return payload

    def _brief(
        self, tenant_id: str, engagement_id: str, task_id: str, request: AuditRequest
    ) -> tuple[str, QualityContext]:
        with self.database.read_session() as session:
            task = session.get(EngagementTask, task_id)
            engagement = session.get(Engagement, engagement_id)
            policy = dict(task.execution_policy_json or {}) if task is not None else {}
            view = {
                "code": engagement.code if engagement else None,
                "title": engagement.title if engagement else None,
                "period": (
                    (engagement.period_start, engagement.period_end)
                    if engagement
                    else (request.period_start, request.period_end)
                ),
            }
        brief = brief_from_lease(
            SimpleNamespace(
                task_key="test-reviewed-change-path",
                task_type="agent",
                assigned_agent_role=AGENT_ROLE,
                engagement_id=engagement_id,
                execution_policy=policy,
                human_gate=None,
            ),
            organization=organization_brief(self.database, tenant_id),
            engagement=view,
        )
        return render_briefing(brief), QualityContext(
            required_control_test=str(policy.get("control_test") or "") or None,
            quality_rules=tuple(str(item) for item in (policy.get("quality_rules") or ())),
        )

    @staticmethod
    def _probe_boundary(gateway, issuer, package, envelope, tracer) -> dict[str, Any]:
        """Ask, under the agent's own identity, for a tool it was never granted.

        A competent model does not overreach, so a boundary that is only
        demonstrated when the model misbehaves is not demonstrated at all.
        """

        identity = issuer.issue(package, envelope)
        try:
            gateway.invoke(
                signed_identity=identity,
                envelope=envelope,
                package=package,
                tool_name="connector.write",
                arguments={"target": "github", "body": "mark the control effective"},
                tracer=tracer,
            )
        except GatewayDenied as denied:
            return {
                "tool": "connector.write",
                "denied": True,
                "stage": denied.decision.stage,
                "reason": denied.decision.reason,
            }
        return {"tool": "connector.write", "denied": False, "reason": "the call was allowed"}

    def _propose_finding(
        self,
        *,
        tenant_id: str,
        engagement_id: str,
        request: AuditRequest,
        test_run: dict[str, Any],
        result: Any,
    ) -> dict[str, Any] | None:
        """Write the finding down, at proposed, or explain why there is none.

        A control that operated effectively produces no finding, and saying so
        is a result rather than an absence. A run that could not establish the
        review path for part of the population produces no finding either, and
        the reason is different: there is nothing to assert yet.
        """

        conclusion = str(test_run.get("conclusion") or "")
        exceptions = test_run.get("exceptions") or []
        if conclusion == "effective":
            return {
                "proposed": False,
                "reason": (
                    "the signed procedure found no exception in the tested population, "
                    "so there is nothing to report"
                ),
            }
        if conclusion != "ineffective" or not exceptions:
            return {
                "proposed": False,
                "reason": (
                    f"the signed procedure concluded {conclusion or 'nothing'}; a finding "
                    "is proposed only on a tested exception, never on missing evidence"
                ),
            }

        from .adjudication import AdjudicationService, SkepticReviewer
        from .adjudication.service import finding_from_exceptions

        evidence_ids = sorted(
            {
                str(value)
                for item in exceptions
                for value in (item.get("evidence_ids") or [])
                if value
            }
        )[:25]
        if not evidence_ids:
            # The type refuses an uncited finding, and rightly. Reaching here
            # means the projection lost the per-row evidence identifiers, which
            # is worth saying out loud rather than surfacing as a validation
            # error from three layers down.
            return {
                "proposed": False,
                "reason": "the tested exceptions carry no evidence identifiers to cite",
            }

        limitations = [str(item) for item in (test_run.get("limitations") or [])]
        summary = (result.output or {}).get("summary") or ""
        population = int(test_run.get("population_count") or 0)
        count = int(test_run.get("exception_count") or 0)
        proposed = finding_from_exceptions(
            code="SCM-02",
            title=f"Changes reached {request.repository} without a reviewed pull request",
            severity="high" if count > 1 else "medium",
            criteria=(
                "SCM-02 requires every change reaching the default branch in period to "
                f"arrive through a merged pull request carrying {request.required_approvals} "
                "approval(s)."
            ),
            # The model contributes the risk statement, which is judgment. The
            # count and the population come from the signed run and are not
            # narrated by anything.
            risk_statement=(
                summary
                or "Unreviewed changes can reach the default branch without a second person seeing them."
            ),
            exceptions=exceptions,
            evidence_ids=evidence_ids,
            source_run_id=str(test_run.get("run_id") or "") or None,
            period=(request.period_start, request.period_end),
            limitations=limitations,
        )
        skeptic = SkepticReviewer(
            period_start=request.period_start,
            period_end=request.period_end,
        )
        service = AdjudicationService(self.database)
        try:
            finding_id, verdict = service.propose(
                tenant_id=tenant_id,
                engagement_id=engagement_id,
                finding=proposed,
                authored_by=f"agent:{AGENT_ROLE}",
                skeptic=skeptic,
                exception_rows=exceptions,
            )
        except Exception as exc:  # a finding that will not write must not hide the audit
            return {"proposed": False, "reason": f"the finding could not be recorded: {exc}"}
        return {
            "proposed": True,
            "finding_id": finding_id,
            "code": proposed.code,
            "title": proposed.title,
            "severity": proposed.severity,
            "status": "proposed",
            "population_tested": population,
            "exception_count": count,
            "limitations": limitations,
            "skeptic": {
                "supported": bool(getattr(verdict, "supported", True)),
                "contradictions": [
                    item.model_dump(mode="json") if hasattr(item, "model_dump") else dict(item)
                    for item in (getattr(verdict, "contradictions", []) or [])
                ],
            },
            "awaiting": "a human decision; nothing here is accepted automatically",
        }


def default_period(days: int = 30, today: date | None = None) -> tuple[date, date]:
    end = today or datetime.now(timezone.utc).date()
    return end - timedelta(days=days), end
