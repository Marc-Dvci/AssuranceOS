"""The audit universe, risk assessment, and portfolio planning.

This is the front of the loop. Everything downstream — the compiled engagement,
the governed agent, the finding, the remediation, the retest — answers "did this
control work". This component answers the question before it: *which* controls are
worth asking about this year, and what does choosing them leave uncovered.

Three properties are enforced rather than encouraged, and each is a place where an
audit function quietly loses its independence:

* **A rating is computed, and an override is a decision.** The recommended rating
  comes from declared inputs under a versioned policy. A person may set it aside,
  and doing so records their name, their reason, and the number they overrode. The
  computed value is never erased.
* **A plan is recommended, never activated.** Approving a plan is a human act.
  Until then it is a proposal, and the proposal carries its own exclusions so the
  approval is informed.
* **Approving a plan accepts its residual explicitly.** The risks the plan does
  not cover are written into the approval as accepted residual, attributed to the
  approver. An audit committee that accepted a plan accepted what it left out;
  this makes that fact retrievable rather than inferable.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date, datetime, timezone
from typing import Any

from sqlalchemy import func, select

from assuranceos.db.models import (
    AssuranceCoverage,
    AuditPlan,
    AuditUniverseEntity,
    PlanProposal,
    Risk,
    RiskAssessment,
)
from assuranceos.db.repositories import AuditEventRepository, OutboxRepository, new_id
from assuranceos.db.session import Database
from assuranceos.models import AuditEvent

from .exceptions import (
    CapacityError,
    PlanNotFoundError,
    PlanStateError,
    RiskNotFoundError,
)
from .planning import Candidate, CapacityPolicy, recommend
from .repository import PortfolioRepository
from .scoring import AssuranceSource, RiskFactors, ScoringPolicy, score


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class PortfolioService:
    """Audit universe, risk ratings, and the plan they justify."""

    def __init__(
        self,
        database: Database,
        *,
        agent_role_prefixes: Sequence[str] = ("agent:", "svc:", "system:"),
    ):
        self.database = database
        self.agent_role_prefixes = tuple(agent_role_prefixes)

    # -- the universe ----------------------------------------------------------

    def register_entity(
        self,
        *,
        tenant_id: str,
        entity_type: str,
        name: str,
        criticality: float = 0.0,
        external_ref: str | None = None,
        attributes: Mapping[str, Any] | None = None,
        profile_id: str | None = None,
    ) -> str:
        """Add or update something auditable.

        Keyed on ``(entity_type, external_ref)`` where a reference exists, so a
        re-import of a system inventory updates the entity rather than creating a
        second one. An audit universe that doubles on every sync is one nobody
        trusts.
        """
        with self.database.transaction() as session:
            repository = PortfolioRepository(session)
            existing = (
                repository.entity_by_ref(tenant_id, entity_type, external_ref)
                if external_ref
                else None
            )
            if existing is not None:
                existing.name = name
                existing.criticality = criticality
                existing.attributes_json = dict(attributes or {})
                existing.active = True
                return existing.entity_id
            record = AuditUniverseEntity(
                entity_id=new_id("ent"),
                tenant_id=tenant_id,
                profile_id=profile_id,
                entity_type=entity_type,
                name=name,
                external_ref=external_ref,
                criticality=criticality,
                attributes_json=dict(attributes or {}),
            )
            repository.add_entity(record)
            return record.entity_id

    def register_risk(
        self,
        *,
        tenant_id: str,
        code: str,
        title: str,
        description: str | None = None,
    ) -> str:
        with self.database.transaction() as session:
            repository = PortfolioRepository(session)
            existing = repository.risk_by_code(tenant_id, code)
            if existing is not None:
                existing.title = title
                if description:
                    existing.description = description
                return existing.risk_id
            record = Risk(
                risk_id=new_id("rsk"),
                tenant_id=tenant_id,
                code=code,
                title=title,
                description=description,
            )
            repository.add_risk(record)
            return record.risk_id

    def record_coverage(
        self,
        *,
        tenant_id: str,
        risk_code: str,
        source: AssuranceSource,
        obtained_on: date,
        recorded_by: str,
        scope_note: str = "",
        reference: str | None = None,
        engagement_id: str | None = None,
        entity_id: str | None = None,
    ) -> str:
        """Record assurance obtained over a risk from somewhere.

        Kept separate from the rating. Assurance lowers the need for *fresh audit
        work*; it does not lower the risk, and folding it into the residual score
        would let a function argue a risk down by having looked at it once.
        """
        with self.database.transaction() as session:
            repository = PortfolioRepository(session)
            risk = self._require_risk(repository, tenant_id, risk_code)
            record = AssuranceCoverage(
                coverage_id=new_id("cov"),
                tenant_id=tenant_id,
                risk_id=risk.risk_id,
                entity_id=entity_id,
                source=source.value,
                obtained_on=obtained_on,
                scope_note=scope_note or None,
                reference=reference,
                engagement_id=engagement_id,
                recorded_by=recorded_by,
            )
            repository.add_coverage(record)
            return record.coverage_id

    # -- risk assessment -------------------------------------------------------

    def assess_risk(
        self,
        *,
        tenant_id: str,
        risk_code: str,
        factors: RiskFactors,
        assessed_by: str,
        as_at: date,
        policy: ScoringPolicy | None = None,
    ) -> RiskAssessment:
        """Score a risk from declared inputs under a versioned policy.

        The assessment is a new version rather than an update. "What did we think
        this was last year, and on what basis" is the question asked whenever a
        rating moves, and a mutable rating column cannot answer it.

        ``assessed_by`` may be an agent. Scoring is arithmetic; what an agent must
        not reach is the *official* rating, which is a decision.
        """
        policy = policy or ScoringPolicy()
        result = score(factors, policy, as_at=as_at)

        with self.database.transaction() as session:
            repository = PortfolioRepository(session)
            risk = self._require_risk(repository, tenant_id, risk_code)
            version = repository.next_assessment_version(risk.risk_id)

            record = RiskAssessment(
                assessment_id=new_id("rsa"),
                tenant_id=tenant_id,
                risk_id=risk.risk_id,
                version=version,
                policy_id=policy.policy_id,
                policy_json=policy.model_dump(mode="json"),
                factors_json=factors.model_dump(mode="json"),
                components_json=dict(result.components),
                inherent=result.inherent,
                residual=result.residual,
                rating=result.rating,
                confidence=result.confidence,
                audit_priority=result.audit_priority,
                uncovered=result.uncovered,
                rationale=result.rationale,
                assessed_by=assessed_by,
                as_at=as_at,
            )
            repository.add_assessment(record)

            # The risk row carries the current numbers so a register view does not
            # have to join the assessment history; the history is where the
            # justification lives.
            risk.inherent_impact = factors.impact
            risk.inherent_likelihood = factors.likelihood
            risk.velocity = factors.velocity
            risk.residual_risk = result.residual
            risk.confidence = result.confidence
            risk.evidence_json = list(factors.evidence_ids)

            self._emit(
                session,
                tenant_id=tenant_id,
                aggregate_id=risk.risk_id,
                event_type="risk.assessed",
                payload={
                    "risk_id": risk.risk_id,
                    "code": risk_code,
                    "version": version,
                    "inherent": result.inherent,
                    "residual": result.residual,
                    "rating": result.rating,
                    "confidence": result.confidence,
                    "audit_priority": result.audit_priority,
                    "untested_controls": result.components["untested_controls"],
                    "assessed_by": assessed_by,
                },
                idempotency_key=f"risk-assessed:{record.assessment_id}",
                aggregate_type="risk",
            )
            session.flush()
            session.expunge(record)
            return record

    def set_official_rating(
        self,
        *,
        tenant_id: str,
        risk_code: str,
        rating: str,
        actor_id: str,
        reason: str,
    ) -> RiskAssessment:
        """Set aside the computed rating, attributably.

        Refused for automated actors. A model that can overwrite its own rating
        has been handed the conclusion the computation existed to constrain.

        The computed value is kept beside the override. A register that shows only
        the number somebody preferred cannot show that a disagreement happened.
        """
        if actor_id.lower().startswith(self.agent_role_prefixes):
            raise PlanStateError(
                f"{actor_id!r} is an automated actor; setting an official risk rating "
                "requires a decision attributable to a person"
            )
        with self.database.transaction() as session:
            repository = PortfolioRepository(session)
            risk = self._require_risk(repository, tenant_id, risk_code)
            assessment = repository.latest_assessment(risk.risk_id)
            if assessment is None:
                raise RiskNotFoundError(
                    f"risk {risk_code!r} has no assessment to override"
                )
            assessment.official_rating = rating
            assessment.official_reason = reason
            assessment.official_by = actor_id
            self._emit(
                session,
                tenant_id=tenant_id,
                aggregate_id=risk.risk_id,
                event_type="risk.rating_overridden",
                payload={
                    "risk_id": risk.risk_id,
                    "code": risk_code,
                    "computed_rating": assessment.rating,
                    "official_rating": rating,
                    "actor_id": actor_id,
                    "reason": reason,
                },
                idempotency_key=f"risk-override:{assessment.assessment_id}",
                aggregate_type="risk",
            )
            session.flush()
            session.expunge(assessment)
            return assessment

    def register_view(self, *, tenant_id: str) -> list[dict[str, Any]]:
        """The risk register as it currently stands.

        Reports the computed rating and any override side by side rather than
        collapsing them, so a reader can see where the two disagree without
        reading the assessment history.
        """
        with self.database.read_session() as session:
            repository = PortfolioRepository(session)
            rows = []
            for risk in repository.list_risks(tenant_id):
                assessment = repository.latest_assessment(risk.risk_id)
                rows.append(
                    {
                        "code": risk.code,
                        "title": risk.title,
                        "computed_rating": assessment.rating if assessment else None,
                        "official_rating": (
                            assessment.official_rating if assessment else None
                        ),
                        "effective_rating": (
                            (assessment.official_rating or assessment.rating)
                            if assessment
                            else None
                        ),
                        "residual": assessment.residual if assessment else None,
                        "confidence": assessment.confidence if assessment else None,
                        "audit_priority": assessment.audit_priority if assessment else None,
                        "uncovered": assessment.uncovered if assessment else True,
                        "assessed_as_at": (
                            assessment.as_at.isoformat() if assessment else None
                        ),
                        "untested_controls": (
                            (assessment.components_json or {}).get("untested_controls", [])
                            if assessment
                            else []
                        ),
                    }
                )
            return rows

    # -- planning --------------------------------------------------------------

    def propose_plan(
        self,
        *,
        tenant_id: str,
        name: str,
        candidates: Sequence[Candidate],
        policy: CapacityPolicy,
        proposed_by: str,
        scenario: str = "baseline",
    ) -> dict[str, Any]:
        """Recommend a plan, and record what it declined to cover.

        The exclusions and blind spots are stored with the proposal. An approval
        given without seeing what was left out is not an informed one, and a
        proposal that cannot show what it declined cannot evidence that it was.
        """
        if not candidates:
            raise CapacityError("a plan proposal needs at least one candidate")
        recommendation = recommend(list(candidates), policy)

        with self.database.transaction() as session:
            repository = PortfolioRepository(session)
            version = int(
                session.scalar(
                    select(func.max(PlanProposal.version)).where(
                        PlanProposal.tenant_id == tenant_id,
                        PlanProposal.name == name,
                    )
                )
                or 0
            ) + 1
            record = PlanProposal(
                proposal_id=new_id("plp"),
                tenant_id=tenant_id,
                name=name,
                version=version,
                status="proposed",
                scenario=scenario,
                horizon_start=policy.horizon_start,
                horizon_end=policy.horizon_end,
                policy_json=policy.model_dump(mode="json"),
                planned_json=[item.model_dump(mode="json") for item in recommendation.planned],
                excluded_json=[
                    item.model_dump(mode="json") for item in recommendation.excluded
                ],
                blind_spots_json=list(recommendation.blind_spots),
                planned_days=recommendation.planned_days,
                plannable_days=recommendation.plannable_days,
                coverage_ratio=recommendation.coverage_ratio,
                uncovered_priority=recommendation.uncovered_priority,
                notes_json=list(recommendation.policy_notes),
                proposed_by=proposed_by,
            )
            repository.add_proposal(record)
            self._emit(
                session,
                tenant_id=tenant_id,
                aggregate_id=record.proposal_id,
                event_type="plan.proposed",
                payload={
                    "proposal_id": record.proposal_id,
                    "name": name,
                    "version": version,
                    "scenario": scenario,
                    "planned": len(recommendation.planned),
                    "excluded": len(recommendation.excluded),
                    "blind_spots": len(recommendation.blind_spots),
                    "planned_days": recommendation.planned_days,
                    "coverage_ratio": recommendation.coverage_ratio,
                    "proposed_by": proposed_by,
                },
                idempotency_key=f"plan-proposed:{record.proposal_id}",
                aggregate_type="plan_proposal",
            )
            return {
                "proposal_id": record.proposal_id,
                "name": name,
                "version": version,
                "scenario": scenario,
                **recommendation.model_dump(mode="json"),
                "deliverable": recommendation.is_deliverable,
            }

    def simulate(
        self,
        *,
        candidates: Sequence[Candidate],
        policy: CapacityPolicy,
    ) -> dict[str, Any]:
        """Recompute a plan under a hypothetical without recording anything.

        The question a head of audit asks in a budget conversation is "what do we
        stop doing if we lose two people". Answering it must not create a plan
        proposal, so this path deliberately touches no state.
        """
        recommendation = recommend(list(candidates), policy)
        return {**recommendation.model_dump(mode="json"), "deliverable": recommendation.is_deliverable}

    def approve_plan(
        self,
        *,
        tenant_id: str,
        proposal_id: str,
        approved_by: str,
        reason: str,
    ) -> dict[str, Any]:
        """Accept a proposal, and record what accepting it accepted.

        Refused for automated actors, and refused when the proposal does not fit
        its own plannable capacity: approving a plan that cannot be delivered
        records a commitment nobody can keep.

        On approval the exclusions become *accepted residual*, attributed to the
        approver. An audit committee that accepted a plan accepted what it left
        out, and this makes that retrievable rather than inferable.
        """
        if approved_by.lower().startswith(self.agent_role_prefixes):
            raise PlanStateError(
                f"{approved_by!r} is an automated actor; approving an audit plan "
                "requires a decision attributable to a person"
            )
        with self.database.transaction() as session:
            repository = PortfolioRepository(session)
            proposal = repository.get_proposal(tenant_id, proposal_id)
            if proposal is None:
                raise PlanNotFoundError(f"plan proposal {proposal_id!r} was not found")
            if proposal.status == "approved":
                return {"proposal_id": proposal_id, "plan_id": proposal.plan_id, "created": False}
            if proposal.status != "proposed":
                raise PlanStateError(
                    f"plan proposal {proposal_id!r} is {proposal.status!r} and cannot be approved"
                )
            if proposal.planned_days > proposal.plannable_days:
                raise CapacityError(
                    f"proposal {proposal_id!r} plans {proposal.planned_days:.1f} days against "
                    f"{proposal.plannable_days:.1f} plannable; it cannot be approved as it stands"
                )

            plan = AuditPlan(
                plan_id=new_id("pln"),
                tenant_id=tenant_id,
                name=proposal.name,
                version=proposal.version,
                status="approved",
                horizon_start=proposal.horizon_start,
                horizon_end=proposal.horizon_end,
                coverage_policy_json=dict(proposal.policy_json or {}),
                approved_at=utc_now(),
                approved_by=approved_by,
            )
            repository.add_plan(plan)

            excluded = list(proposal.excluded_json or [])
            blind_spots = list(proposal.blind_spots_json or [])
            proposal.status = "approved"
            proposal.plan_id = plan.plan_id
            proposal.approved_at = utc_now()
            proposal.approved_by = approved_by
            proposal.approval_reason = reason
            proposal.accepted_residual_json = {
                "accepted_by": approved_by,
                "accepted_at": utc_now().isoformat(),
                "uncovered_priority": proposal.uncovered_priority,
                "excluded": [
                    {
                        "candidate_key": item.get("candidate_key"),
                        "risk_ref": item.get("risk_ref"),
                        "rating": item.get("rating"),
                        "reason": item.get("reason"),
                    }
                    for item in excluded
                ],
                "blind_spots": [
                    {
                        "candidate_key": item.get("candidate_key"),
                        "risk_ref": item.get("risk_ref"),
                        "rating": item.get("rating"),
                    }
                    for item in blind_spots
                ],
            }

            self._emit(
                session,
                tenant_id=tenant_id,
                aggregate_id=proposal.proposal_id,
                event_type="plan.approved",
                payload={
                    "proposal_id": proposal.proposal_id,
                    "plan_id": plan.plan_id,
                    "approved_by": approved_by,
                    "reason": reason,
                    "planned": len(proposal.planned_json or []),
                    # Carried into the event so a downstream consumer sees the
                    # accepted residual without reading the proposal back.
                    "excluded": len(excluded),
                    "blind_spots": len(blind_spots),
                    "uncovered_priority": proposal.uncovered_priority,
                },
                idempotency_key=f"plan-approved:{proposal.proposal_id}",
                aggregate_type="plan_proposal",
            )
            return {
                "proposal_id": proposal_id,
                "plan_id": plan.plan_id,
                "created": True,
                "accepted_residual": dict(proposal.accepted_residual_json),
            }

    def proposal_view(self, *, tenant_id: str, proposal_id: str) -> dict[str, Any]:
        with self.database.read_session() as session:
            proposal = PortfolioRepository(session).get_proposal(tenant_id, proposal_id)
            if proposal is None:
                raise PlanNotFoundError(f"plan proposal {proposal_id!r} was not found")
            return {
                "proposal_id": proposal.proposal_id,
                "name": proposal.name,
                "version": proposal.version,
                "status": proposal.status,
                "scenario": proposal.scenario,
                "horizon_start": proposal.horizon_start.isoformat(),
                "horizon_end": proposal.horizon_end.isoformat(),
                "planned": list(proposal.planned_json or []),
                "excluded": list(proposal.excluded_json or []),
                "blind_spots": list(proposal.blind_spots_json or []),
                "planned_days": proposal.planned_days,
                "plannable_days": proposal.plannable_days,
                "coverage_ratio": proposal.coverage_ratio,
                "uncovered_priority": proposal.uncovered_priority,
                "notes": list(proposal.notes_json or []),
                "plan_id": proposal.plan_id,
                "approved_by": proposal.approved_by,
                "accepted_residual": dict(proposal.accepted_residual_json or {}),
            }

    # -- internals -------------------------------------------------------------

    @staticmethod
    def _require_risk(repository: PortfolioRepository, tenant_id: str, code: str) -> Risk:
        risk = repository.risk_by_code(tenant_id, code)
        if risk is None:
            raise RiskNotFoundError(f"risk {code!r} is not registered for this tenant")
        return risk

    def _emit(
        self,
        session: Any,
        *,
        tenant_id: str,
        aggregate_id: str,
        event_type: str,
        payload: Mapping[str, Any],
        idempotency_key: str,
        aggregate_type: str,
    ) -> None:
        AuditEventRepository(session).append(
            AuditEvent(
                event_type=event_type,
                tenant_id=tenant_id,
                engagement_id=None,
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
