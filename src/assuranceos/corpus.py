"""Read the Asteria evidence corpus and project it into control-test datasets.

Between a source system and a control test there is a step that audit software
usually leaves implicit: something has to turn *what the system exported* into
*the population the test is defined over*. Doing that silently is how a test
ends up measuring a convenient subset instead of the control.

So the projection is explicit and it is small. This module:

* enumerates every file the connected systems exposed, hashing each one as an
  evidence reference before anything reads it;
* projects the declared columns — and only the declared columns — into the row
  shapes the signed test manifests specify, because the manifests set
  ``additionalProperties: false`` and a projection that carries extra fields is
  a projection nobody validated;
* attaches the evidence identifier of the file each row came from, so a single
  exception in a result can be traced to the export it was read out of;
* refuses to invent a population. Every row here exists in a file on disk.

It contains no control logic. Whether a row is an exception is decided by the
signed test, in the sandbox, from these inputs.
"""

from __future__ import annotations

import csv
import json
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Iterator

from .control_testing.definitions import ControlTestDataset
from .evidence import capture_file
from .models import EvidenceReference
from .spreadsheet import read_workbook

# The period the demonstration engagement covers.
PERIOD_START = date(2026, 7, 1)
PERIOD_END = date(2026, 7, 31)

SOURCE_SYSTEMS = (
    "cloud",
    "confluence",
    "finance",
    "github",
    "governance",
    "hr",
    "identity",
    "jira",
    "legal",
    "public",
)


@dataclass(frozen=True)
class CorpusFile:
    """One collected file, with the evidence reference computed at read time."""

    relative_path: str
    system: str
    path: Path
    evidence: EvidenceReference

    @property
    def evidence_id(self) -> str:
        return self.evidence.evidence_id


class AsteriaCorpus:
    """The collected fieldwork corpus for one engagement."""

    def __init__(self, demo_root: Path | str) -> None:
        self.root = Path(demo_root)
        self.sources_root = self.root / "sources"
        if not self.sources_root.is_dir():
            raise FileNotFoundError(f"no corpus at {self.sources_root}")
        self._files: dict[str, CorpusFile] = {}
        for path in sorted(self.sources_root.rglob("*")):
            if not path.is_file():
                continue
            relative = path.relative_to(self.sources_root).as_posix()
            system = relative.split("/", 1)[0]
            self._files[relative] = CorpusFile(
                relative_path=relative,
                system=system,
                path=path,
                evidence=capture_file(path, source_type=system),
            )

    # -- collection ------------------------------------------------------------

    def __len__(self) -> int:
        return len(self._files)

    def __iter__(self) -> Iterator[CorpusFile]:
        return iter(self._files.values())

    def file(self, relative_path: str) -> CorpusFile:
        try:
            return self._files[relative_path]
        except KeyError:
            raise FileNotFoundError(f"{relative_path} is not in the corpus") from None

    def by_system(self) -> dict[str, list[CorpusFile]]:
        grouped: dict[str, list[CorpusFile]] = {}
        for item in self._files.values():
            grouped.setdefault(item.system, []).append(item)
        return grouped

    def json(self, relative_path: str) -> Any:
        return json.loads(self.file(relative_path).path.read_text(encoding="utf-8"))

    def csv(self, relative_path: str) -> list[dict[str, str]]:
        with self.file(relative_path).path.open(encoding="utf-8", newline="") as handle:
            return list(csv.DictReader(handle))

    def workbook(self, relative_path: str):
        return read_workbook(self.file(relative_path).path)

    # -- projections -----------------------------------------------------------

    def scm_datasets(self) -> list[ControlTestDataset]:
        """SCM-01: pull requests, change tickets, and the exception register.

        ``approvals`` collapses from a list of reviewer handles to a count. The
        signed manifest asks for a count, and the identities of the reviewers are
        a separate control question (segregation of duties) tested against a
        different criterion — feeding them here would let one test quietly answer
        two.
        """
        pr_file = self.file("github/pull_requests.json")
        ticket_file = self.file("jira/change_tickets.json")
        exception_file = self.file("governance/approved_exceptions.json")

        pull_requests = [
            {
                "pull_request_id": item["pull_request_id"],
                "repository": item["repository"],
                "merged_at": item["merged_at"],
                "approvals": len(item.get("approvals") or []),
                "change_ticket": item.get("change_ticket"),
                "exception_key": item.get("exception_key"),
                "evidence_id": pr_file.evidence_id,
            }
            for item in self.json("github/pull_requests.json")
        ]
        tickets = [
            {
                "ticket_id": item["ticket_id"],
                "status": item["status"],
                "evidence_id": ticket_file.evidence_id,
            }
            for item in self.json("jira/change_tickets.json")
        ]
        exceptions = [
            {
                "exception_key": item["exception_key"],
                "active": bool(item["active"]),
                "evidence_id": exception_file.evidence_id,
            }
            for item in self.json("governance/approved_exceptions.json")
        ]
        return [
            ControlTestDataset(
                name="pull_requests",
                expected_count=len(pull_requests),
                evidence_ids=[pr_file.evidence_id],
                records=pull_requests,
            ),
            ControlTestDataset(
                name="change_tickets",
                evidence_ids=[ticket_file.evidence_id],
                records=tickets,
            ),
            ControlTestDataset(
                name="approved_exceptions",
                evidence_ids=[exception_file.evidence_id],
                records=exceptions,
            ),
        ]

    def iam_datasets(self) -> list[ControlTestDataset]:
        """IAM-01: leavers, directory accounts, and the exception register.

        The leaver population comes from the HR feed and the account status from
        the directory. They are deliberately two sources: a control that reads
        only the directory cannot detect an identity the directory never learned
        had left.
        """
        termination_file = self.file("hr/terminations.csv")
        account_file = self.file("identity/directory_accounts.csv")
        exception_file = self.file("governance/approved_exceptions.json")

        terminations = [
            {
                "user_id": row["user_id"],
                "terminated_at": row["terminated_at"],
                "disable_due_at": row["disable_due_at"],
                "evidence_id": termination_file.evidence_id,
            }
            for row in self.csv("hr/terminations.csv")
        ]
        leaver_ids = {row["user_id"] for row in terminations}
        accounts = [
            {
                "user_id": row["user_id"],
                "enabled": row["enabled"] == "true",
                "disabled_at": row["disabled_at"] or None,
                "exception_key": row["exception_key"] or None,
                "evidence_id": account_file.evidence_id,
            }
            for row in self.csv("identity/directory_accounts.csv")
            # The reference dataset is scoped to the population it is joined
            # against. Carrying all 254 accounts would not change a single
            # classification and would put the whole workforce directory into an
            # engagement that has no purpose for it.
            if row["user_id"] in leaver_ids
        ]
        exceptions = [
            {
                "exception_key": item["exception_key"],
                "active": bool(item["active"]),
                "evidence_id": exception_file.evidence_id,
            }
            for item in self.json("governance/approved_exceptions.json")
        ]
        return [
            ControlTestDataset(
                name="terminated_users",
                expected_count=len(terminations),
                evidence_ids=[termination_file.evidence_id],
                records=terminations,
            ),
            ControlTestDataset(
                name="directory_accounts",
                evidence_ids=[account_file.evidence_id],
                records=accounts,
            ),
            ControlTestDataset(
                name="approved_exceptions",
                evidence_ids=[exception_file.evidence_id],
                records=exceptions,
            ),
        ]

    def sla_datasets(self) -> list[ControlTestDataset]:
        """SLA-01: incidents, contractual commitments, and the documented target.

        Three systems that nothing inside the incident process joins: the
        ticketing export, the contract register, and the procedure page. The
        target on a ticket is carried through as ``operated_target_hours``
        precisely so the test can see that it disagrees with the contract — a
        projection that replaced it with the contractual figure would erase the
        condition on the way in.
        """
        incident_file = self.file("jira/incident_tickets.json")
        register_file = self.file("legal/contract_commitment_register.csv")
        procedure_file = self.file("confluence/incident_response_plan.md")

        incidents = [
            {
                "incident_id": item["ticket_id"],
                "customer": item["customer"],
                "priority": item["severity"],
                "opened_at": item["opened_at"],
                "first_response_at": item.get("first_response_at"),
                "operated_target_hours": int(item["sla_target_hours"]),
                "evidence_id": incident_file.evidence_id,
            }
            for item in self.json("jira/incident_tickets.json")
        ]
        commitments = [
            {
                "contract_ref": row["contract_ref"],
                "customer": row["customer"],
                "priority": row["priority"],
                "response_hours": int(row["response_hours"]),
                "coverage": row["coverage"] or None,
                "effective_from": row["effective_from"],
                "effective_to": row["effective_to"] or None,
                "credit_pct_per_breach": float(row["credit_pct_per_breach"]),
                "credit_cap_pct": float(row["credit_cap_pct"]),
                "monthly_fee_eur": float(row["monthly_fee_eur"]),
                "evidence_id": register_file.evidence_id,
            }
            for row in self.csv("legal/contract_commitment_register.csv")
        ]
        # The procedure is prose, so the targets it states are read out of its
        # own table rather than assumed. A page that stopped stating them would
        # produce no rows here, and the test would report that instead of
        # silently comparing against nothing.
        documented = [
            {
                "priority": priority,
                "scope": "all",
                "response_hours": hours,
                "document_ref": "confluence/incident_response_plan.md",
                "evidence_id": procedure_file.evidence_id,
            }
            for priority, hours in _documented_response_targets(
                procedure_file.path.read_text(encoding="utf-8")
            )
        ]
        return [
            ControlTestDataset(
                name="incidents",
                expected_count=len(incidents),
                evidence_ids=[incident_file.evidence_id],
                records=incidents,
            ),
            ControlTestDataset(
                name="contract_commitments",
                evidence_ids=[register_file.evidence_id],
                records=commitments,
            ),
            ControlTestDataset(
                name="documented_targets",
                evidence_ids=[procedure_file.evidence_id],
                records=documented,
            ),
        ]

    # -- observations ----------------------------------------------------------

    def access_review_status(self, *, as_at: date | None = None) -> dict[str, Any]:
        """Read the access-review campaign workbook the control owner supplied.

        This is a design-and-operation question rather than a population test:
        the policy requires a *completed* quarterly campaign, and the register
        says when campaigns closed. The result is returned as an observation with
        its own evidence reference, not as a finding — deciding what it means is
        the adjudication step's job.
        """
        as_at = as_at or PERIOD_END
        source = self.file("identity/access_review_campaigns.xlsx")
        campaigns = self.workbook("identity/access_review_campaigns.xlsx").sheet("Campaigns")
        completed = [
            row
            for row in campaigns.rows
            if str(row.get("status", "")).lower() == "completed" and row.get("closed_on")
        ]
        latest = max(completed, key=lambda row: str(row["closed_on"])) if completed else None
        latest_close = date.fromisoformat(str(latest["closed_on"])) if latest else None
        days_since = (as_at - latest_close).days if latest_close else None
        return {
            "control_ref": "PAM-01",
            "criteria": (
                "The access control policy requires production privileged roles to be reviewed "
                "quarterly, and the access review procedure requires the campaign to be completed "
                "within the quarter."
            ),
            "as_at": as_at.isoformat(),
            "campaign_count": len(campaigns.rows),
            "completed_campaign_count": len(completed),
            "latest_completed_campaign": str(latest["campaign_id"]) if latest else None,
            "latest_completed_on": latest_close.isoformat() if latest_close else None,
            "days_since_completed_review": days_since,
            "required_interval_days": 92,
            "within_required_interval": bool(days_since is not None and days_since <= 92),
            "incomplete_campaigns": [
                {"campaign_id": str(row["campaign_id"]), "status": str(row["status"])}
                for row in campaigns.rows
                if str(row.get("status", "")).lower() != "completed"
            ],
            "evidence_id": source.evidence_id,
            "source_locator": source.relative_path,
        }

    def collection_summary(self) -> dict[str, Any]:
        """What fieldwork collected, by system — the source-coverage matrix."""
        grouped = self.by_system()
        return {
            "file_count": len(self),
            "systems": {
                system: {
                    "file_count": len(items),
                    "files": [item.relative_path for item in items],
                    "bytes": sum(item.path.stat().st_size for item in items),
                }
                for system, items in sorted(grouped.items())
            },
        }


def _documented_response_targets(markdown: str) -> list[tuple[str, int]]:
    """Read the response targets out of the procedure's own commitments table.

    The page is prose written for people, so the targets are parsed rather than
    configured: hard-coding them here would mean the test compared the contract
    against a number in this file, and the procedure could then drift without
    anything noticing — which is the exact failure the test exists to catch.
    """
    targets: list[tuple[str, int]] = []
    in_table = False
    for line in markdown.splitlines():
        stripped = line.strip()
        if stripped.startswith("## "):
            in_table = stripped.lower().startswith("## customer response commitments")
            continue
        if not in_table or not stripped.startswith("|"):
            continue
        cells = [cell.strip() for cell in stripped.strip("|").split("|")]
        if len(cells) < 2 or not re.fullmatch(r"P[1-4]", cells[0]):
            continue
        match = re.match(r"(\d+)\s*(hour|business day)", cells[1], re.IGNORECASE)
        if not match:
            continue
        hours = int(match.group(1))
        if match.group(2).lower() == "business day":
            hours *= 24
        targets.append((cells[0], hours))
    return targets
