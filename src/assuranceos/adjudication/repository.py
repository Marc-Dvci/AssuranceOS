"""Canonical reads and writes for the adjudication lifecycle."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from assuranceos.db.models import (
    ApprovalDecision,
    ControlTestException,
    Finding,
    ManagementResponse,
    RemediationAction,
    Retest,
)


class AdjudicationRepository:
    def __init__(self, session: Session):
        self.session = session

    # -- findings --------------------------------------------------------------

    def add_finding(self, finding: Finding) -> Finding:
        self.session.add(finding)
        self.session.flush()
        return finding

    def get_finding(self, tenant_id: str, finding_id: str) -> Finding | None:
        return self.session.scalar(
            select(Finding).where(
                Finding.tenant_id == tenant_id, Finding.finding_id == finding_id
            )
        )

    def find_by_code(self, tenant_id: str, engagement_id: str, code: str) -> Finding | None:
        return self.session.scalar(
            select(Finding)
            .where(
                Finding.tenant_id == tenant_id,
                Finding.engagement_id == engagement_id,
                Finding.code == code,
            )
            .order_by(Finding.version.desc())
        )

    def list_findings(self, tenant_id: str, engagement_id: str | None = None) -> list[Finding]:
        statement = select(Finding).where(Finding.tenant_id == tenant_id)
        if engagement_id:
            statement = statement.where(Finding.engagement_id == engagement_id)
        return list(self.session.scalars(statement.order_by(Finding.code, Finding.version)))

    def list_findings_by_code(self, tenant_id: str, code: str) -> list[Finding]:
        return list(
            self.session.scalars(
                select(Finding)
                .where(Finding.tenant_id == tenant_id, Finding.code == code)
                .order_by(Finding.created_at)
            )
        )

    # -- decisions -------------------------------------------------------------

    def add_decision(self, decision: ApprovalDecision) -> ApprovalDecision:
        self.session.add(decision)
        self.session.flush()
        return decision

    def decisions(self, tenant_id: str, finding_id: str) -> list[ApprovalDecision]:
        return list(
            self.session.scalars(
                select(ApprovalDecision)
                .where(
                    ApprovalDecision.tenant_id == tenant_id,
                    ApprovalDecision.finding_id == finding_id,
                )
                .order_by(ApprovalDecision.decided_at, ApprovalDecision.decision_id)
            )
        )

    # -- remediation -----------------------------------------------------------

    def add_action(self, action: RemediationAction) -> RemediationAction:
        self.session.add(action)
        self.session.flush()
        return action

    def get_action(self, tenant_id: str, action_id: str) -> RemediationAction | None:
        return self.session.scalar(
            select(RemediationAction).where(
                RemediationAction.tenant_id == tenant_id,
                RemediationAction.action_id == action_id,
            )
        )

    def open_action_for(self, tenant_id: str, finding_id: str) -> RemediationAction | None:
        """The action already opened for this finding, if any.

        Remediation is opened at most once per finding. Looking it up by finding
        rather than by idempotency key means a replay with a *different* key still
        cannot open a second ticket.
        """
        return self.session.scalar(
            select(RemediationAction)
            .where(
                RemediationAction.tenant_id == tenant_id,
                RemediationAction.finding_id == finding_id,
            )
            .order_by(RemediationAction.created_at)
        )

    def actions(self, tenant_id: str, finding_id: str) -> list[RemediationAction]:
        return list(
            self.session.scalars(
                select(RemediationAction)
                .where(
                    RemediationAction.tenant_id == tenant_id,
                    RemediationAction.finding_id == finding_id,
                )
                .order_by(RemediationAction.created_at)
            )
        )

    # -- management responses --------------------------------------------------

    def add_response(self, response: ManagementResponse) -> ManagementResponse:
        self.session.add(response)
        self.session.flush()
        return response

    def responses(self, tenant_id: str, finding_id: str) -> list[ManagementResponse]:
        return list(
            self.session.scalars(
                select(ManagementResponse)
                .where(
                    ManagementResponse.tenant_id == tenant_id,
                    ManagementResponse.finding_id == finding_id,
                )
                .order_by(ManagementResponse.version)
            )
        )

    # -- retests ---------------------------------------------------------------

    def add_retest(self, retest: Retest) -> Retest:
        self.session.add(retest)
        self.session.flush()
        return retest

    def retests(self, tenant_id: str, action_id: str) -> list[Retest]:
        return list(
            self.session.scalars(
                select(Retest)
                .where(Retest.tenant_id == tenant_id, Retest.action_id == action_id)
                .order_by(Retest.created_at)
            )
        )

    # -- source exceptions -----------------------------------------------------

    def exceptions_for_run(self, tenant_id: str, run_id: str) -> list[ControlTestException]:
        return list(
            self.session.scalars(
                select(ControlTestException)
                .where(
                    ControlTestException.tenant_id == tenant_id,
                    ControlTestException.run_id == run_id,
                )
                .order_by(ControlTestException.exception_key)
            )
        )
