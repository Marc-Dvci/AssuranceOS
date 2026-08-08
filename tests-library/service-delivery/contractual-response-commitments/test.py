from __future__ import annotations

from datetime import datetime
from typing import Any


def _moment(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _in_force(commitment: dict[str, Any], at: datetime) -> bool:
    """Whether a contractual commitment governs an incident opened at ``at``."""
    starts = _moment(commitment["effective_from"] + "T00:00:00+00:00")
    if at < starts:
        return False
    ends = commitment.get("effective_to")
    if not ends:
        return True
    return at <= _moment(ends + "T23:59:59.999999+00:00")


def execute(
    *, datasets: dict[str, list[dict[str, Any]]], parameters: dict[str, Any], context: dict[str, Any]
) -> dict[str, Any]:
    """Test whether contractual response commitments were met, and whether the
    organisation is in a position to know.

    Two questions, deliberately kept apart. Whether each incident met the target
    its *contract* sets is the operating question. Whether the procedure and the
    ticketing configuration carry that same target is the design question, and it
    is the one that decides whether the operating result means anything: an
    incident marked "met" against a target copied from a superseded clause is not
    evidence of compliance, it is evidence that nobody is measuring.
    """
    period_start = _moment(context["period_start"] + "T00:00:00+00:00")
    period_end = _moment(context["period_end"] + "T23:59:59.999999+00:00")
    priorities = set(parameters["in_scope_priorities"])

    commitments = datasets["contract_commitments"]
    documented = {
        (item["priority"], item.get("scope") or "all"): item
        for item in datasets["documented_targets"]
    }

    rows: list[dict[str, Any]] = []
    exceptions: list[dict[str, Any]] = []

    # -- design: does the internal target match the contractual one? -----------
    # Checked per commitment in force at the end of the period, because a target
    # that was corrected mid-period is a different condition from one that was
    # never corrected at all.
    operated_by_priority: dict[str, set[int]] = {}
    for incident in datasets["incidents"]:
        operated_by_priority.setdefault(incident["priority"], set()).add(
            int(incident["operated_target_hours"])
        )

    for commitment in commitments:
        if commitment["priority"] not in priorities:
            continue
        if not _in_force(commitment, period_end):
            continue
        contractual = int(commitment["response_hours"])
        reference = documented.get((commitment["priority"], "all"))
        documented_hours = int(reference["response_hours"]) if reference else None
        operated = sorted(operated_by_priority.get(commitment["priority"], set()))
        stale = [value for value in operated if value != contractual]
        if documented_hours == contractual and not stale:
            continue
        detail = []
        if documented_hours is not None and documented_hours != contractual:
            detail.append(
                f"the documented procedure states {documented_hours} hours "
                f"({reference['document_ref']})"
            )
        if stale:
            detail.append(
                "the ticketing configuration applied "
                + ", ".join(f"{value} hours" for value in stale)
                + " to incidents in the period"
            )
        exceptions.append(
            {
                "exception_key": f"SLA-01:design:{commitment['customer']}:{commitment['priority']}",
                "subject_ref": f"{commitment['contract_ref']}:{commitment['priority']}",
                "classification": "control_design_gap",
                "severity": "high",
                "status": "open",
                "reason": (
                    f"{commitment['customer']} is owed a {contractual}-hour "
                    f"{commitment['priority']} response under {commitment['contract_ref']} "
                    f"effective {commitment['effective_from']}, but "
                    + "; and ".join(detail)
                    + ". Incidents are therefore measured against a superseded target."
                ),
                "attributes": {
                    "customer": commitment["customer"],
                    "priority": commitment["priority"],
                    "contractual_hours": contractual,
                    "documented_hours": documented_hours,
                    "operated_hours": operated,
                    "contract_ref": commitment["contract_ref"],
                    "effective_from": commitment["effective_from"],
                    "document_ref": reference["document_ref"] if reference else None,
                },
                "evidence_ids": [
                    value
                    for value in [
                        commitment.get("evidence_id"),
                        reference.get("evidence_id") if reference else None,
                    ]
                    if value
                ],
            }
        )

    # -- operation: was each in-period incident answered in time? --------------
    credits: dict[str, dict[str, Any]] = {}
    for incident in datasets["incidents"]:
        opened = _moment(incident["opened_at"])
        if not period_start <= opened <= period_end:
            continue
        if incident["priority"] not in priorities:
            continue
        governing = [
            item
            for item in commitments
            if item["customer"] == incident["customer"]
            and item["priority"] == incident["priority"]
            and _in_force(item, opened)
        ]
        if not governing:
            continue
        # If the clause was amended mid-life the latest one in force governs.
        commitment = max(governing, key=lambda item: item["effective_from"])
        target = int(commitment["response_hours"])
        responded = incident.get("first_response_at")
        elapsed = (_moment(responded) - opened).total_seconds() / 3600 if responded else None
        breached = elapsed is None or elapsed > target
        row = {
            "incident_id": incident["incident_id"],
            "customer": incident["customer"],
            "priority": incident["priority"],
            "opened_at": incident["opened_at"],
            "first_response_at": responded,
            "response_hours": None if elapsed is None else round(elapsed, 2),
            "contractual_target_hours": target,
            "operated_target_hours": int(incident["operated_target_hours"]),
            "contract_ref": commitment["contract_ref"],
            "classification": "control_exception" if breached else "effective",
        }
        rows.append(row)
        if not breached:
            continue
        credit_pct = float(commitment.get("credit_pct_per_breach") or 0)
        summary = credits.setdefault(
            incident["customer"],
            {
                "breaches": 0,
                "credit_pct": 0.0,
                "cap_pct": float(commitment.get("credit_cap_pct") or 0),
                "monthly_fee_eur": float(commitment.get("monthly_fee_eur") or 0),
            },
        )
        summary["breaches"] += 1
        summary["credit_pct"] += credit_pct
        exceptions.append(
            {
                "exception_key": f"SLA-01:breach:{incident['incident_id']}",
                "subject_ref": f"jira:{incident['incident_id']}",
                "classification": "sla_breach",
                "severity": "high",
                "status": "open",
                "reason": (
                    f"first response after {row['response_hours']} hours against a "
                    f"contractual target of {target} hours "
                    f"({commitment['contract_ref']}); the ticket records the target as "
                    f"{row['operated_target_hours']} hours and is marked met"
                ),
                "attributes": {
                    **row,
                    "credit_pct_per_breach": credit_pct,
                },
                "evidence_ids": [
                    value
                    for value in [incident.get("evidence_id"), commitment.get("evidence_id")]
                    if value
                ],
            }
        )

    exposure = []
    for customer, summary in sorted(credits.items()):
        applied = min(summary["credit_pct"], summary["cap_pct"]) if summary["cap_pct"] else summary["credit_pct"]
        exposure.append(
            {
                "customer": customer,
                "breaches": summary["breaches"],
                "credit_pct_before_cap": round(summary["credit_pct"], 2),
                "credit_pct_applied": round(applied, 2),
                "monthly_fee_eur": summary["monthly_fee_eur"],
                "credit_value_eur": round(summary["monthly_fee_eur"] * applied / 100, 2),
            }
        )

    return {
        "conclusion": "ineffective" if exceptions else "effective",
        "rows": rows,
        "exceptions": exceptions,
        "metrics": {"service_credit_exposure": exposure},
        "limitations": [
            "Response times are read from the ticketing system's own timestamps; "
            "whether a recorded first response was substantive is not testable from "
            "this population.",
            "Contract coverage windows (business hours versus 24x7) are reported but "
            "not applied: every incident in this population was opened outside "
            "business hours, so applying the window would only widen the breach.",
        ],
    }
