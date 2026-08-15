"""Real handlers for the domain tools the signed packages declare.

A declared tool with no bound handler is denied at routing, which is the correct
default and was also, until now, the whole story: two demonstrations bound one
stub each and every other tool in the fleet was unreachable. An agent that cannot
read evidence, run the signed test, or look at the population it is concluding
about is not doing audit work; it is describing it.

These handlers do the work against canonical state. Each one:

* reads or writes through the same service the product's own routes use, so a
  tool call and a UI action cannot diverge;
* is scoped by the execution envelope rather than by its arguments -- an agent
  asking for another engagement's evidence is refused here, not trusted;
* returns structured data, never prose, so the model receives facts it has to
  cite rather than a narrative it can paraphrase;
* records custody when it reads bytes, because an agent reading a document is an
  access event like any other.

Nothing here grants authority. ``finding.propose`` proposes; the human gate that
follows is unchanged, and the adjudication service still refuses a conclusion
whose evidence does not resolve.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from sqlalchemy import select

from ..db.models import (
    Control,
    ControlTestException,
    ControlTestRun,
    Criterion,
    Engagement,
    EngagementTask,
    EvidenceRecord,
    OrganizationFact,
    OrganizationProfile,
    Risk,
)
from ..db.session import Database
from ..models import ExecutionEnvelope
from .gateway import BoundedTool
from .identity import AgentIdentity

# The corpus dataset builders keyed by the signed test they populate. A tool that
# executes a released test must supply the population that test declares, and the
# agent does not get to choose it: picking your own population is how a control
# test becomes a hand-picked trio that can only produce the wanted answer.
_TEST_DATASETS: dict[str, str] = {
    "SCM-01": "scm_datasets",
    "IAM-01": "iam_datasets",
    "SLA-01": "sla_datasets",
}


class DomainToolError(RuntimeError):
    """A tool refused its arguments. The gateway records this as a denial."""


@dataclass
class DomainToolContext:
    """Everything the handlers need, resolved once per task rather than per call."""

    database: Database
    repository_root: Path
    vault: Any = None
    control_tests: Any = None
    adjudication: Any = None
    # Evidence content is the largest thing a tool can return and the most likely
    # to carry an injection. It is screened on the way out by the gateway; this
    # bound exists so a single call cannot spend the whole model context on one
    # document without the caller having asked for it.
    max_content_bytes: int = 2_000_000

    def corpus(self) -> Any:
        from ..corpus import AsteriaCorpus

        return AsteriaCorpus(self.repository_root / "demo/asteria")


# -- scope enforcement --------------------------------------------------------


def _engagement_scope(envelope: ExecutionEnvelope, requested: str | None) -> str | None:
    """Resolve which engagement a call may read.

    ``engagement`` scope pins the answer to the envelope's own engagement. A
    request naming a different one is refused rather than silently narrowed,
    because narrowing would return a short answer to a question the agent thinks
    it asked broadly.
    """
    scopes = set(envelope.allowed_evidence_scopes)
    if requested and requested != envelope.engagement_id:
        if "tenant" not in scopes:
            raise DomainToolError(
                f"engagement {requested!r} is outside the evidence scope granted to this task"
            )
        return requested
    if "engagement" in scopes or not scopes:
        return envelope.engagement_id
    if "tenant" in scopes:
        return requested
    raise DomainToolError(f"evidence scopes {sorted(scopes)} do not permit reading evidence")


def _period(envelope: ExecutionEnvelope, arguments: Mapping[str, Any]) -> tuple[date, date]:
    from ..corpus import PERIOD_END, PERIOD_START

    def parse(value: Any, fallback: date) -> date:
        if value in (None, ""):
            return fallback
        try:
            return date.fromisoformat(str(value))
        except ValueError as exc:
            raise DomainToolError(f"invalid date {value!r}") from exc

    start = parse(arguments.get("period_start"), PERIOD_START)
    end = parse(arguments.get("period_end"), PERIOD_END)
    if end < start:
        raise DomainToolError("period_end must not precede period_start")
    return start, end


# -- handlers -----------------------------------------------------------------


def _evidence_query(context: DomainToolContext):
    def handler(*, arguments: Mapping[str, Any], identity: AgentIdentity, envelope: ExecutionEnvelope):
        engagement_id = _engagement_scope(envelope, _text(arguments.get("engagement_id")))
        source_type = _text(arguments.get("source_type"))
        query = (_text(arguments.get("query")) or _text(arguments.get("locator")) or "").lower()
        include_content = bool(arguments.get("include_content"))
        limit = _bounded_int(arguments.get("limit"), default=50, ceiling=500)

        with context.database.read_session() as session:
            statement = select(EvidenceRecord).where(
                EvidenceRecord.tenant_id == envelope.tenant_id,
                EvidenceRecord.deleted_at.is_(None),
            )
            if engagement_id:
                statement = statement.where(EvidenceRecord.engagement_id == engagement_id)
            if source_type:
                statement = statement.where(EvidenceRecord.source_type == source_type)
            records = list(session.scalars(statement.limit(limit)))

        if query:
            records = [
                record
                for record in records
                if query in (record.source_locator or "").lower()
                or query in (record.evidence_id or "").lower()
            ] or records

        items: list[dict[str, Any]] = []
        budget = context.max_content_bytes
        for record in records:
            item = {
                "evidence_id": record.evidence_id,
                "source_type": record.source_type,
                "source_locator": record.source_locator,
                "content_sha256": record.content_sha256,
                "classification": record.classification,
                "record_kind": record.record_kind,
                "integrity_status": record.integrity_status,
                "tainted": bool(record.tainted),
                "accepted": bool(record.accepted),
                "collected_at": record.collected_at.isoformat() if record.collected_at else None,
                "size_bytes": record.size_bytes,
            }
            if include_content and context.vault is not None:
                # Reading the bytes is an access event. It is recorded against the
                # agent's own identity, not the operator's, so an evidence file
                # opened by a model is attributable to the model.
                if (record.size_bytes or 0) > budget:
                    item["content_omitted"] = (
                        f"{record.size_bytes} bytes exceeds the remaining content budget; "
                        "request this evidence_id on its own"
                    )
                else:
                    try:
                        payload = context.vault.read_bytes(
                            envelope.tenant_id,
                            record.evidence_id,
                            actor_id=identity.workload_uri,
                            actor_type="agent",
                            purpose=envelope.purpose,
                        )
                    except Exception as exc:  # object missing, purged, or on hold
                        item["content_error"] = f"{type(exc).__name__}: {exc}"
                    else:
                        budget -= len(payload)
                        item["content"] = payload.decode("utf-8", errors="replace")
            items.append(item)
        return {
            "engagement_id": engagement_id,
            "count": len(items),
            "content_included": include_content,
            "evidence": items,
        }

    return handler


def _evidence_hash_verify(context: DomainToolContext):
    def handler(*, arguments: Mapping[str, Any], identity: AgentIdentity, envelope: ExecutionEnvelope):
        if context.vault is None:
            raise DomainToolError("no evidence vault is bound to this task")
        evidence_id = _text(arguments.get("evidence_id"))
        if not evidence_id:
            raise DomainToolError("evidence_id is required")
        result = context.vault.verify_integrity(envelope.tenant_id, evidence_id)
        return {
            "evidence_id": evidence_id,
            "status": getattr(result, "status", str(result)),
            "expected_sha256": getattr(result, "expected_sha256", None),
            "observed_sha256": getattr(result, "observed_sha256", None),
        }

    return handler


def _engagement_read(context: DomainToolContext):
    def handler(*, arguments: Mapping[str, Any], identity: AgentIdentity, envelope: ExecutionEnvelope):
        engagement_id = _engagement_scope(envelope, _text(arguments.get("engagement_id")))
        with context.database.read_session() as session:
            engagement = session.scalar(
                select(Engagement).where(
                    Engagement.tenant_id == envelope.tenant_id,
                    Engagement.engagement_id == engagement_id,
                )
            )
            if engagement is None:
                raise DomainToolError(f"engagement not found: {engagement_id}")
            tasks = list(
                session.scalars(
                    select(EngagementTask).where(
                        EngagementTask.tenant_id == envelope.tenant_id,
                        EngagementTask.engagement_id == engagement_id,
                    )
                )
            )
            return {
                "engagement_id": engagement.engagement_id,
                "code": engagement.code,
                "title": engagement.title,
                "status": engagement.status,
                "audit_pack_ref": engagement.audit_pack_ref,
                "period_start": engagement.period_start.isoformat()
                if engagement.period_start
                else None,
                "period_end": engagement.period_end.isoformat() if engagement.period_end else None,
                "tasks": [
                    {
                        "task_id": task.task_id,
                        "task_key": task.task_key,
                        "status": task.status,
                        "agent_role": task.agent_role,
                        "human_gate": task.human_gate,
                    }
                    for task in tasks
                ],
            }

    return handler


def _test_registry_read(context: DomainToolContext):
    def handler(*, arguments: Mapping[str, Any], identity: AgentIdentity, envelope: ExecutionEnvelope):
        if context.control_tests is None:
            raise DomainToolError("no control-test registry is bound to this task")
        releases = context.control_tests.list_releases()
        return {
            "count": len(releases),
            "releases": [
                {
                    key: release.get(key)
                    for key in ("test_id", "version", "title", "domain", "status", "package_sha256")
                    if key in release
                }
                for release in releases
            ],
        }

    return handler


def _tests_execute(context: DomainToolContext):
    def handler(*, arguments: Mapping[str, Any], identity: AgentIdentity, envelope: ExecutionEnvelope):
        if context.control_tests is None:
            raise DomainToolError("no control-test registry is bound to this task")
        from ..control_testing.definitions import ControlTestRunRequest

        test_id = _text(arguments.get("test_id")).upper()
        if test_id not in _TEST_DATASETS:
            raise DomainToolError(
                f"no corpus population is bound to test {test_id!r}; "
                f"available: {', '.join(sorted(_TEST_DATASETS))}"
            )
        version = _text(arguments.get("version"))
        if not version:
            candidates = [
                release
                for release in context.control_tests.list_releases()
                if release.get("test_id") == test_id
            ]
            if not candidates:
                raise DomainToolError(f"no released version of {test_id}")
            version = str(sorted(candidates, key=lambda item: str(item.get("version")))[-1]["version"])
        period_start, period_end = _period(envelope, arguments)

        corpus = context.corpus()
        datasets = getattr(corpus, _TEST_DATASETS[test_id])()
        population = datasets[0]
        for item in datasets:
            if item.name in {"pull_requests", "terminated_users", "incidents"}:
                population = item
                break

        # The idempotency key is derived from the task, not supplied. A model that
        # can choose the key can run the same test twice and get two answers.
        request = ControlTestRunRequest(
            test_id=test_id,
            version=version,
            purpose=envelope.purpose,
            period_start=period_start,
            period_end=period_end,
            requested_by=identity.workload_uri,
            idempotency_key=f"agent:{envelope.task_id}:{test_id}:{version}",
            engagement_id=envelope.engagement_id,
            parameters={
                "expected_population_count": len(population.records),
                **({"required_approvals": 1} if test_id == "SCM-01" else {}),
            },
            datasets=datasets,
        )
        result = context.control_tests.run(envelope.tenant_id, request)
        payload = result.model_dump(mode="json") if hasattr(result, "model_dump") else dict(result)
        exceptions = payload.get("exceptions") or []
        return {
            "run_id": payload.get("run_id"),
            "test_id": test_id,
            "version": version,
            "status": payload.get("status"),
            "conclusion": payload.get("conclusion"),
            "population_count": payload.get("population_count"),
            "population_complete": payload.get("population_complete"),
            "reconciled_count": payload.get("reconciled_count"),
            "exception_count": payload.get("exception_count"),
            "result_manifest_hash": payload.get("result_manifest_hash"),
            "exceptions": exceptions[:50],
        }

    return handler


def _population_reconcile(context: DomainToolContext):
    def handler(*, arguments: Mapping[str, Any], identity: AgentIdentity, envelope: ExecutionEnvelope):
        run_id = _text(arguments.get("run_id"))
        test_id = _text(arguments.get("test_id")).upper()
        with context.database.read_session() as session:
            statement = select(ControlTestRun).where(
                ControlTestRun.tenant_id == envelope.tenant_id
            )
            if run_id:
                statement = statement.where(ControlTestRun.run_id == run_id)
            if test_id:
                statement = statement.where(ControlTestRun.test_id == test_id)
            runs = list(session.scalars(statement.order_by(ControlTestRun.created_at.desc())))
            if not runs:
                raise DomainToolError("no control-test run matches the request")
            run = runs[0]
            return {
                "run_id": run.run_id,
                "test_id": run.test_id,
                "test_version": run.test_version,
                "status": run.status,
                "conclusion": run.conclusion,
                "population_count": run.population_count,
                "reconciled_count": run.reconciled_count,
                "sampled_count": run.sampled_count,
                "exception_count": run.exception_count,
                "population_complete": bool(run.population_complete),
                "input_manifest_hash": run.input_manifest_hash,
                "result_manifest_hash": run.result_manifest_hash,
                "period": [
                    run.period_start.isoformat() if run.period_start else None,
                    run.period_end.isoformat() if run.period_end else None,
                ],
            }

    return handler


def _exceptions_classify(context: DomainToolContext):
    def handler(*, arguments: Mapping[str, Any], identity: AgentIdentity, envelope: ExecutionEnvelope):
        run_id = _text(arguments.get("run_id"))
        with context.database.read_session() as session:
            statement = select(ControlTestException).where(
                ControlTestException.tenant_id == envelope.tenant_id
            )
            if run_id:
                statement = statement.where(ControlTestException.run_id == run_id)
            rows = list(session.scalars(statement.limit(200)))
            return {
                "run_id": run_id or None,
                "count": len(rows),
                "exceptions": [
                    {
                        "exception_key": row.exception_key,
                        "subject_ref": row.subject_ref,
                        "classification": row.classification,
                        "severity": row.severity,
                        "status": row.status,
                        "reason": row.reason,
                        "evidence_ids": row.evidence_ids_json or [],
                    }
                    for row in rows
                ],
            }

    return handler


def _criteria_query(context: DomainToolContext):
    def handler(*, arguments: Mapping[str, Any], identity: AgentIdentity, envelope: ExecutionEnvelope):
        text = _text(arguments.get("query")).lower()
        with context.database.read_session() as session:
            rows = list(session.scalars(select(Criterion).limit(400)))
        matched = [
            row
            for row in rows
            if not text or text in (row.code or "").lower() or text in (row.text or "").lower()
        ]
        return {
            "count": len(matched),
            "criteria": [
                {
                    "criterion_id": row.criterion_id,
                    "standard_id": row.standard_id,
                    "code": row.code,
                    "text": row.text,
                    "citation": row.citation,
                    "strength": row.strength,
                }
                for row in matched[:50]
            ],
        }

    return handler


def _controls_read(context: DomainToolContext):
    def handler(*, arguments: Mapping[str, Any], identity: AgentIdentity, envelope: ExecutionEnvelope):
        code = _text(arguments.get("code")).upper()
        with context.database.read_session() as session:
            statement = select(Control).where(Control.tenant_id == envelope.tenant_id)
            if code:
                statement = statement.where(Control.code == code)
            rows = list(session.scalars(statement.limit(200)))
        return {
            "count": len(rows),
            "controls": [
                {
                    "control_id": row.control_id,
                    "code": row.code,
                    "title": row.title,
                    "description": row.description,
                    "owner_ref": row.owner_ref,
                    "frequency": row.frequency,
                    "status": row.status,
                }
                for row in rows
            ],
        }

    return handler


def _risks_read(context: DomainToolContext):
    def handler(*, arguments: Mapping[str, Any], identity: AgentIdentity, envelope: ExecutionEnvelope):
        with context.database.read_session() as session:
            rows = list(
                session.scalars(
                    select(Risk).where(Risk.tenant_id == envelope.tenant_id).limit(200)
                )
            )
        return {
            "count": len(rows),
            "risks": [
                {
                    "risk_id": row.risk_id,
                    "code": row.code,
                    "title": row.title,
                    "status": row.status,
                    "residual_risk": row.residual_risk,
                    "confidence": row.confidence,
                    "control_maturity": row.control_maturity,
                }
                for row in rows
            ],
        }

    return handler


def _organization_context_read(context: DomainToolContext):
    """What the platform knows about the company, with each claim's provenance.

    The profile header alone — name, domain, country, industry — is not enough to
    adapt an audit to a company. What changes the work is the rest: whether it
    processes personal data, where it operates, what it has publicly committed to,
    and which of those the platform *inferred* rather than observed. Returning
    facts without their claim type would let a model cite an inference as though
    the company had asserted it, which is the one thing the whole taxonomy exists
    to prevent, so the class travels with every value.
    """

    def handler(*, arguments: Mapping[str, Any], identity: AgentIdentity, envelope: ExecutionEnvelope):
        with context.database.read_session() as session:
            profile = session.scalar(
                select(OrganizationProfile)
                .where(OrganizationProfile.tenant_id == envelope.tenant_id)
                .order_by(OrganizationProfile.version.desc())
            )
            if profile is None:
                return {
                    "profile": None,
                    "reason": "no approved organization profile in this tenant",
                }
            facts = list(
                session.scalars(
                    select(OrganizationFact).where(
                        OrganizationFact.tenant_id == envelope.tenant_id,
                        OrganizationFact.profile_id == profile.profile_id,
                    )
                )
            )
        return {
            "profile": {
                "profile_id": profile.profile_id,
                "version": profile.version,
                "status": profile.status,
                "legal_name": profile.legal_name,
                "primary_domain": profile.primary_domain,
                "headquarters_country": profile.headquarters_country,
                "industry": profile.industry,
            },
            "facts": [
                {
                    "key": fact.fact_key,
                    "value": fact.value_json,
                    "claim_type": fact.claim_type,
                    "source_type": fact.source_type,
                    "confidence": fact.confidence,
                }
                for fact in facts
                if fact.status == "accepted"
            ],
            # A fact a human overruled is more informative than one nobody
            # questioned: it is the platform being wrong, on the record.
            "overruled": [
                {"key": fact.fact_key, "proposed_value": fact.value_json}
                for fact in facts
                if fact.status == "corrected"
            ],
            "claim_type_note": (
                "'observed' was read from a source; 'assertion' was stated by the "
                "company; 'inference' is this platform's reading and is not "
                "evidence on its own."
            ),
        }

    return handler


def _contradictions_search(context: DomainToolContext):
    """Find evidence that disagrees with a stated claim.

    Contradiction search is the reason the skeptic exists, and it cannot be a
    model judgement about its own conclusion. This returns the records whose
    content mentions the subject so the model has to read them; deciding whether
    they contradict remains the model's proposal and the human's decision.
    """

    def handler(*, arguments: Mapping[str, Any], identity: AgentIdentity, envelope: ExecutionEnvelope):
        subject = _text(arguments.get("subject")) or _text(arguments.get("query"))
        if not subject:
            raise DomainToolError("subject is required")
        engagement_id = _engagement_scope(envelope, _text(arguments.get("engagement_id")))
        needle = subject.lower()
        matches: list[dict[str, Any]] = []
        with context.database.read_session() as session:
            statement = select(EvidenceRecord).where(
                EvidenceRecord.tenant_id == envelope.tenant_id,
                EvidenceRecord.deleted_at.is_(None),
            )
            if engagement_id:
                statement = statement.where(EvidenceRecord.engagement_id == engagement_id)
            records = list(session.scalars(statement.limit(300)))
        for record in records:
            payload = ""
            if context.vault is not None and (record.size_bytes or 0) <= 400_000:
                try:
                    payload = context.vault.read_bytes(
                        envelope.tenant_id,
                        record.evidence_id,
                        actor_id=identity.workload_uri,
                        actor_type="agent",
                        purpose=f"contradiction search: {subject}",
                    ).decode("utf-8", errors="replace")
                except Exception:
                    payload = ""
            haystack = f"{record.source_locator or ''}\n{payload}".lower()
            if needle in haystack:
                index = haystack.find(needle)
                matches.append(
                    {
                        "evidence_id": record.evidence_id,
                        "source_type": record.source_type,
                        "source_locator": record.source_locator,
                        "content_sha256": record.content_sha256,
                        "excerpt": payload[max(0, index - 200) : index + 400] if payload else "",
                    }
                )
        return {"subject": subject, "count": len(matches), "matches": matches[:20]}

    return handler


def _finding_propose(context: DomainToolContext):
    def handler(*, arguments: Mapping[str, Any], identity: AgentIdentity, envelope: ExecutionEnvelope):
        if context.adjudication is None:
            raise DomainToolError("no adjudication service is bound to this task")
        from ..adjudication.definitions import FindingProposal

        proposal = FindingProposal(
            engagement_id=envelope.engagement_id,
            code=_required(arguments, "code"),
            title=_required(arguments, "title"),
            observed_condition=_required(arguments, "observed_condition"),
            criteria_ref=_required(arguments, "criteria_ref"),
            cause=_text(arguments.get("cause")) or "not yet determined",
            effect=_text(arguments.get("effect")) or "not yet quantified",
            severity=_text(arguments.get("severity")) or "medium",
            evidence_ids=[str(item) for item in (arguments.get("evidence_ids") or [])],
            proposed_by=identity.workload_uri,
        )
        finding_id = context.adjudication.propose(tenant_id=envelope.tenant_id, proposal=proposal)
        return {
            "finding_id": finding_id,
            "status": "proposed",
            "note": "a proposal is not a finding; approval remains a human decision",
        }

    return handler


# -- assembly -----------------------------------------------------------------

# What each tool actually accepts. The signed packages declare a tool's name,
# side effect and network route -- the things policy needs -- but not its
# arguments, and a model given a bare name invents them: measured against
# gemma-4-12b, `tests.execute` was called with no test_id at all and
# `population.reconcile` before anything had been run. A tool whose contract the
# caller cannot read is not a usable tool, so the contract is published here,
# beside the handler that enforces it, and rendered into the prompt.
TOOL_CONTRACTS: dict[str, str] = {
    "evidence.query": (
        '{"source_type": "github|jira|confluence|hr|identity|legal|cloud|finance", '
        '"query": "substring of the locator", "include_content": true, "limit": 50} '
        "- all optional; returns evidence ids, digests and, with include_content, "
        "the documents themselves"
    ),
    "evidence.hash.verify": '{"evidence_id": "ev_..."} - required',
    "engagement.read": "{} - the engagement this task belongs to, with its tasks",
    "engagement.graph.read": "{} - the engagement this task belongs to, with its tasks",
    "test_registry.read": "{} - the signed control tests available to execute",
    "tests.execute": (
        '{"test_id": "SCM-01|IAM-01|SLA-01"} - REQUIRED. Runs the signed release '
        "over the complete declared population and returns the population count, "
        "completeness, exceptions and the result manifest digest"
    ),
    "population.reconcile": (
        '{"test_id": "SCM-01"} or {"run_id": "..."} - reads back a run that has '
        "already been executed; execute the test first"
    ),
    "exceptions.classify": (
        '{"run_id": "the run_id returned by tests.execute"} - the exception records '
        "of that run, with their reasons"
    ),
    "criteria.query": '{"query": "text or code to match"} - optional',
    "criteria.applicability.query": '{"query": "text or code to match"} - optional',
    "controls.read": '{"code": "SCM-01"} - optional; omit for every control',
    "risks.read": "{} - the tenant risk register",
    "risk.universe.read": "{} - the tenant risk register",
    "organization.context.read": "{} - the approved organization profile",
    "contradictions.search": (
        '{"subject": "what the claim asserts"} - REQUIRED; returns evidence '
        "mentioning it, with excerpts"
    ),
    "evidence.contradictions.query": '{"subject": "what the claim asserts"} - REQUIRED',
    "finding.propose": (
        '{"code": "...", "title": "...", "observed_condition": "...", '
        '"criteria_ref": "...", "severity": "low|medium|high|critical", '
        '"evidence_ids": ["ev_..."]} - proposes only; a human approves'
    ),
}


def tool_contract(name: str) -> str:
    """The argument contract for a tool, or a note that none is published."""

    return TOOL_CONTRACTS.get(name, "{} - no published argument contract")


_FACTORIES: dict[str, Callable[[DomainToolContext], Any]] = {
    "evidence.query": _evidence_query,
    "evidence.hash.verify": _evidence_hash_verify,
    "engagement.read": _engagement_read,
    "engagement.graph.read": _engagement_read,
    "test_registry.read": _test_registry_read,
    "tests.execute": _tests_execute,
    "population.reconcile": _population_reconcile,
    "exceptions.classify": _exceptions_classify,
    "criteria.query": _criteria_query,
    "criteria.applicability.query": _criteria_query,
    "controls.read": _controls_read,
    "risks.read": _risks_read,
    "risk.universe.read": _risks_read,
    "organization.context.read": _organization_context_read,
    "contradictions.search": _contradictions_search,
    "evidence.contradictions.query": _contradictions_search,
    "finding.propose": _finding_propose,
}

IMPLEMENTED_TOOLS: frozenset[str] = frozenset(_FACTORIES)


def build_domain_tools(context: DomainToolContext) -> dict[str, BoundedTool]:
    """Every implemented domain tool, bound to this context."""

    return {
        name: BoundedTool(name, factory(context), description=tool_contract(name))
        for name, factory in _FACTORIES.items()
    }


def register_domain_tools(
    gateway: Any,
    *,
    package: Any,
    context: DomainToolContext,
    tool_names: Sequence[str] | None = None,
) -> list[str]:
    """Bind the intersection of what the package declares and what exists here.

    The intersection matters in both directions. Registering a tool the package
    does not declare would put a handler behind a name the policy gateway will
    refuse anyway; registering one it declares but that has no implementation
    would be worse, because the denial would read as a policy decision when it is
    really a missing feature.
    """
    declared = {
        str(item.get("name"))
        for item in (package.tools or {}).get("tools", [])
        if item.get("name")
    }
    available = build_domain_tools(context)
    selected = declared & set(tool_names or available)
    bound: list[str] = []
    for name in sorted(selected & set(available)):
        if name in set(gateway.registered_tools(package.agent_id)):
            continue
        gateway.register_tool(package.agent_id, available[name])
        bound.append(name)
    return bound


def unimplemented_tools(package: Any) -> list[str]:
    """Declared tools with no handler, so the gap is inspectable rather than felt."""

    declared = {
        str(item.get("name"))
        for item in (package.tools or {}).get("tools", [])
        if item.get("name")
    }
    return sorted(declared - IMPLEMENTED_TOOLS)


# -- argument helpers ---------------------------------------------------------


def _text(value: Any) -> str:
    return str(value).strip() if isinstance(value, (str, int, float)) else ""


def _required(arguments: Mapping[str, Any], key: str) -> str:
    value = _text(arguments.get(key))
    if not value:
        raise DomainToolError(f"{key} is required")
    return value


def _bounded_int(value: Any, *, default: int, ceiling: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return max(1, min(parsed, ceiling))
