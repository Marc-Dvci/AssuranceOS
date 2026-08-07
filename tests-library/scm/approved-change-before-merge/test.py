from __future__ import annotations

from datetime import datetime
from typing import Any


def execute(*, datasets: dict[str, list[dict[str, Any]]], parameters: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    period_start = datetime.fromisoformat(context["period_start"] + "T00:00:00+00:00")
    period_end = datetime.fromisoformat(context["period_end"] + "T23:59:59.999999+00:00")
    required_approvals = int(parameters["required_approvals"])
    tickets = {item["ticket_id"]: item for item in datasets["change_tickets"]}
    approved_exceptions = {
        item["exception_key"]: item
        for item in datasets["approved_exceptions"]
        if item["active"]
    }
    rows: list[dict[str, Any]] = []
    exceptions: list[dict[str, Any]] = []
    for pull_request in datasets["pull_requests"]:
        merged_at = datetime.fromisoformat(pull_request["merged_at"].replace("Z", "+00:00"))
        if not period_start <= merged_at <= period_end:
            continue
        ticket = tickets.get(pull_request.get("change_ticket"))
        exception = approved_exceptions.get(pull_request.get("exception_key"))
        approvals_ok = int(pull_request["approvals"]) >= required_approvals
        ticket_ok = bool(ticket and ticket["status"] == "Approved")
        if exception:
            classification = "approved_exception"
        elif approvals_ok and ticket_ok:
            classification = "effective"
        else:
            classification = "control_exception"
        row = {
            "pull_request_id": pull_request["pull_request_id"],
            "repository": pull_request["repository"],
            "merged_at": pull_request["merged_at"],
            "approvals": pull_request["approvals"],
            "change_ticket": pull_request.get("change_ticket"),
            "ticket_status": ticket["status"] if ticket else None,
            "classification": classification,
        }
        rows.append(row)
        if classification == "control_exception":
            evidence_ids = [value for value in [pull_request.get("evidence_id"), ticket.get("evidence_id") if ticket else None] if value]
            reasons = []
            if not approvals_ok:
                reasons.append(f"received {pull_request['approvals']} approval(s); {required_approvals} required")
            if not ticket_ok:
                reasons.append("approved change ticket was not found")
            exceptions.append({
                "exception_key": f"SCM-01:{pull_request['repository']}:{pull_request['pull_request_id']}",
                "subject_ref": f"github:{pull_request['repository']}#{pull_request['pull_request_id']}",
                "classification": "unapproved_change",
                "severity": "high",
                "status": "open",
                "reason": "; ".join(reasons),
                "attributes": row,
                "evidence_ids": evidence_ids,
            })
    return {
        "conclusion": "ineffective" if exceptions else "effective",
        "rows": rows,
        "exceptions": exceptions,
        "limitations": [],
    }
