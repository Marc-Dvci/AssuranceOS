from __future__ import annotations

import json
from pathlib import Path
from typing import Any


REQUIRED_APPROVALS = 1


def run_scm_population_test(demo_root: Path) -> dict[str, Any]:
    """Reproducible population test for SCM-01: approved changes before merge."""
    prs = json.loads((demo_root / "sources/github/pull_requests.json").read_text())
    tickets = json.loads((demo_root / "sources/jira/change_tickets.json").read_text())
    exceptions = json.loads((demo_root / "sources/governance/approved_exceptions.json").read_text())

    ticket_by_id = {item["ticket_id"]: item for item in tickets}
    exception_keys = {item["exception_key"] for item in exceptions if item["active"]}

    results: list[dict[str, Any]] = []
    for pr in prs:
        ticket = ticket_by_id.get(pr.get("change_ticket"))
        exception_key = pr.get("exception_key")
        approved = len(pr.get("approvals", [])) >= REQUIRED_APPROVALS
        ticket_approved = bool(ticket and ticket.get("status") == "Approved")
        within_period = pr["merged_at"].startswith("2026-07")

        if not within_period:
            status = "out_of_period"
            is_exception = False
        elif exception_key and exception_key in exception_keys:
            status = "approved_exception"
            is_exception = False
        elif approved and ticket_approved:
            status = "effective"
            is_exception = False
        else:
            status = "control_exception"
            is_exception = True

        results.append(
            {
                "pull_request_id": pr["pull_request_id"],
                "repository": pr["repository"],
                "merged_at": pr["merged_at"],
                "approvals": len(pr.get("approvals", [])),
                "change_ticket": pr.get("change_ticket"),
                "ticket_status": ticket.get("status") if ticket else None,
                "exception_key": exception_key,
                "classification": status,
                "is_exception": is_exception,
            }
        )

    in_period = [r for r in results if r["classification"] != "out_of_period"]
    exceptions_found = [r for r in in_period if r["is_exception"]]
    return {
        "test_id": "SCM-01",
        "population_count": len(in_period),
        "reconciled_count": len(in_period),
        "exception_count": len(exceptions_found),
        "population_complete": True,
        "conclusion": "ineffective" if exceptions_found else "effective",
        "exceptions": exceptions_found,
        "all_results": results,
        "logic_version": "1.0.0",
    }
