from __future__ import annotations

from datetime import date, datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class LaunchMode(StrEnum):
    APPROVAL_REQUIRED = "approval_required"
    PREFLIGHT_THEN_APPROVAL = "preflight_then_approval"
    AUTOMATIC = "automatic"


class MissedOccurrencePolicy(StrEnum):
    LAUNCH_ALL = "launch_all"
    LAUNCH_LATEST = "launch_latest"
    SKIP = "skip"


class OverlapPolicy(StrEnum):
    PREVENT = "prevent"
    ALLOW = "allow"


class OccurrenceStatus(StrEnum):
    DUE = "due"
    DEFERRED = "deferred"
    SKIPPED = "skipped"
    PREFLIGHT_BLOCKED = "preflight_blocked"
    WAITING_APPROVAL = "waiting_approval"
    LAUNCHING = "launching"
    LAUNCHED = "launched"
    LAUNCH_FAILED = "launch_failed"
    CANCELLED = "cancelled"


class AuditPeriodRule(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["calendar_months", "rolling_days"] = "calendar_months"
    months: int = Field(default=1, ge=1, le=60)
    days: int = Field(default=30, ge=1, le=3660)
    end_offset_days: int = Field(default=1, ge=0, le=3660)

    @model_validator(mode="after")
    def validate_relevant_field(self) -> "AuditPeriodRule":
        if self.kind == "calendar_months" and self.months < 1:
            raise ValueError("months must be positive")
        if self.kind == "rolling_days" and self.days < 1:
            raise ValueError("days must be positive")
        return self


class BusinessCalendarConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    weekend_days: set[int] = Field(default_factory=lambda: {5, 6})
    holidays: set[date] = Field(default_factory=set)

    @model_validator(mode="after")
    def validate_weekdays(self) -> "BusinessCalendarConfig":
        if any(day < 0 or day > 6 for day in self.weekend_days):
            raise ValueError("weekend_days must use Python weekday numbers 0 through 6")
        return self


class BlackoutWindow(BaseModel):
    model_config = ConfigDict(extra="forbid")

    start: date
    end: date
    behavior: Literal["delay", "skip"] = "delay"
    reason: str = Field(default="configured blackout window", min_length=1, max_length=500)

    @model_validator(mode="after")
    def validate_order(self) -> "BlackoutWindow":
        if self.end < self.start:
            raise ValueError("blackout window end must not precede start")
        return self


class BlackoutPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    windows: list[BlackoutWindow] = Field(default_factory=list)


class PreflightContext(BaseModel):
    model_config = ConfigDict(extra="forbid")

    connector_health: dict[str, str] = Field(default_factory=dict)
    available_budget_usd: float | None = Field(default=None, ge=0)
    available_competencies: set[str] = Field(default_factory=set)
    independence_conflicts: set[str] = Field(default_factory=set)
    attributes: dict[str, Any] = Field(default_factory=dict)


class PreflightCheck(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str
    passed: bool
    message: str
    details: dict[str, Any] = Field(default_factory=dict)


class PreflightReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    passed: bool
    checked_at: datetime
    checks: list[PreflightCheck]


class OccurrenceSnapshot(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    occurrence_id: str
    schedule_id: str
    engagement_id: str | None
    nominal_due: datetime
    eligible_at: datetime
    period_start: date
    period_end: date
    status: OccurrenceStatus
    decision_reason: str | None
    launch_attempts: int
    last_error: str | None
    preflight_result_json: dict[str, Any]


class ScheduleSimulationItem(BaseModel):
    nominal_due: datetime
    eligible_at: datetime
    period_start: date
    period_end: date
    blackout_action: Literal["none", "delay", "skip"] = "none"
    blackout_reason: str | None = None


class ScheduleEvaluationSummary(BaseModel):
    tenant_id: str
    evaluated_at: datetime
    schedules_evaluated: int = 0
    occurrences_created: int = 0
    launched: int = 0
    waiting_approval: int = 0
    blocked: int = 0
    skipped: int = 0
    deferred: int = 0
    failures: int = 0
    occurrence_ids: list[str] = Field(default_factory=list)


class OccurrenceDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    actor_id: str = Field(min_length=1, max_length=128)
    reason: str = Field(min_length=1, max_length=4000)
    preflight_context: PreflightContext = Field(default_factory=PreflightContext)
