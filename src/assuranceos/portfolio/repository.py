"""Canonical reads and writes for the audit universe and the plan."""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from assuranceos.db.models import (
    AssuranceCoverage,
    AuditPlan,
    AuditUniverseEntity,
    PlanProposal,
    Risk,
    RiskAssessment,
)


class PortfolioRepository:
    def __init__(self, session: Session):
        self.session = session

    # -- universe --------------------------------------------------------------

    def add_entity(self, entity: AuditUniverseEntity) -> AuditUniverseEntity:
        self.session.add(entity)
        self.session.flush()
        return entity

    def entity_by_ref(
        self, tenant_id: str, entity_type: str, external_ref: str | None
    ) -> AuditUniverseEntity | None:
        if external_ref is None:
            return None
        return self.session.scalar(
            select(AuditUniverseEntity).where(
                AuditUniverseEntity.tenant_id == tenant_id,
                AuditUniverseEntity.entity_type == entity_type,
                AuditUniverseEntity.external_ref == external_ref,
            )
        )

    def list_entities(self, tenant_id: str) -> list[AuditUniverseEntity]:
        return list(
            self.session.scalars(
                select(AuditUniverseEntity)
                .where(
                    AuditUniverseEntity.tenant_id == tenant_id,
                    AuditUniverseEntity.active.is_(True),
                )
                .order_by(AuditUniverseEntity.entity_type, AuditUniverseEntity.name)
            )
        )

    # -- risks -----------------------------------------------------------------

    def add_risk(self, risk: Risk) -> Risk:
        self.session.add(risk)
        self.session.flush()
        return risk

    def risk_by_code(self, tenant_id: str, code: str) -> Risk | None:
        return self.session.scalar(
            select(Risk).where(Risk.tenant_id == tenant_id, Risk.code == code)
        )

    def list_risks(self, tenant_id: str) -> list[Risk]:
        return list(
            self.session.scalars(
                select(Risk).where(Risk.tenant_id == tenant_id).order_by(Risk.code)
            )
        )

    # -- assessments -----------------------------------------------------------

    def add_assessment(self, assessment: RiskAssessment) -> RiskAssessment:
        self.session.add(assessment)
        self.session.flush()
        return assessment

    def next_assessment_version(self, risk_id: str) -> int:
        current = self.session.scalar(
            select(func.max(RiskAssessment.version)).where(RiskAssessment.risk_id == risk_id)
        )
        return int(current or 0) + 1

    def latest_assessment(self, risk_id: str) -> RiskAssessment | None:
        return self.session.scalar(
            select(RiskAssessment)
            .where(RiskAssessment.risk_id == risk_id)
            .order_by(RiskAssessment.version.desc())
        )

    def assessment_history(self, risk_id: str) -> list[RiskAssessment]:
        return list(
            self.session.scalars(
                select(RiskAssessment)
                .where(RiskAssessment.risk_id == risk_id)
                .order_by(RiskAssessment.version)
            )
        )

    # -- coverage --------------------------------------------------------------

    def add_coverage(self, coverage: AssuranceCoverage) -> AssuranceCoverage:
        self.session.add(coverage)
        self.session.flush()
        return coverage

    def coverage_for(self, tenant_id: str, risk_id: str) -> list[AssuranceCoverage]:
        return list(
            self.session.scalars(
                select(AssuranceCoverage)
                .where(
                    AssuranceCoverage.tenant_id == tenant_id,
                    AssuranceCoverage.risk_id == risk_id,
                )
                .order_by(AssuranceCoverage.obtained_on.desc())
            )
        )

    # -- plans -----------------------------------------------------------------

    def add_proposal(self, proposal: PlanProposal) -> PlanProposal:
        self.session.add(proposal)
        self.session.flush()
        return proposal

    def get_proposal(self, tenant_id: str, proposal_id: str) -> PlanProposal | None:
        return self.session.scalar(
            select(PlanProposal).where(
                PlanProposal.tenant_id == tenant_id,
                PlanProposal.proposal_id == proposal_id,
            )
        )

    def list_proposals(self, tenant_id: str) -> list[PlanProposal]:
        return list(
            self.session.scalars(
                select(PlanProposal)
                .where(PlanProposal.tenant_id == tenant_id)
                .order_by(PlanProposal.name, PlanProposal.version)
            )
        )

    def add_plan(self, plan: AuditPlan) -> AuditPlan:
        self.session.add(plan)
        self.session.flush()
        return plan
