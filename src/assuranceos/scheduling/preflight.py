from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session

from assuranceos.db.models import (
    AuditPlan,
    AuditSchedule,
    Engagement,
    EngagementTemplate,
    ScheduleOccurrence,
    Tenant,
)

from .definitions import PreflightCheck, PreflightContext, PreflightReport


ACTIVE_ENGAGEMENT_STATUSES = ("planned", "running", "waiting_approval", "blocked")


class PreflightEvaluator:
    def evaluate(
        self,
        session: Session,
        *,
        schedule: AuditSchedule,
        template: EngagementTemplate,
        occurrence: ScheduleOccurrence,
        context: PreflightContext,
        checked_at: datetime,
    ) -> PreflightReport:
        plan = session.get(AuditPlan, schedule.plan_id)
        tenant = session.get(Tenant, schedule.tenant_id)
        checks = [
            self._check(
                "tenant_active",
                tenant is not None and tenant.status == "active",
                "Tenant must be active.",
            ),
            self._check(
                "plan_approved",
                plan is not None and plan.status == "approved",
                "Audit plan must be approved.",
            ),
            self._check("schedule_active", schedule.status == "active", "Schedule must be active."),
            self._check(
                "template_released",
                template.status in {"released", "approved"},
                "Engagement template must be released.",
            ),
            self._check(
                "workflow_present",
                bool(template.workflow_definition_json),
                "Template must contain a workflow definition.",
            ),
        ]
        policy = {**template.preflight_policy_json, **schedule.preflight_policy_json}
        checks.extend(self._connector_checks(policy, context))
        checks.extend(self._competency_checks(policy, context))
        checks.append(self._budget_check(policy, context))
        checks.append(self._independence_check(policy, context))
        checks.append(self._concurrency_check(session, schedule, occurrence))
        checks.append(self._overlap_check(session, schedule, occurrence))
        return PreflightReport(
            passed=all(check.passed for check in checks), checked_at=checked_at, checks=checks
        )

    @staticmethod
    def _check(code: str, passed: bool, message: str, **details: Any) -> PreflightCheck:
        return PreflightCheck(code=code, passed=passed, message=message, details=details)

    def _connector_checks(
        self, policy: dict[str, Any], context: PreflightContext
    ) -> list[PreflightCheck]:
        checks = []
        for connector in policy.get("required_connectors", []):
            state = context.connector_health.get(connector)
            checks.append(
                self._check(
                    f"connector:{connector}",
                    state == "healthy",
                    f"Required connector {connector!r} must be healthy.",
                    observed_state=state,
                )
            )
        return checks

    def _competency_checks(
        self, policy: dict[str, Any], context: PreflightContext
    ) -> list[PreflightCheck]:
        required = set(policy.get("required_competencies", []))
        missing = sorted(required - context.available_competencies)
        return [
            self._check(
                "competencies_available",
                not missing,
                "Required reviewer and agent competencies must be available.",
                missing=missing,
            )
        ]

    def _budget_check(self, policy: dict[str, Any], context: PreflightContext) -> PreflightCheck:
        required = policy.get("estimated_cost_usd")
        passed = required is None or (
            context.available_budget_usd is not None
            and context.available_budget_usd >= float(required)
        )
        return self._check(
            "budget_available",
            passed,
            "Configured execution budget must be available.",
            required_usd=required,
            available_usd=context.available_budget_usd,
        )

    def _independence_check(
        self, policy: dict[str, Any], context: PreflightContext
    ) -> PreflightCheck:
        protected_roles = set(policy.get("independence_roles", []))
        conflicts = sorted(protected_roles & context.independence_conflicts)
        return self._check(
            "independence_clear",
            not conflicts,
            "No configured independence conflict may be present.",
            conflicts=conflicts,
        )

    def _concurrency_check(
        self, session: Session, schedule: AuditSchedule, occurrence: ScheduleOccurrence
    ) -> PreflightCheck:
        filters = [
            Engagement.tenant_id == schedule.tenant_id,
            Engagement.template_id == schedule.template_id,
            Engagement.status.in_(ACTIVE_ENGAGEMENT_STATUSES),
        ]
        if occurrence.engagement_id is not None:
            filters.append(Engagement.engagement_id != occurrence.engagement_id)
        active = session.scalar(
            select(func.count(Engagement.engagement_id)).where(*filters)
        ) or 0
        return self._check(
            "concurrency_limit",
            active < schedule.max_concurrent_engagements,
            "Concurrent engagement limit must not be exceeded.",
            active=active,
            limit=schedule.max_concurrent_engagements,
        )

    def _overlap_check(
        self, session: Session, schedule: AuditSchedule, occurrence: ScheduleOccurrence
    ) -> PreflightCheck:
        if schedule.overlap_policy == "allow":
            return self._check("period_overlap", True, "Period overlap is allowed by policy.")
        filters = [
            Engagement.tenant_id == schedule.tenant_id,
            Engagement.template_id == schedule.template_id,
            Engagement.status.in_(ACTIVE_ENGAGEMENT_STATUSES),
            and_(
                Engagement.period_start <= occurrence.period_end,
                Engagement.period_end >= occurrence.period_start,
            ),
        ]
        if occurrence.engagement_id is not None:
            filters.append(Engagement.engagement_id != occurrence.engagement_id)
        overlap = session.scalar(
            select(Engagement.engagement_id).where(*filters).limit(1)
        )
        return self._check(
            "period_overlap",
            overlap is None,
            "An equivalent active engagement must not overlap this audit period.",
            conflicting_engagement_id=overlap,
        )
