from __future__ import annotations

from calendar import monthrange
from datetime import date, datetime, timedelta

from dateutil.relativedelta import relativedelta

from .definitions import AuditPeriodRule


class AuditPeriodCalculator:
    def calculate(self, due_local: datetime, rule: AuditPeriodRule) -> tuple[date, date]:
        period_end = due_local.date() - timedelta(days=rule.end_offset_days)
        if rule.kind == "rolling_days":
            return period_end - timedelta(days=rule.days - 1), period_end

        first_of_end_month = period_end.replace(day=1)
        period_start = first_of_end_month - relativedelta(months=rule.months - 1)
        last_day = monthrange(period_end.year, period_end.month)[1]
        if period_end.day != last_day:
            # A calendar-month period is complete only through the last completed month.
            previous_month_end = first_of_end_month - timedelta(days=1)
            period_end = previous_month_end
            period_start = period_end.replace(day=1) - relativedelta(months=rule.months - 1)
        return period_start, period_end
