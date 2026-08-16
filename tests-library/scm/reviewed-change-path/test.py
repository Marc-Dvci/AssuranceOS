from __future__ import annotations

from datetime import datetime
from typing import Any


def _moment(value: str) -> datetime:
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


def execute(
    *,
    datasets: dict[str, list[dict[str, Any]]],
    parameters: dict[str, Any],
    context: dict[str, Any],
) -> dict[str, Any]:
    """Did every change that reached the branch get there through review.

    The order of the three questions matters, and it is the order an auditor
    would ask them in:

    1. was the association established at all? If not this row is a limitation
       and the procedure says so. Treating an undetermined association as "no
       pull request" would manufacture exceptions out of a collection budget;
       treating it as a pass would do something worse.
    2. did a *merged* pull request carry the commit? An open or closed-unmerged
       pull request is not a compensating control. Somebody opening a pull
       request that never merged, while the code reached the branch by another
       route, is the failure this procedure exists to find, and counting it as
       mitigation would invert the result.
    3. did that pull request carry the required approvals? Only asked when the
       first two are satisfied, so an unreviewed change is reported as
       unreviewed rather than as under-approved.
    """

    period_start = _moment(context["period_start"] + "T00:00:00+00:00")
    period_end = _moment(context["period_end"] + "T23:59:59.999999+00:00")
    required_approvals = int(parameters["required_approvals"])
    reviews = {item["commit_sha"]: item for item in datasets.get("commit_reviews", [])}

    rows: list[dict[str, Any]] = []
    exceptions: list[dict[str, Any]] = []
    limitations: list[str] = []
    undetermined_association = 0
    undetermined_approvals = 0

    for commit in datasets["commits"]:
        committed_at = _moment(commit["committed_at"])
        if not period_start <= committed_at <= period_end:
            continue
        sha = commit["commit_sha"]
        repository = commit["repository"]
        review = reviews.get(sha)
        evidence_ids = [
            value
            for value in [commit.get("evidence_id"), (review or {}).get("evidence_id")]
            if value
        ]

        if review is None or not review.get("association_determined"):
            undetermined_association += 1
            rows.append(
                {
                    "commit_sha": sha,
                    "repository": repository,
                    "committed_at": commit["committed_at"],
                    "author_login": commit.get("author_login"),
                    "merged_pull_request": None,
                    "approvals": None,
                    "classification": "not_determined",
                }
            )
            continue

        merged = review.get("merged_pull_request")
        states = [str(item) for item in review.get("pull_request_states") or []]
        approvals = review.get("approvals")
        approvals_determined = bool(review.get("approvals_determined"))

        if merged is None:
            classification = "control_exception"
            if states:
                reason = (
                    "no merged pull request carried this change; the associated "
                    f"pull request(s) are {', '.join(sorted(set(states)))}, and an "
                    "unmerged pull request is not a compensating control"
                )
            else:
                reason = (
                    "no pull request is associated with this commit; it reached "
                    "the default branch directly"
                )
            exceptions.append(
                {
                    "exception_key": f"SCM-02:{repository}:{sha}",
                    "subject_ref": f"github:{repository}@{sha}",
                    "classification": "unreviewed_change",
                    "severity": "high",
                    "status": "open",
                    "reason": reason,
                    "attributes": {
                        "commit_sha": sha,
                        "repository": repository,
                        "committed_at": commit["committed_at"],
                        "author_login": commit.get("author_login"),
                        "pull_request_states": states,
                    },
                    "evidence_ids": evidence_ids,
                }
            )
        elif required_approvals > 0 and not approvals_determined:
            undetermined_approvals += 1
            classification = "not_determined"
        elif required_approvals > 0 and int(approvals or 0) < required_approvals:
            classification = "control_exception"
            exceptions.append(
                {
                    "exception_key": f"SCM-02:{repository}:{sha}",
                    "subject_ref": f"github:{repository}@{sha}",
                    "classification": "unapproved_change",
                    "severity": "high",
                    "status": "open",
                    "reason": (
                        f"merged through pull request #{merged} with "
                        f"{int(approvals or 0)} approval(s); {required_approvals} required"
                    ),
                    "attributes": {
                        "commit_sha": sha,
                        "repository": repository,
                        "committed_at": commit["committed_at"],
                        "merged_pull_request": merged,
                        "approvals": approvals,
                    },
                    "evidence_ids": evidence_ids,
                }
            )
        else:
            classification = "effective"

        if merged is not None:
            rows.append(
                {
                    "commit_sha": sha,
                    "repository": repository,
                    "committed_at": commit["committed_at"],
                    "author_login": commit.get("author_login"),
                    "merged_pull_request": merged,
                    "approvals": approvals,
                    "classification": classification,
                }
            )
        else:
            rows.append(
                {
                    "commit_sha": sha,
                    "repository": repository,
                    "committed_at": commit["committed_at"],
                    "author_login": commit.get("author_login"),
                    "merged_pull_request": None,
                    "approvals": None,
                    "classification": classification,
                }
            )

    if undetermined_association:
        limitations.append(
            f"{undetermined_association} commit(s) had no determined pull-request "
            "association within the collection's lookup budget and are reported as "
            "neither effective nor exceptions"
        )
    if undetermined_approvals:
        limitations.append(
            f"{undetermined_approvals} merged pull request(s) had no determined "
            "approval count within the collection's lookup budget"
        )

    if exceptions:
        conclusion = "ineffective"
    elif limitations:
        # Nothing failed and not everything was seen. Calling that effective is
        # the single most common way a control test overstates itself.
        conclusion = "insufficient_evidence"
    elif not rows:
        conclusion = "not_applicable"
    else:
        conclusion = "effective"

    return {
        "conclusion": conclusion,
        "rows": rows,
        "exceptions": exceptions,
        "limitations": limitations,
    }
