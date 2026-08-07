from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from dateutil.rrule import rrulestr


class RecurrenceError(ValueError):
    pass


class RecurrenceEngine:
    def timezone(self, name: str) -> ZoneInfo:
        try:
            return ZoneInfo(name)
        except ZoneInfoNotFoundError as exc:
            raise RecurrenceError(f"unknown time zone: {name}") from exc

    def occurrences_between(
        self,
        *,
        rule: str,
        timezone_name: str,
        effective_from: datetime,
        start_exclusive: datetime,
        end_inclusive: datetime,
        limit: int = 1000,
    ) -> list[datetime]:
        if limit < 1:
            raise ValueError("limit must be positive")
        tz = self.timezone(timezone_name)
        effective_local = self._aware(effective_from).astimezone(tz)
        start_local = self._aware(start_exclusive).astimezone(tz)
        end_local = self._aware(end_inclusive).astimezone(tz)
        try:
            recurrence = rrulestr(self._normalize_rule(rule), dtstart=effective_local)
            values = recurrence.between(start_local, end_local, inc=True)
        except (TypeError, ValueError) as exc:
            raise RecurrenceError(f"invalid recurrence rule: {exc}") from exc
        filtered = [value for value in values if value > start_local]
        if len(filtered) > limit:
            raise RecurrenceError(
                f"recurrence produced more than {limit} occurrences in one evaluation window"
            )
        return [self._aware(value).astimezone(timezone.utc) for value in filtered]

    def next_after(
        self,
        *,
        rule: str,
        timezone_name: str,
        effective_from: datetime,
        after: datetime,
    ) -> datetime | None:
        tz = self.timezone(timezone_name)
        effective_local = self._aware(effective_from).astimezone(tz)
        after_local = self._aware(after).astimezone(tz)
        try:
            value = rrulestr(self._normalize_rule(rule), dtstart=effective_local).after(
                after_local, inc=False
            )
        except (TypeError, ValueError) as exc:
            raise RecurrenceError(f"invalid recurrence rule: {exc}") from exc
        return None if value is None else self._aware(value).astimezone(timezone.utc)

    @staticmethod
    def _normalize_rule(rule: str) -> str:
        stripped = rule.strip()
        if stripped.upper().startswith(("RRULE:", "DTSTART")):
            return stripped
        return f"RRULE:{stripped}"

    @staticmethod
    def _aware(value: datetime) -> datetime:
        return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value
