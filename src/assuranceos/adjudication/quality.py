"""The methodology gate.

Approval and quality review answer different questions. Approval asks whether the
organisation stands behind a conclusion; quality review asks whether the work
behind it was performed properly. Collapsing them — which is what a single
"approve" button does — means a badly supported finding and a well supported one
reach the audit committee through the same door.

The checks below are computed from canonical state rather than ticked by a
reviewer, for the same reason the skeptic is deterministic: a checklist a person
fills in is a record of what they believed, and a checklist the system computes is
a record of what was true. The reviewer's judgement is still required — the gate
does not pass without a named reviewer who is not the author — but it is added to
the mechanical checks, not substituted for them.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field
from ..text import counted


class QualityCheck(StrEnum):
    """The mechanical conditions a finding must satisfy to be reviewable."""

    EVIDENCE_CITED = "evidence_cited"
    CONTRADICTIONS_SEARCHED = "contradictions_searched"
    POPULATION_RECONCILED = "population_reconciled"
    CRITERIA_STATED = "criteria_stated"
    CONDITION_OBSERVED = "condition_observed"
    MATERIALITY_ASSESSED = "materiality_assessed"
    SEVERITY_SUPPORTED = "severity_supported"
    LIMITATIONS_DISCLOSED = "limitations_disclosed"
    NOT_SELF_REVIEWED = "not_self_reviewed"


class CheckResult(BaseModel):
    """One check, its verdict, and why."""

    check: QualityCheck
    passed: bool
    detail: str = Field(max_length=2000)


class QualityReviewOutcome(BaseModel):
    """The result of a quality review over one version of a finding."""

    passed: bool
    checks: list[CheckResult]
    content_hash: str
    reviewer_id: str

    @property
    def failures(self) -> list[CheckResult]:
        return [item for item in self.checks if not item.passed]

    @property
    def summary(self) -> str:
        if self.passed:
            return f"{len(self.checks)} quality checks passed"
        names = ", ".join(item.check.value for item in self.failures)
        return f"quality review failed on: {names}"


def evaluate(
    *,
    reviewer_id: str,
    authored_by: str | None,
    severity: str,
    evidence_ids: Sequence[str],
    contradictions: Sequence[Mapping[str, Any]],
    exception_keys: Sequence[str],
    criteria: str,
    observed_condition: str,
    limitations: Sequence[str],
    materiality: Mapping[str, Any] | None,
    skeptic_ran: bool,
    content_hash: str,
) -> QualityReviewOutcome:
    """Run the mechanical checks over a finding's canonical state.

    ``materiality`` is the stored assessment for this content hash, or ``None``
    when there is none. Passing ``None`` fails the gate rather than skipping the
    check: an unassessed finding is not one whose materiality is zero.

    The reviewer's own notes are recorded by the service alongside the outcome.
    They are deliberately not an input here: a note cannot turn a failed
    mechanical check into a pass, and accepting one would suggest it could.
    """
    checks: list[CheckResult] = []

    checks.append(
        CheckResult(
            check=QualityCheck.EVIDENCE_CITED,
            passed=bool(evidence_ids),
            detail=(
                f"{counted(len(evidence_ids), 'evidence record')} cited"
                if evidence_ids
                else "the finding cites no evidence"
            ),
        )
    )

    # A search that found nothing is a pass; a search that never ran is not. The
    # two are indistinguishable from the contradiction list alone, which is why
    # the caller reports whether the skeptic was invoked.
    checks.append(
        CheckResult(
            check=QualityCheck.CONTRADICTIONS_SEARCHED,
            passed=skeptic_ran,
            detail=(
                f"contradiction search ran and recorded {counted(len(contradictions), 'result')}"
                if skeptic_ran
                else "no contradiction search is recorded against this finding"
            ),
        )
    )

    reconciled = bool(exception_keys) or bool(evidence_ids)
    checks.append(
        CheckResult(
            check=QualityCheck.POPULATION_RECONCILED,
            passed=reconciled,
            detail=(
                f"{counted(len(exception_keys), 'exception key')} trace to the tested population"
                if exception_keys
                else "no exception keys; the finding rests on cited evidence alone"
            ),
        )
    )

    checks.append(
        CheckResult(
            check=QualityCheck.CRITERIA_STATED,
            passed=len(criteria.strip()) >= 10,
            detail=(
                "criteria are stated"
                if len(criteria.strip()) >= 10
                else "criteria are missing or too short to identify a standard"
            ),
        )
    )

    checks.append(
        CheckResult(
            check=QualityCheck.CONDITION_OBSERVED,
            passed=len(observed_condition.strip()) >= 10,
            detail=(
                "the observed condition is stated"
                if len(observed_condition.strip()) >= 10
                else "the observed condition is missing or too short"
            ),
        )
    )

    has_materiality = materiality is not None
    checks.append(
        CheckResult(
            check=QualityCheck.MATERIALITY_ASSESSED,
            passed=has_materiality,
            detail=(
                f"materiality scored {materiality.get('score')} "  # type: ignore[union-attr]
                f"({materiality.get('severity_floor')} floor)"  # type: ignore[union-attr]
                if has_materiality
                else "no materiality assessment exists for this version of the finding"
            ),
        )
    )

    # Severity is supported when it is at least the floor materiality computed, or
    # when a recorded override explains why it is lower. A severity below an
    # unexplained floor is the exact case this gate exists to stop.
    if has_materiality:
        from .materiality import severity_rank

        floor = str(materiality.get("severity_floor", "low"))  # type: ignore[union-attr]
        override = materiality.get("override_severity")  # type: ignore[union-attr]
        supported = severity_rank(severity) >= severity_rank(floor) or bool(override)
        detail = (
            f"severity {severity!r} meets the {floor!r} floor"
            if severity_rank(severity) >= severity_rank(floor)
            else (
                f"severity {severity!r} is below the {floor!r} floor under recorded override"
                if override
                else f"severity {severity!r} is below the computed {floor!r} floor "
                "and carries no override"
            )
        )
    else:
        supported = False
        detail = "severity cannot be checked without a materiality assessment"
    checks.append(
        CheckResult(check=QualityCheck.SEVERITY_SUPPORTED, passed=supported, detail=detail)
    )

    # Contradictions that were found and not disclosed are the failure mode; an
    # absence of contradictions needs no limitation.
    undisclosed = bool(contradictions) and not limitations
    checks.append(
        CheckResult(
            check=QualityCheck.LIMITATIONS_DISCLOSED,
            passed=not undisclosed,
            detail=(
                f"{counted(len(contradictions), 'contradiction')} found but no limitation is disclosed"
                if undisclosed
                else f"{counted(len(limitations), 'limitation')} disclosed"
            ),
        )
    )

    independent = bool(authored_by) and reviewer_id.strip().lower() != authored_by.strip().lower()
    checks.append(
        CheckResult(
            check=QualityCheck.NOT_SELF_REVIEWED,
            passed=independent,
            detail=(
                f"reviewer {reviewer_id!r} is independent of author {authored_by!r}"
                if independent
                else (
                    f"reviewer {reviewer_id!r} authored the finding"
                    if authored_by
                    else "the finding records no author, so independence cannot be established"
                )
            ),
        )
    )

    return QualityReviewOutcome(
        passed=all(item.passed for item in checks),
        checks=checks,
        content_hash=content_hash,
        reviewer_id=reviewer_id,
    )
