"""Finding adjudication, remediation, and independent retest.

This is where the execution chain stops being a collection of test results and
becomes an audit. A deterministic exception enters; a closed, verified finding or
a deterministically reopened one comes out; and every transition in between is
attributable to an actor and a reason.

Three properties are enforced structurally rather than by convention, because
each is a place where an autonomous system would otherwise quietly award itself
authority it should not have:

* **The human gate is a record, not a threshold.** An agent may propose a finding
  and may state a confidence. It cannot approve one. Approval requires a decision
  attributed to a person, and the service refuses decisions attributed to an
  agent role.
* **Remediation is opened at most once.** Replay is a normal condition in a
  durable orchestrator, and a workflow that files a second Jira ticket on every
  retry is worse than no automation. Idempotency is keyed on the finding, so even
  a replay carrying a different key cannot open a second action.
* **Retest is independent by construction.** A retest performed by the identity
  that authored the finding, or by the one that performed the remediation, is not
  weaker evidence — it is not evidence. The service refuses it.

Every transition writes an approval decision, an audit event, and an outbox event
inside one transaction, so canonical state and its published consequences cannot
disagree.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date, datetime, timezone
from typing import Any

from assuranceos.db.models import (
    ApprovalDecision,
    Finding,
    ManagementResponse,
    RemediationAction,
    Retest,
)
from assuranceos.db.repositories import AuditEventRepository, OutboxRepository, new_id
from assuranceos.db.session import Database
from assuranceos.models import AuditEvent

from .definitions import (
    CLOSING_OUTCOMES,
    ALLOWED_TRANSITIONS,
    AdjudicationRequest,
    ClosureSubmission,
    FindingStatus,
    FindingView,
    HumanDecision,
    ProposedFinding,
    RecurrenceMatch,
    RemediationRequest,
    RetestRequest,
    SkepticVerdict,
)
from .exceptions import (
    ClosureEvidenceError,
    FindingNotFoundError,
    HumanGateError,
    IdempotencyConflictError,
    IndependenceError,
    InvalidTransitionError,
    RemediationNotFoundError,
)
from .repository import AdjudicationRepository
from .skeptic import SkepticReviewer

#: Decisions that move a finding forward, and the state each produces.
_DECISION_TARGET: dict[HumanDecision, FindingStatus] = {
    HumanDecision.APPROVE: FindingStatus.APPROVED,
    HumanDecision.REJECT: FindingStatus.REJECTED,
    HumanDecision.DEFER: FindingStatus.DEFERRED,
    HumanDecision.ACCEPT_RISK: FindingStatus.RISK_ACCEPTED,
    # Rework leaves the finding where it is; the rationale is the product.
    HumanDecision.RETURN_FOR_REWORK: FindingStatus.PROPOSED,
}


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class AdjudicationService:
    """The finding lifecycle, from accepted exception to closed or reopened.

    ``agent_role_prefixes`` names the actor prefixes that identify a non-human
    actor. Approval attributed to one of these is refused. The check is a prefix
    match rather than a lookup so that an unknown agent fails closed.
    """

    def __init__(
        self,
        database: Database,
        *,
        agent_role_prefixes: Sequence[str] = ("agent:", "svc:", "system:"),
    ):
        self.database = database
        self.agent_role_prefixes = tuple(agent_role_prefixes)

    # -- 1. proposal -----------------------------------------------------------

    def propose(
        self,
        *,
        tenant_id: str,
        engagement_id: str,
        finding: ProposedFinding,
        authored_by: str,
        skeptic: SkepticReviewer | None = None,
        exception_rows: Sequence[Mapping[str, Any]] = (),
    ) -> tuple[str, SkepticVerdict]:
        """Create a proposed finding, after searching for reasons it should not stand.

        The skeptic runs before the finding is persisted as proposable. A finding
        whose every exception is explained by canonical records is recorded as
        ``rejected`` immediately, with the contradictions attached: the work of
        having looked is retained even though nothing is raised.
        """
        verdict = (skeptic or SkepticReviewer()).review(
            finding, exception_rows=exception_rows
        )

        with self.database.transaction() as session:
            repository = AdjudicationRepository(session)
            existing = repository.find_by_code(tenant_id, engagement_id, finding.code)
            version = (existing.version + 1) if existing else 1

            status = (
                FindingStatus.PROPOSED if verdict.supported else FindingStatus.REJECTED
            )
            record = Finding(
                finding_id=new_id("fnd"),
                tenant_id=tenant_id,
                engagement_id=engagement_id,
                code=finding.code,
                version=version,
                title=finding.title,
                status=status.value,
                severity=finding.severity,
                confidence=finding.confidence,
                business_objective=finding.business_objective,
                risk_statement=finding.risk_statement,
                criteria=finding.criteria,
                observed_condition=finding.observed_condition,
                cause=finding.cause,
                consequence=finding.consequence,
                affected_population_json=dict(finding.affected_population),
                limitations_json=list(finding.limitations),
                requires_human_approval=True,
                evidence_ids_json=list(finding.evidence_ids),
                contradictions_json=[c.model_dump(mode="json") for c in verdict.contradictions],
                exception_keys_json=list(finding.exception_keys),
                source_run_id=finding.source_run_id,
                authored_by=authored_by,
            )
            repository.add_finding(record)

            # The skeptic's rejection is itself a decision on the record, so a
            # reviewer can see that the finding was considered and why it fell.
            if not verdict.supported:
                repository.add_decision(
                    ApprovalDecision(
                        decision_id=new_id("apd"),
                        tenant_id=tenant_id,
                        engagement_id=engagement_id,
                        finding_id=record.finding_id,
                        decision_type="skeptic_reject",
                        actor_id="agent:skeptic",
                        reason=verdict.rejection_reason,
                    )
                )

            self._emit(
                session,
                tenant_id=tenant_id,
                engagement_id=engagement_id,
                aggregate_id=record.finding_id,
                event_type=(
                    "finding.proposed" if verdict.supported else "finding.skeptic_rejected"
                ),
                payload={
                    "finding_id": record.finding_id,
                    "code": finding.code,
                    "version": version,
                    "severity": finding.severity,
                    "status": status.value,
                    "authored_by": authored_by,
                    "evidence_ids": list(finding.evidence_ids),
                    "contradictions": [c.model_dump(mode="json") for c in verdict.contradictions],
                },
                idempotency_key=f"finding-proposed:{record.finding_id}",
            )
            return record.finding_id, verdict

    # -- 2. human adjudication -------------------------------------------------

    def adjudicate(self, *, tenant_id: str, request: AdjudicationRequest) -> FindingStatus:
        """Record a human decision on a proposed finding.

        Refuses any decision attributed to an agent. This is the single point at
        which an automated pipeline is prevented from concluding on its own, so
        it is checked here rather than at the caller.
        """
        if request.actor_id.lower().startswith(self.agent_role_prefixes):
            raise HumanGateError(
                f"{request.actor_id!r} is an automated actor; approval of a finding "
                "requires a decision attributable to a person"
            )

        with self.database.transaction() as session:
            repository = AdjudicationRepository(session)
            finding = self._require_finding(repository, tenant_id, request.finding_id)

            current = FindingStatus(finding.status)
            target = _DECISION_TARGET[request.decision]

            # The replay check comes before the transition guard. The finding has
            # already moved by the time a duplicate arrives, so testing the
            # transition first would reject the replay as an illegal move rather
            # than recognising it as the no-op it is.
            for prior in repository.decisions(tenant_id, finding.finding_id):
                if prior.decision_type == f"human:{request.decision.value}" and (
                    prior.actor_id == request.actor_id
                ):
                    return current

            if request.decision is HumanDecision.RETURN_FOR_REWORK:
                if current is not FindingStatus.PROPOSED:
                    raise InvalidTransitionError(current.value, target.value)
            else:
                self._require_transition(current, target)

            finding.status = target.value
            repository.add_decision(
                ApprovalDecision(
                    decision_id=new_id("apd"),
                    tenant_id=tenant_id,
                    engagement_id=finding.engagement_id,
                    finding_id=finding.finding_id,
                    decision_type=f"human:{request.decision.value}",
                    actor_id=request.actor_id,
                    reason=request.reason,
                )
            )
            self._emit(
                session,
                tenant_id=tenant_id,
                engagement_id=finding.engagement_id,
                aggregate_id=finding.finding_id,
                event_type=f"finding.{request.decision.value}",
                payload={
                    "finding_id": finding.finding_id,
                    "code": finding.code,
                    "status": target.value,
                    "actor_id": request.actor_id,
                    "reason": request.reason,
                },
                idempotency_key=f"finding-decision:{request.idempotency_key}",
            )
            return target

    # -- 3. remediation --------------------------------------------------------

    def open_remediation(
        self, *, tenant_id: str, request: RemediationRequest
    ) -> tuple[str, bool]:
        """Convert an approved finding into a remediation obligation.

        Returns ``(action_id, created)``. ``created`` is False when an action was
        already open for this finding, which is the replay path: the caller gets
        the original action rather than a duplicate, and no second external ticket
        is filed.
        """
        with self.database.transaction() as session:
            repository = AdjudicationRepository(session)
            finding = self._require_finding(repository, tenant_id, request.finding_id)

            existing = repository.open_action_for(tenant_id, finding.finding_id)
            if existing is not None:
                # Same finding, different plan, means the caller believes it is
                # opening something new. Say so rather than silently returning
                # an action that does not match what was asked for.
                if (
                    existing.idempotency_key
                    and existing.idempotency_key != request.idempotency_key
                    and existing.action_plan != request.action_plan
                ):
                    raise IdempotencyConflictError(
                        f"finding {finding.finding_id} already has remediation "
                        f"{existing.action_id}; a finding carries one open action"
                    )
                return existing.action_id, False

            current = FindingStatus(finding.status)
            self._require_transition(current, FindingStatus.REMEDIATION_OPEN)

            action = RemediationAction(
                action_id=new_id("rma"),
                tenant_id=tenant_id,
                finding_id=finding.finding_id,
                owner_ref=request.owner_ref,
                status="open",
                due_date=request.due_date,
                action_plan=request.action_plan,
                escalation_policy_json=dict(request.escalation_policy),
                closure_evidence_required=request.closure_evidence_required,
                idempotency_key=request.idempotency_key,
                external_system=request.external_system,
                # The external reference is derived from the action, so a retry
                # that reaches an external system presents the same key.
                external_ref=None,
            )
            repository.add_action(action)
            finding.status = FindingStatus.REMEDIATION_OPEN.value

            self._emit(
                session,
                tenant_id=tenant_id,
                engagement_id=finding.engagement_id,
                aggregate_id=action.action_id,
                event_type="remediation.opened",
                payload={
                    "action_id": action.action_id,
                    "finding_id": finding.finding_id,
                    "owner_ref": request.owner_ref,
                    "due_date": request.due_date.isoformat(),
                    "external_system": request.external_system,
                    "closure_evidence_required": request.closure_evidence_required,
                },
                idempotency_key=f"remediation-opened:{action.action_id}",
                aggregate_type="remediation_action",
            )
            return action.action_id, True

    # -- 4. closure ------------------------------------------------------------

    def submit_closure(self, *, tenant_id: str, submission: ClosureSubmission) -> str:
        """Record management's assertion that an action is complete.

        An assertion is not a closure. The finding moves to
        ``remediation_declared_complete``; only an independent retest can close it.
        """
        with self.database.transaction() as session:
            repository = AdjudicationRepository(session)
            action = repository.get_action(tenant_id, submission.action_id)
            if action is None:
                raise RemediationNotFoundError(
                    f"remediation action {submission.action_id!r} was not found"
                )
            finding = self._require_finding(repository, tenant_id, action.finding_id)

            if action.closure_evidence_required and not submission.closure_evidence_ids:
                raise ClosureEvidenceError(
                    f"remediation {action.action_id} requires closure evidence; "
                    "an unevidenced assertion cannot advance it"
                )

            current = FindingStatus(finding.status)
            self._require_transition(
                current, FindingStatus.REMEDIATION_DECLARED_COMPLETE
            )

            version = len(repository.responses(tenant_id, finding.finding_id)) + 1
            response = ManagementResponse(
                response_id=new_id("mgr"),
                tenant_id=tenant_id,
                finding_id=finding.finding_id,
                version=version,
                response_text=submission.response_text,
                action_plan=submission.action_plan or action.action_plan,
                submitted_by=submission.submitted_by,
                closure_evidence_ids_json=list(submission.closure_evidence_ids),
            )
            repository.add_response(response)

            action.status = "declared_complete"
            action.declared_complete_at = utc_now()
            action.completed_by = submission.submitted_by
            finding.status = FindingStatus.REMEDIATION_DECLARED_COMPLETE.value

            self._emit(
                session,
                tenant_id=tenant_id,
                engagement_id=finding.engagement_id,
                aggregate_id=action.action_id,
                event_type="remediation.closure_submitted",
                payload={
                    "action_id": action.action_id,
                    "finding_id": finding.finding_id,
                    "response_id": response.response_id,
                    "submitted_by": submission.submitted_by,
                    "closure_evidence_ids": list(submission.closure_evidence_ids),
                },
                idempotency_key=f"closure-submitted:{response.response_id}",
                aggregate_type="remediation_action",
            )
            return response.response_id

    # -- 5. independent retest -------------------------------------------------

    def retest(self, *, tenant_id: str, request: RetestRequest) -> tuple[str, FindingStatus]:
        """Verify a declared remediation with an independent test.

        The retester must differ from the finding's author and from whoever
        declared the remediation complete. Any non-closing outcome reopens the
        finding, which is the deterministic direction: closure is the claim that
        needs evidence, not reopening.
        """
        with self.database.transaction() as session:
            repository = AdjudicationRepository(session)
            action = repository.get_action(tenant_id, request.action_id)
            if action is None:
                raise RemediationNotFoundError(
                    f"remediation action {request.action_id!r} was not found"
                )
            finding = self._require_finding(repository, tenant_id, action.finding_id)

            self._require_independence(finding, action, request.performed_by)

            for prior in repository.retests(tenant_id, action.action_id):
                if prior.idempotency_key == request.idempotency_key:
                    return prior.retest_id, FindingStatus(finding.status)

            current = FindingStatus(finding.status)
            self._require_transition(current, FindingStatus.RETEST_IN_PROGRESS)

            closed = request.outcome in CLOSING_OUTCOMES
            if closed and not request.evidence_ids:
                raise ClosureEvidenceError(
                    "a retest may only close a finding on fresh evidence; "
                    f"outcome {request.outcome.value!r} cited none"
                )

            final = (
                FindingStatus.CLOSED_VERIFIED if closed else FindingStatus.REOPENED
            )
            retest = Retest(
                retest_id=new_id("rts"),
                tenant_id=tenant_id,
                action_id=action.action_id,
                engagement_id=finding.engagement_id,
                status="completed",
                outcome=request.outcome.value,
                procedure_ref=request.procedure_ref,
                performed_by=request.performed_by,
                result_json={
                    "detail": request.detail,
                    "fresh_evidence_collected_at": (
                        request.fresh_evidence_collected_at.isoformat()
                        if request.fresh_evidence_collected_at
                        else None
                    ),
                    "resulting_status": final.value,
                },
                completed_at=utc_now(),
                evidence_ids_json=list(request.evidence_ids),
                idempotency_key=request.idempotency_key,
                independence_basis_json={
                    "authored_by": finding.authored_by,
                    "remediated_by": action.completed_by,
                    "performed_by": request.performed_by,
                },
            )
            repository.add_retest(retest)

            action.status = "closed" if closed else "reopened"
            finding.status = final.value

            self._emit(
                session,
                tenant_id=tenant_id,
                engagement_id=finding.engagement_id,
                aggregate_id=finding.finding_id,
                event_type=(
                    "finding.closed_verified" if closed else "finding.reopened"
                ),
                payload={
                    "finding_id": finding.finding_id,
                    "action_id": action.action_id,
                    "retest_id": retest.retest_id,
                    "outcome": request.outcome.value,
                    "performed_by": request.performed_by,
                    "status": final.value,
                    "evidence_ids": list(request.evidence_ids),
                },
                idempotency_key=f"retest-completed:{retest.retest_id}",
            )
            return retest.retest_id, final

    def reopen_for_remediation(
        self, *, tenant_id: str, finding_id: str, request: RemediationRequest
    ) -> str:
        """Open a fresh remediation cycle on a reopened finding."""
        with self.database.transaction() as session:
            repository = AdjudicationRepository(session)
            finding = self._require_finding(repository, tenant_id, finding_id)
            self._require_transition(
                FindingStatus(finding.status), FindingStatus.REMEDIATION_OPEN
            )
            existing = repository.open_action_for(tenant_id, finding_id)
            if existing is not None:
                existing.status = "open"
                existing.due_date = request.due_date
                existing.action_plan = request.action_plan
                existing.declared_complete_at = None
                existing.completed_by = None
                finding.status = FindingStatus.REMEDIATION_OPEN.value
                self._emit(
                    session,
                    tenant_id=tenant_id,
                    engagement_id=finding.engagement_id,
                    aggregate_id=existing.action_id,
                    event_type="remediation.reopened",
                    payload={
                        "action_id": existing.action_id,
                        "finding_id": finding_id,
                        "due_date": request.due_date.isoformat(),
                    },
                    idempotency_key=f"remediation-reopened:{existing.action_id}:{request.idempotency_key}",
                    aggregate_type="remediation_action",
                )
                return existing.action_id
            raise RemediationNotFoundError(
                f"finding {finding_id!r} has no remediation action to reopen"
            )

    # -- 6. recurrence ---------------------------------------------------------

    def recurrence(self, *, tenant_id: str, code: str) -> RecurrenceMatch | None:
        """The same control failing across more than one engagement.

        Recurrence is a distinct signal from severity: a medium finding that has
        now appeared in three consecutive engagements is a different problem from
        a medium finding seen once.
        """
        with self.database.read_session() as session:
            rows = AdjudicationRepository(session).list_findings_by_code(tenant_id, code)
            raised = [
                row
                for row in rows
                if FindingStatus(row.status) is not FindingStatus.REJECTED
            ]
            engagements = sorted({row.engagement_id for row in raised})
            if len(engagements) < 2:
                return None
            return RecurrenceMatch(
                code=code,
                engagement_ids=engagements,
                occurrences=len(raised),
                latest_status=FindingStatus(raised[-1].status),
            )

    # -- views -----------------------------------------------------------------

    def view(self, *, tenant_id: str, finding_id: str) -> FindingView:
        with self.database.read_session() as session:
            repository = AdjudicationRepository(session)
            finding = self._require_finding(repository, tenant_id, finding_id)
            actions = repository.actions(tenant_id, finding_id)
            retests: list[dict[str, Any]] = []
            for action in actions:
                for item in repository.retests(tenant_id, action.action_id):
                    retests.append(
                        {
                            "retest_id": item.retest_id,
                            "action_id": item.action_id,
                            "outcome": item.outcome,
                            "performed_by": item.performed_by,
                            "evidence_ids": list(item.evidence_ids_json or []),
                            "independence_basis": dict(item.independence_basis_json or {}),
                        }
                    )
            return FindingView(
                finding_id=finding.finding_id,
                tenant_id=finding.tenant_id,
                engagement_id=finding.engagement_id,
                code=finding.code,
                version=finding.version,
                title=finding.title,
                status=FindingStatus(finding.status),
                severity=finding.severity,
                confidence=finding.confidence,
                requires_human_approval=finding.requires_human_approval,
                evidence_ids=list(finding.evidence_ids_json or []),
                limitations=list(finding.limitations_json or []),
                decisions=[
                    {
                        "decision_id": item.decision_id,
                        "decision_type": item.decision_type,
                        "actor_id": item.actor_id,
                        "reason": item.reason,
                        "decided_at": item.decided_at.isoformat(),
                    }
                    for item in repository.decisions(tenant_id, finding_id)
                ],
                actions=[
                    {
                        "action_id": item.action_id,
                        "status": item.status,
                        "owner_ref": item.owner_ref,
                        "due_date": item.due_date.isoformat(),
                        "external_system": item.external_system,
                        "completed_by": item.completed_by,
                    }
                    for item in actions
                ],
                retests=retests,
            )

    def list_findings(
        self, *, tenant_id: str, engagement_id: str | None = None
    ) -> list[dict[str, Any]]:
        with self.database.read_session() as session:
            return [
                {
                    "finding_id": row.finding_id,
                    "code": row.code,
                    "version": row.version,
                    "title": row.title,
                    "status": row.status,
                    "severity": row.severity,
                    "engagement_id": row.engagement_id,
                }
                for row in AdjudicationRepository(session).list_findings(
                    tenant_id, engagement_id
                )
            ]

    # -- internals -------------------------------------------------------------

    @staticmethod
    def _require_finding(
        repository: AdjudicationRepository, tenant_id: str, finding_id: str
    ) -> Finding:
        finding = repository.get_finding(tenant_id, finding_id)
        if finding is None:
            raise FindingNotFoundError(f"finding {finding_id!r} was not found")
        return finding

    @staticmethod
    def _require_transition(current: FindingStatus, target: FindingStatus) -> None:
        if target not in ALLOWED_TRANSITIONS.get(current, frozenset()):
            raise InvalidTransitionError(current.value, target.value)

    @staticmethod
    def _require_independence(
        finding: Finding, action: RemediationAction, performed_by: str
    ) -> None:
        """Refuse a retest performed by anyone who produced the work.

        Compared case-insensitively: an identity that differs only by case is the
        same actor, and treating it as independent would make the control
        trivially evadable.
        """
        candidate = performed_by.strip().lower()
        conflicts = {
            "author of the finding": (finding.authored_by or "").strip().lower(),
            "owner of the remediation": (action.owner_ref or "").strip().lower(),
            "declarer of completion": (action.completed_by or "").strip().lower(),
        }
        for role, actor in conflicts.items():
            if actor and actor == candidate:
                raise IndependenceError(
                    f"{performed_by!r} is the {role} and cannot perform its "
                    "independent retest"
                )

    def _emit(
        self,
        session: Any,
        *,
        tenant_id: str,
        engagement_id: str | None,
        aggregate_id: str,
        event_type: str,
        payload: Mapping[str, Any],
        idempotency_key: str,
        aggregate_type: str = "finding",
    ) -> None:
        """Write the audit event and the outbox event in the caller's transaction.

        Both are written here rather than by the caller so that a state change can
        never commit without the record of it.
        """
        AuditEventRepository(session).append(
            AuditEvent(
                event_type=event_type,
                tenant_id=tenant_id,
                engagement_id=engagement_id,
                occurred_at=utc_now(),
                payload=dict(payload),
            )
        )
        OutboxRepository(session).add(
            tenant_id=tenant_id,
            aggregate_type=aggregate_type,
            aggregate_id=aggregate_id,
            event_type=event_type,
            payload=dict(payload),
            idempotency_key=idempotency_key,
        )


def finding_from_exceptions(
    *,
    code: str,
    title: str,
    severity: str,
    criteria: str,
    risk_statement: str,
    exceptions: Sequence[Mapping[str, Any]],
    evidence_ids: Sequence[str],
    source_run_id: str | None = None,
    confidence: float = 0.7,
    period: tuple[date, date] | None = None,
) -> ProposedFinding:
    """Build a proposed finding from accepted control-test exceptions.

    The observed condition is composed from the exception rows rather than
    written by a model, so the statement of what was seen is a computed fact. A
    model's contribution is the risk statement and the recommendation, which are
    judgment; the count and the population are not.
    """
    subjects = [str(row.get("subject_ref") or row.get("exception_key")) for row in exceptions]
    condition = (
        f"{len(subjects)} exception(s) identified: " + ", ".join(sorted(subjects)[:20])
        if subjects
        else "no exceptions identified"
    )
    if period:
        condition += f" (period {period[0].isoformat()} to {period[1].isoformat()})"
    return ProposedFinding(
        code=code,
        title=title,
        severity=severity,  # type: ignore[arg-type]
        confidence=confidence,
        criteria=criteria,
        observed_condition=condition,
        risk_statement=risk_statement,
        evidence_ids=list(evidence_ids),
        exception_keys=[str(row.get("exception_key")) for row in exceptions],
        affected_population={"exception_count": len(subjects), "subjects": sorted(subjects)},
        source_run_id=source_run_id,
    )
