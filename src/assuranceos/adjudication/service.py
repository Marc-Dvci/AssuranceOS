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
    FindingDispute,
    ManagementResponse,
    MaterialityAssessment,
    QualityReview,
    RemediationAction,
    Retest,
)
from assuranceos.db.repositories import AuditEventRepository, OutboxRepository, new_id
from assuranceos.db.session import Database
from assuranceos.models import AuditEvent

from . import quality
from .definitions import (
    CLOSING_OUTCOMES,
    ALLOWED_TRANSITIONS,
    AdjudicationRequest,
    ClosureSubmission,
    DisputeRequest,
    DisputeResolution,
    DisputeResolutionRequest,
    FindingStatus,
    FindingView,
    HumanDecision,
    MaterialityRequest,
    ProposedFinding,
    QualityReviewRequest,
    RecurrenceMatch,
    RemediationRequest,
    RetestRequest,
    SeverityOverrideRequest,
    SkepticVerdict,
)
from .exceptions import (
    ClosureEvidenceError,
    DisputeError,
    FindingNotFoundError,
    HumanGateError,
    IdempotencyConflictError,
    IndependenceError,
    InvalidTransitionError,
    MaterialityError,
    QualityGateError,
    RemediationNotFoundError,
    TicketingError,
)
from .materiality import (
    MaterialityPolicy,
    assess,
    content_hash,
    severity_rank,
)
from .repository import AdjudicationRepository
from .skeptic import SkepticReviewer
from .ticketing import NullTicketWriter, TicketRequest, TicketWriter

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
        require_quality_review: bool = True,
        max_dispute_rounds: int = 3,
    ):
        self.database = database
        self.agent_role_prefixes = tuple(agent_role_prefixes)
        # Defaults to on. It is exposed so an engagement type that genuinely has
        # no second reviewer can be configured explicitly rather than by an
        # undocumented code path, and so the test that proves the gate bites can
        # also prove the waiver is a deliberate setting.
        self.require_quality_review = require_quality_review
        self.max_dispute_rounds = max_dispute_rounds

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
                # Recorded whether or not anything was found. "We looked and found
                # nothing" and "nobody looked" produce the same empty list, and the
                # quality gate downstream has to be able to tell them apart.
                skeptic_reviewed_at=utc_now(),
                skeptic_rationale=verdict.rationale or None,
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

            # Approval — and only approval — has preconditions beyond the
            # transition. Rejecting, deferring or accepting the risk on an
            # unreviewed finding is legitimate: the reviewer's work is what
            # supports raising something, not what supports dropping it.
            if request.decision is HumanDecision.APPROVE:
                self._require_approval_preconditions(
                    repository, tenant_id, finding, approver_id=request.actor_id
                )

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

    # -- 2a. materiality -------------------------------------------------------

    def assess_materiality(
        self, *, tenant_id: str, request: MaterialityRequest
    ) -> MaterialityAssessment:
        """Score whether a finding matters, from measured inputs under a policy.

        The score may raise the finding's severity to the computed floor, and the
        raise happens here rather than being left to the caller: a materiality
        assessment that concludes ``critical`` and leaves the finding at ``low``
        has documented the disagreement instead of resolving it.

        It never lowers a severity. That direction requires
        :meth:`override_severity`, which takes an actor and a reason.
        """
        policy = request.policy or MaterialityPolicy()
        result = assess(request.inputs, policy)

        with self.database.transaction() as session:
            repository = AdjudicationRepository(session)
            finding = self._require_finding(repository, tenant_id, request.finding_id)
            digest = self._content_hash(finding)

            escalated_from: str | None = None
            if severity_rank(result.severity_floor) > severity_rank(finding.severity):
                escalated_from = finding.severity
                finding.severity = result.severity_floor
                # Raising the severity changes the material content, so the digest
                # the assessment binds to is the *post-escalation* one. Binding to
                # the old digest would leave an assessment that no longer matches
                # the finding it just changed.
                digest = self._content_hash(finding)

            assessment = MaterialityAssessment(
                assessment_id=new_id("mat"),
                tenant_id=tenant_id,
                finding_id=finding.finding_id,
                content_hash=digest,
                policy_id=policy.policy_id,
                policy_json=policy.model_dump(mode="json"),
                population_size=request.inputs.population_size,
                exception_count=request.inputs.exception_count,
                monetary_exposure=request.inputs.monetary_exposure,
                factors_json=[item.model_dump(mode="json") for item in request.inputs.factors],
                components_json=dict(result.components),
                score=result.score,
                material=result.material,
                severity_floor=result.severity_floor,
                rationale=result.rationale,
                assessed_by=request.assessed_by,
            )
            repository.add_assessment(assessment)

            self._emit(
                session,
                tenant_id=tenant_id,
                engagement_id=finding.engagement_id,
                aggregate_id=finding.finding_id,
                event_type="finding.materiality_assessed",
                payload={
                    "finding_id": finding.finding_id,
                    "assessment_id": assessment.assessment_id,
                    "score": result.score,
                    "material": result.material,
                    "severity_floor": result.severity_floor,
                    "severity_escalated_from": escalated_from,
                    "policy_id": policy.policy_id,
                    "components": dict(result.components),
                    "assessed_by": request.assessed_by,
                },
                idempotency_key=f"materiality:{assessment.assessment_id}",
            )
            session.flush()
            session.expunge(assessment)
            return assessment

    def override_severity(
        self, *, tenant_id: str, request: SeverityOverrideRequest
    ) -> MaterialityAssessment:
        """Set a severity below the computed floor, attributably.

        Refuses an override attributed to an automated actor, for the same reason
        approval is refused: an agent that can talk its own finding down to ``low``
        has been handed the conclusion it was supposed to be gated on.

        Refuses an override that does not actually lower the severity, so the
        mechanism cannot be used as a quiet second route to escalation.
        """
        if request.actor_id.lower().startswith(self.agent_role_prefixes):
            raise HumanGateError(
                f"{request.actor_id!r} is an automated actor; lowering a severity "
                "below its computed materiality floor requires a person"
            )

        with self.database.transaction() as session:
            repository = AdjudicationRepository(session)
            finding = self._require_finding(repository, tenant_id, request.finding_id)
            assessment = repository.latest_assessment(
                tenant_id, finding.finding_id, self._content_hash(finding)
            )
            if assessment is None:
                raise MaterialityError(
                    f"finding {finding.finding_id!r} has no current materiality "
                    "assessment; there is no floor to override"
                )
            if severity_rank(request.severity) >= severity_rank(assessment.severity_floor):
                raise MaterialityError(
                    f"severity {request.severity!r} is not below the computed floor "
                    f"{assessment.severity_floor!r}; an override records a reduction, "
                    "not a confirmation"
                )

            previous = finding.severity
            finding.severity = request.severity
            assessment.override_severity = request.severity
            assessment.override_reason = request.reason
            assessment.override_by = request.actor_id
            # The override changes the material content, so it re-binds to the new
            # digest. Any quality review passed against the old text is thereby
            # spent, which is correct: the reviewer approved a different severity.
            assessment.content_hash = self._content_hash(finding)

            repository.add_decision(
                ApprovalDecision(
                    decision_id=new_id("apd"),
                    tenant_id=tenant_id,
                    engagement_id=finding.engagement_id,
                    finding_id=finding.finding_id,
                    decision_type="human:severity_override",
                    actor_id=request.actor_id,
                    reason=request.reason,
                )
            )
            self._emit(
                session,
                tenant_id=tenant_id,
                engagement_id=finding.engagement_id,
                aggregate_id=finding.finding_id,
                event_type="finding.severity_overridden",
                payload={
                    "finding_id": finding.finding_id,
                    "from_severity": previous,
                    "to_severity": request.severity,
                    "computed_floor": assessment.severity_floor,
                    "actor_id": request.actor_id,
                    "reason": request.reason,
                },
                idempotency_key=(
                    f"severity-override:{finding.finding_id}:{request.severity}"
                ),
            )
            session.flush()
            session.expunge(assessment)
            return assessment

    # -- 2b. quality review ----------------------------------------------------

    def review_quality(
        self, *, tenant_id: str, request: QualityReviewRequest
    ) -> quality.QualityReviewOutcome:
        """Run the methodology gate over a finding.

        A failed review is recorded, not raised. The reviewer's job is to report
        what they found; refusing to store a failure would leave the only durable
        trace of a badly supported finding in the logs.
        """
        with self.database.transaction() as session:
            repository = AdjudicationRepository(session)
            finding = self._require_finding(repository, tenant_id, request.finding_id)
            digest = self._content_hash(finding)
            assessment = repository.latest_assessment(tenant_id, finding.finding_id, digest)

            outcome = quality.evaluate(
                reviewer_id=request.reviewer_id,
                authored_by=finding.authored_by,
                severity=finding.severity,
                evidence_ids=list(finding.evidence_ids_json or []),
                contradictions=list(finding.contradictions_json or []),
                exception_keys=list(finding.exception_keys_json or []),
                criteria=finding.criteria,
                observed_condition=finding.observed_condition,
                limitations=list(finding.limitations_json or []),
                materiality=(
                    {
                        "score": assessment.score,
                        "severity_floor": assessment.severity_floor,
                        "override_severity": assessment.override_severity,
                    }
                    if assessment is not None
                    else None
                ),
                skeptic_ran=finding.skeptic_reviewed_at is not None,
                content_hash=digest,
            )

            review_id = new_id("qrv")
            repository.add_quality_review(
                QualityReview(
                    review_id=review_id,
                    tenant_id=tenant_id,
                    finding_id=finding.finding_id,
                    content_hash=digest,
                    reviewer_id=request.reviewer_id,
                    passed=outcome.passed,
                    checks_json=[item.model_dump(mode="json") for item in outcome.checks],
                    notes=request.notes,
                )
            )
            self._emit(
                session,
                tenant_id=tenant_id,
                engagement_id=finding.engagement_id,
                aggregate_id=finding.finding_id,
                event_type=(
                    "finding.quality_review_passed"
                    if outcome.passed
                    else "finding.quality_review_failed"
                ),
                payload={
                    "finding_id": finding.finding_id,
                    "reviewer_id": request.reviewer_id,
                    "passed": outcome.passed,
                    "content_hash": digest,
                    "failed_checks": [item.check.value for item in outcome.failures],
                    "summary": outcome.summary,
                },
                # Keyed on the review, not on the finding and reviewer. A second
                # review of the same text is a real event — a reviewer re-running
                # the gate after a rework that turned out to change nothing — and
                # keying on the pair would make the outbox reject it.
                idempotency_key=f"quality-review:{review_id}",
            )
            return outcome

    # -- 2c. disputes ----------------------------------------------------------

    def raise_dispute(self, *, tenant_id: str, request: DisputeRequest) -> str:
        """Record management's contest of a finding.

        A disputed finding stops moving. It cannot be sent to remediation while the
        disagreement is open, because opening a remediation obligation is the point
        at which the organisation has accepted the finding — doing that under an
        unresolved dispute would record an agreement that does not exist.
        """
        with self.database.transaction() as session:
            repository = AdjudicationRepository(session)
            finding = self._require_finding(repository, tenant_id, request.finding_id)

            if repository.open_dispute(tenant_id, finding.finding_id) is not None:
                raise DisputeError(
                    f"finding {finding.finding_id!r} already has an open dispute; "
                    "resolve it before raising another"
                )

            current = FindingStatus(finding.status)
            self._require_transition(current, FindingStatus.DISPUTED)

            rounds = repository.disputes(tenant_id, finding.finding_id)
            round_no = len(rounds) + 1
            # Past the round limit the disagreement is no longer a working-level
            # one. It is flagged for escalation rather than blocked: refusing the
            # dispute would leave management with no route except to accept.
            escalated = round_no > self.max_dispute_rounds

            dispute = FindingDispute(
                dispute_id=new_id("dsp"),
                tenant_id=tenant_id,
                finding_id=finding.finding_id,
                round_no=round_no,
                ground=request.ground.value,
                statement=request.statement,
                raised_by=request.raised_by,
                evidence_ids_json=list(request.evidence_ids),
                prior_status=current.value,
                status="open",
                escalated=escalated,
            )
            repository.add_dispute(dispute)
            finding.status = FindingStatus.DISPUTED.value

            self._emit(
                session,
                tenant_id=tenant_id,
                engagement_id=finding.engagement_id,
                aggregate_id=finding.finding_id,
                event_type="finding.disputed",
                payload={
                    "finding_id": finding.finding_id,
                    "dispute_id": dispute.dispute_id,
                    "round_no": round_no,
                    "ground": request.ground.value,
                    "raised_by": request.raised_by,
                    "prior_status": current.value,
                    "escalated": escalated,
                    "evidence_ids": list(request.evidence_ids),
                },
                idempotency_key=f"dispute-raised:{dispute.dispute_id}",
            )
            return dispute.dispute_id

    def resolve_dispute(
        self, *, tenant_id: str, request: DisputeResolutionRequest
    ) -> FindingStatus:
        """Answer a dispute, and apply what the answer costs.

        Three outcomes, with three different consequences:

        * **upheld** — the finding returns to the status it held before the
          dispute, with the disagreement retained on the record;
        * **modified** — the audit side concedes the finding must change. It
          returns to ``proposed``, and the approval it may already have had is
          void, because both the quality review and the approval were given for
          text that is about to change;
        * **withdrawn** — the finding is dropped.

        The resolver may not be the party that raised the dispute, and may not be
        the author of the finding. Letting either side resolve its own
        disagreement is not adjudication.
        """
        if request.resolved_by.lower().startswith(self.agent_role_prefixes):
            raise HumanGateError(
                f"{request.resolved_by!r} is an automated actor; resolving a dispute "
                "over a finding requires a decision attributable to a person"
            )

        with self.database.transaction() as session:
            repository = AdjudicationRepository(session)
            dispute = repository.get_dispute(tenant_id, request.dispute_id)
            if dispute is None:
                raise DisputeError(f"dispute {request.dispute_id!r} was not found")
            if dispute.status != "open":
                raise DisputeError(
                    f"dispute {dispute.dispute_id!r} is already {dispute.status}"
                )
            finding = self._require_finding(repository, tenant_id, dispute.finding_id)

            resolver = request.resolved_by.strip().lower()
            if resolver == dispute.raised_by.strip().lower():
                raise IndependenceError(
                    f"{request.resolved_by!r} raised this dispute and cannot resolve it"
                )
            if finding.authored_by and resolver == finding.authored_by.strip().lower():
                raise IndependenceError(
                    f"{request.resolved_by!r} authored the finding and cannot resolve "
                    "a dispute against it"
                )

            if request.resolution is DisputeResolution.UPHELD:
                target = FindingStatus(dispute.prior_status)
            elif request.resolution is DisputeResolution.MODIFIED:
                target = FindingStatus.PROPOSED
            else:
                target = FindingStatus.WITHDRAWN
            self._require_transition(FindingStatus.DISPUTED, target)

            dispute.status = "resolved"
            dispute.resolution = request.resolution.value
            dispute.resolution_reason = request.reason
            dispute.resolved_by = request.resolved_by
            dispute.resolved_at = utc_now()
            finding.status = target.value

            repository.add_decision(
                ApprovalDecision(
                    decision_id=new_id("apd"),
                    tenant_id=tenant_id,
                    engagement_id=finding.engagement_id,
                    finding_id=finding.finding_id,
                    decision_type=f"dispute:{request.resolution.value}",
                    actor_id=request.resolved_by,
                    reason=request.reason,
                )
            )
            self._emit(
                session,
                tenant_id=tenant_id,
                engagement_id=finding.engagement_id,
                aggregate_id=finding.finding_id,
                event_type=f"finding.dispute_{request.resolution.value}",
                payload={
                    "finding_id": finding.finding_id,
                    "dispute_id": dispute.dispute_id,
                    "round_no": dispute.round_no,
                    "resolution": request.resolution.value,
                    "resolved_by": request.resolved_by,
                    "status": target.value,
                    "reason": request.reason,
                },
                idempotency_key=f"dispute-resolved:{dispute.dispute_id}",
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
                external_target=request.external_target,
                # The external reference is derived from the action, so a retry
                # that reaches an external system presents the same key.
                external_ref=None,
                external_sync_state=(
                    "not_applicable" if request.external_system == "none" else "pending"
                ),
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

    # -- 3a. external remediation systems --------------------------------------

    def sync_remediation_ticket(
        self, *, tenant_id: str, action_id: str, writer: TicketWriter | None = None
    ) -> dict[str, Any]:
        """File the remediation in its external system, at most once.

        Two guards, and both are load-bearing. The local one — an ``external_ref``
        already on the action — makes the ordinary retry free. The remote one —
        the writer's correlation lookup — is what survives a crash between the
        provider's create and this transaction's commit, where local state says
        "no ticket" and the provider disagrees. Only the provider can settle that,
        so the lookup is not an optimisation to skip when state looks clean.

        A provider failure is recorded on the action and re-raised. That ordering
        forces the shape of this method: the provider call happens between two
        transactions rather than inside one. Recording the failure in the same
        transaction that then raises would roll the record back, leaving a method
        that documents a behaviour it does not have — and holding a database
        transaction open across a network round trip is its own mistake.
        """
        with self.database.read_session() as session:
            repository = AdjudicationRepository(session)
            action = repository.get_action(tenant_id, action_id)
            if action is None:
                raise RemediationNotFoundError(
                    f"remediation action {action_id!r} was not found"
                )
            finding = self._require_finding(repository, tenant_id, action.finding_id)

            if action.external_ref:
                return {
                    "action_id": action.action_id,
                    "external_system": action.external_system,
                    "external_ref": action.external_ref,
                    "external_url": action.external_url,
                    "created": False,
                    "reason": "already filed",
                }

            writer = writer or NullTicketWriter()
            if writer.system != action.external_system:
                raise TicketingError(
                    f"remediation {action.action_id} is registered against "
                    f"{action.external_system!r} but the supplied writer files into "
                    f"{writer.system!r}"
                )
            if action.external_system != "none" and not action.external_target:
                raise TicketingError(
                    f"remediation {action.action_id} names no project or table in "
                    f"{action.external_system!r}; a ticket cannot be filed without one"
                )

            engagement_id = finding.engagement_id
            finding_id = finding.finding_id
            ticket_request = TicketRequest(
                action_id=action.action_id,
                finding_code=finding.code,
                title=f"[{finding.code}] {finding.title}",
                description=(
                    f"Remediation for AssuranceOS finding {finding.code} "
                    f"(severity {finding.severity}).\n\n"
                    f"Criteria: {finding.criteria}\n\n"
                    f"Observed condition: {finding.observed_condition}\n\n"
                    f"Agreed action: {action.action_plan}"
                ),
                owner_ref=action.owner_ref,
                due_date=action.due_date,
                severity=finding.severity,
                project_or_table=action.external_target or "none",
                labels=("assuranceos", f"finding-{finding.code.lower()}"),
            )
            external_system = action.external_system

        try:
            ticket = writer.create_or_get(ticket_request)
        except Exception as exc:  # noqa: BLE001 - recorded, then re-raised
            with self.database.transaction() as session:
                failed = AdjudicationRepository(session).get_action(tenant_id, action_id)
                if failed is not None:
                    failed.external_sync_state = "failed"
                    failed.external_error = str(exc)[:4000]
                self._emit(
                    session,
                    tenant_id=tenant_id,
                    engagement_id=engagement_id,
                    aggregate_id=action_id,
                    event_type="remediation.ticket_failed",
                    payload={
                        "action_id": action_id,
                        "finding_id": finding_id,
                        "external_system": external_system,
                        "error": str(exc)[:1000],
                    },
                    idempotency_key=f"ticket-failed:{action_id}",
                    aggregate_type="remediation_action",
                    # A provider failure is an internal condition, and retries of a
                    # failing provider are expected. Publishing one outbox event per
                    # attempt would either flood the topic or collide on its key, so
                    # the failure is written to the audit log only.
                    outbox=False,
                )
            raise TicketingError(
                f"{external_system} refused remediation {action_id}: {exc}"
            ) from exc

        with self.database.transaction() as session:
            repository = AdjudicationRepository(session)
            action = repository.get_action(tenant_id, action_id)
            if action is None:
                raise RemediationNotFoundError(
                    f"remediation action {action_id!r} was not found"
                )
            # Another worker may have filed between the read and here. Its
            # reference wins: the provider's correlation lookup guarantees both
            # workers are talking about the same ticket, and overwriting achieves
            # nothing but a second write.
            if action.external_ref:
                return {
                    "action_id": action.action_id,
                    "external_system": action.external_system,
                    "external_ref": action.external_ref,
                    "external_url": action.external_url,
                    "created": False,
                    "reason": "filed concurrently",
                }

            action.external_ref = ticket.external_ref
            action.external_url = ticket.url
            action.external_sync_state = "synced"
            action.external_synced_at = utc_now()
            action.external_error = None

            # Filing and reconciling are different events. The first says a ticket
            # now exists because we made one; the second says a ticket already
            # existed and local state has caught up with it. Collapsing them would
            # make the duplicate-ticket bug indistinguishable from its absence, and
            # would also collide on the outbox key.
            if ticket.created:
                event_type = "remediation.ticket_filed"
                key = f"ticket-filed:{action_id}"
            else:
                event_type = "remediation.ticket_reconciled"
                key = f"ticket-reconciled:{action_id}:{ticket.external_ref}"
            self._emit(
                session,
                tenant_id=tenant_id,
                engagement_id=engagement_id,
                aggregate_id=action_id,
                event_type=event_type,
                payload={
                    "action_id": action_id,
                    "finding_id": finding_id,
                    "external_system": ticket.system,
                    "external_ref": ticket.external_ref,
                    "external_url": ticket.url,
                    "correlation_key": ticket_request.correlation_key,
                    "created": ticket.created,
                },
                idempotency_key=key,
                aggregate_type="remediation_action",
            )
            return {
                "action_id": action_id,
                "external_system": ticket.system,
                "external_ref": ticket.external_ref,
                "external_url": ticket.url,
                "created": ticket.created,
                "correlation_key": ticket_request.correlation_key,
            }

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
            digest = self._content_hash(finding)
            assessment = repository.latest_assessment(tenant_id, finding.finding_id, digest)
            blockers = self._approval_blockers(repository, tenant_id, finding)
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
                content_hash=digest,
                approval_ready=not blockers,
                approval_blockers=blockers,
                materiality=(
                    {
                        "assessment_id": assessment.assessment_id,
                        "score": assessment.score,
                        "material": assessment.material,
                        "severity_floor": assessment.severity_floor,
                        "policy_id": assessment.policy_id,
                        "components": dict(assessment.components_json or {}),
                        "rationale": assessment.rationale,
                        "assessed_by": assessment.assessed_by,
                        "override_severity": assessment.override_severity,
                        "override_reason": assessment.override_reason,
                        "override_by": assessment.override_by,
                    }
                    if assessment is not None
                    else None
                ),
                quality_reviews=[
                    {
                        "review_id": item.review_id,
                        "reviewer_id": item.reviewer_id,
                        "passed": item.passed,
                        "content_hash": item.content_hash,
                        # A review whose hash no longer matches the finding is
                        # surfaced as spent rather than hidden: "this was reviewed
                        # and then changed" is the state a reader must be able to see.
                        "applies_to_current_text": item.content_hash == digest,
                        "failed_checks": [
                            check["check"]
                            for check in (item.checks_json or [])
                            if not check.get("passed")
                        ],
                        "notes": item.notes,
                        "reviewed_at": item.reviewed_at.isoformat(),
                    }
                    for item in repository.quality_reviews(tenant_id, finding_id)
                ],
                disputes=[
                    {
                        "dispute_id": item.dispute_id,
                        "round_no": item.round_no,
                        "ground": item.ground,
                        "statement": item.statement,
                        "raised_by": item.raised_by,
                        "status": item.status,
                        "resolution": item.resolution,
                        "resolution_reason": item.resolution_reason,
                        "resolved_by": item.resolved_by,
                        "escalated": item.escalated,
                        "evidence_ids": list(item.evidence_ids_json or []),
                    }
                    for item in repository.disputes(tenant_id, finding_id)
                ],
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
                        "external_target": item.external_target,
                        "external_ref": item.external_ref,
                        "external_url": item.external_url,
                        "external_sync_state": item.external_sync_state,
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

    def get_remediation_action(self, *, tenant_id: str, action_id: str) -> dict[str, Any]:
        """Return the routing metadata needed to select an external writer."""
        with self.database.read_session() as session:
            action = AdjudicationRepository(session).get_action(tenant_id, action_id)
            if action is None:
                raise RemediationNotFoundError(
                    f"remediation action {action_id!r} was not found"
                )
            return {
                "action_id": action.action_id,
                "external_system": action.external_system,
                "external_target": action.external_target,
                "external_ref": action.external_ref,
            }

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
    def _content_hash(finding: Finding) -> str:
        """The digest of this finding's material content, as stored."""
        return content_hash(
            code=finding.code,
            title=finding.title,
            severity=finding.severity,
            criteria=finding.criteria,
            observed_condition=finding.observed_condition,
            risk_statement=finding.risk_statement,
            evidence_ids=list(finding.evidence_ids_json or []),
            exception_keys=list(finding.exception_keys_json or []),
        )

    def approval_blockers(self, *, tenant_id: str, finding_id: str) -> list[str]:
        """The approval gates currently open on a finding, for display.

        Public because read models need it. A projection that recomputes the
        gates itself will drift from the ones that actually refuse the approval,
        and the version that drifts is the one the operator is looking at.
        """
        with self.database.read_session() as session:
            repository = AdjudicationRepository(session)
            finding = repository.get_finding(tenant_id, finding_id)
            if finding is None or finding.status != "proposed":
                return []
            return self._approval_blockers(repository, tenant_id, finding)

    def _approval_blockers(
        self, repository: AdjudicationRepository, tenant_id: str, finding: Finding
    ) -> list[str]:
        """Every reason this finding cannot currently be approved.

        Returns all of them rather than the first. An approver who fixes one
        blocker only to be told about the next has been given a worse experience
        than one who is handed the list, and the list is what the UI shows.
        """
        digest = self._content_hash(finding)
        blockers: list[str] = []

        if repository.open_dispute(tenant_id, finding.finding_id) is not None:
            blockers.append("an open dispute must be resolved before approval")

        if repository.latest_assessment(tenant_id, finding.finding_id, digest) is None:
            blockers.append(
                "no materiality assessment exists for the current text of the finding"
            )

        if self.require_quality_review:
            review = repository.passing_review(tenant_id, finding.finding_id, digest)
            if review is None:
                stale = [
                    item
                    for item in repository.quality_reviews(tenant_id, finding.finding_id)
                    if item.passed and item.content_hash != digest
                ]
                blockers.append(
                    "the finding changed after its last passing quality review"
                    if stale
                    else "no passing quality review exists for the current text of the finding"
                )
        return blockers

    def _require_approval_preconditions(
        self,
        repository: AdjudicationRepository,
        tenant_id: str,
        finding: Finding,
        *,
        approver_id: str,
    ) -> None:
        """Refuse an approval that has not cleared the gates before it.

        Separation of the two gates is enforced here: the person who performed the
        quality review may not also approve the finding. Preparer, reviewer and
        approver being three people is the ordinary shape of an audit file, and a
        system that permits two of them to be one person has removed a control
        without saying so.
        """
        blockers = self._approval_blockers(repository, tenant_id, finding)
        if blockers:
            raise QualityGateError(
                f"finding {finding.finding_id!r} cannot be approved: " + "; ".join(blockers)
            )

        if self.require_quality_review:
            digest = self._content_hash(finding)
            review = repository.passing_review(tenant_id, finding.finding_id, digest)
            if review is not None and (
                review.reviewer_id.strip().lower() == approver_id.strip().lower()
            ):
                raise IndependenceError(
                    f"{approver_id!r} performed the quality review of this finding "
                    "and cannot also approve it"
                )

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
        outbox: bool = True,
    ) -> None:
        """Write the audit event and the outbox event in the caller's transaction.

        Both are written here rather than by the caller so that a state change can
        never commit without the record of it.

        ``outbox=False`` records the audit event alone. Reserved for conditions
        that have no downstream contract and can legitimately repeat, where a
        published event per occurrence would be noise and a fixed idempotency key
        would collide.
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
        if not outbox:
            return
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
    limitations: Sequence[str] = (),
) -> ProposedFinding:
    """Build a proposed finding from accepted control-test exceptions.

    The observed condition is composed from the exception rows rather than
    written by a model, so the statement of what was seen is a computed fact. A
    model's contribution is the risk statement and the recommendation, which are
    judgment; the count and the population are not.

    ``limitations`` is the caller's, not a default. Where the skeptic will suppress
    some of the exceptions, the quality gate requires that suppression to be
    disclosed, and inventing the disclosure here would let the finding satisfy the
    gate without anyone having written it.
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
        limitations=list(limitations),
        source_run_id=source_run_id,
    )
