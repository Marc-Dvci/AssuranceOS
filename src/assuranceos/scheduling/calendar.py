from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from .definitions import BlackoutPolicy, BusinessCalendarConfig


@dataclass(frozen=True)
class BlackoutResolution:
    eligible_at: datetime
    action: str = "none"
    reason: str | None = None


class BusinessCalendar:
    def __init__(self, config: BusinessCalendarConfig):
        self.config = config

    def is_business_day(self, value: date) -> bool:
        return value.weekday() not in self.config.weekend_days and value not in self.config.holidays

    def next_business_day(self, value: date) -> date:
        candidate = value
        while not self.is_business_day(candidate):
            candidate += timedelta(days=1)
        return candidate

    def resolve_blackout(
        self,
        due_local: datetime,
        policy: BlackoutPolicy,
        timezone: ZoneInfo,
    ) -> BlackoutResolution:
        for window in policy.windows:
            if window.start <= due_local.date() <= window.end:
                if window.behavior == "skip":
                    return BlackoutResolution(due_local, action="skip", reason=window.reason)
                next_day = self.next_business_day(window.end + timedelta(days=1))
                eligible = datetime.combine(
                    next_day, due_local.timetz().replace(tzinfo=None), tzinfo=timezone
                )
                return BlackoutResolution(eligible, action="delay", reason=window.reason)
        return BlackoutResolution(due_local)
