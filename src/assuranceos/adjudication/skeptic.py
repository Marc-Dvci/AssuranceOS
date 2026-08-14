"""Contradiction search over a proposed finding.

A deterministic control test says "this row breaks the rule". That is not yet a
finding: the exception may be a registered exception, may fall outside the audit
period, or may be covered by a compensating control. Promoting every exception to
a finding is how an automated audit loses the room, and it is the failure mode
this module exists to prevent.

The search is deterministic and evidence-driven on purpose. A model may *also*
review a finding, but the structural contradictions are the ones that must never
depend on a model's mood, so they are computed from canonical records here.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date
from typing import Any

from ..text import counted
from .definitions import (
    Contradiction,
    ContradictionKind,
    ProposedFinding,
    SkepticVerdict,
)


def _as_date(value: Any) -> date | None:
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value[:10])
        except ValueError:
            return None
    return None


class SkepticReviewer:
    """Searches for reasons a proposed finding should not stand.

    Constructed with the canonical context an auditor would consult: the register
    of approved exceptions, the audit period, the compensating controls on record,
    and findings already raised. Each is optional; absent context simply produces
    no contradiction of that kind rather than a false pass.
    """

    def __init__(
        self,
        *,
        approved_exceptions: Sequence[Mapping[str, Any]] = (),
        period_start: date | None = None,
        period_end: date | None = None,
        compensating_controls: Sequence[Mapping[str, Any]] = (),
        existing_codes: Sequence[str] = (),
    ):
        self.approved_exceptions = list(approved_exceptions)
        self.period_start = period_start
        self.period_end = period_end
        self.compensating_controls = list(compensating_controls)
        self.existing_codes = set(existing_codes)

    def review(
        self,
        finding: ProposedFinding,
        *,
        exception_rows: Sequence[Mapping[str, Any]] = (),
    ) -> SkepticVerdict:
        """Return a verdict over the finding and the rows that produced it."""
        contradictions: list[Contradiction] = []
        contradictions.extend(self._approved_exceptions(exception_rows))
        contradictions.extend(self._out_of_period(exception_rows))
        contradictions.extend(self._compensating(exception_rows))
        contradictions.extend(self._duplicate(finding))

        # A finding stands only if at least one exception survives the search.
        # Counting survivors rather than contradictions matters: a finding built
        # from five exceptions where three are registered is still a finding.
        subjects = {str(row.get("subject_ref") or row.get("exception_key")) for row in exception_rows}
        contradicted = {
            item.subject_ref
            for item in contradictions
            if item.kind is not ContradictionKind.DUPLICATE_OF_EXISTING
        }
        surviving = subjects - contradicted

        if not exception_rows:
            # Nothing to contradict; the finding rests on whatever evidence it
            # cited and the human gate remains the control.
            return SkepticVerdict(supported=True, contradictions=contradictions)

        duplicate = any(
            item.kind is ContradictionKind.DUPLICATE_OF_EXISTING for item in contradictions
        )
        if duplicate:
            return SkepticVerdict(
                supported=False,
                contradictions=contradictions,
                rationale=f"{finding.code} is already raised in this engagement",
            )
        if not surviving:
            return SkepticVerdict(
                supported=False,
                contradictions=contradictions,
                rationale=(
                    f"all {counted(len(subjects), 'exception')} behind {finding.code} are "
                    "explained by canonical records"
                ),
            )
        return SkepticVerdict(
            supported=True,
            contradictions=contradictions,
            rationale=(
                f"{len(surviving)} of {counted(len(subjects), 'exception')} remain unexplained"
            ),
        )

    # -- individual searches ---------------------------------------------------

    def _approved_exceptions(
        self, rows: Sequence[Mapping[str, Any]]
    ) -> list[Contradiction]:
        found: list[Contradiction] = []
        for row in rows:
            subject = str(row.get("subject_ref") or row.get("exception_key") or "")
            for approval in self.approved_exceptions:
                if str(approval.get("subject_ref")) != subject:
                    continue
                expires = _as_date(approval.get("expires_on"))
                # An expired waiver is not a waiver. Treating it as one is how a
                # stale exception register quietly suppresses real findings.
                if expires and self.period_end and expires < self.period_end:
                    continue
                found.append(
                    Contradiction(
                        kind=ContradictionKind.APPROVED_EXCEPTION,
                        subject_ref=subject,
                        detail=(
                            f"{subject} is covered by approved exception "
                            f"{approval.get('reference', 'unknown')}"
                            + (f", valid to {expires.isoformat()}" if expires else "")
                        ),
                        evidence_ids=[str(approval["evidence_id"])]
                        if approval.get("evidence_id")
                        else [],
                    )
                )
                break
        return found

    def _out_of_period(self, rows: Sequence[Mapping[str, Any]]) -> list[Contradiction]:
        if not (self.period_start and self.period_end):
            return []
        found: list[Contradiction] = []
        for row in rows:
            occurred = _as_date(
                (row.get("attributes") or {}).get("occurred_on")
                if isinstance(row.get("attributes"), Mapping)
                else None
            ) or _as_date(row.get("occurred_on"))
            if occurred is None:
                continue
            if self.period_start <= occurred <= self.period_end:
                continue
            subject = str(row.get("subject_ref") or row.get("exception_key") or "")
            found.append(
                Contradiction(
                    kind=ContradictionKind.OUT_OF_PERIOD,
                    subject_ref=subject,
                    detail=(
                        f"{subject} occurred on {occurred.isoformat()}, outside the "
                        f"audit period {self.period_start.isoformat()} to "
                        f"{self.period_end.isoformat()}"
                    ),
                )
            )
        return found

    def _compensating(self, rows: Sequence[Mapping[str, Any]]) -> list[Contradiction]:
        found: list[Contradiction] = []
        for row in rows:
            subject = str(row.get("subject_ref") or row.get("exception_key") or "")
            for control in self.compensating_controls:
                covered = control.get("covers_subjects") or []
                if subject not in {str(item) for item in covered}:
                    continue
                # A compensating control only compensates if someone tested it.
                if not control.get("tested_effective"):
                    continue
                found.append(
                    Contradiction(
                        kind=ContradictionKind.COMPENSATING_CONTROL,
                        subject_ref=subject,
                        detail=(
                            f"{subject} is covered by compensating control "
                            f"{control.get('control_ref', 'unknown')}, tested effective"
                        ),
                        evidence_ids=[str(control["evidence_id"])]
                        if control.get("evidence_id")
                        else [],
                    )
                )
                break
        return found

    def _duplicate(self, finding: ProposedFinding) -> list[Contradiction]:
        if finding.code not in self.existing_codes:
            return []
        return [
            Contradiction(
                kind=ContradictionKind.DUPLICATE_OF_EXISTING,
                subject_ref=finding.code,
                detail=f"a finding with code {finding.code} already exists in this engagement",
            )
        ]
