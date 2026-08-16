"""Turn what a connector collected into the population a signed test is defined over.

:mod:`assuranceos.corpus` does this for files on disk. This does it for objects
that arrived over a connector, and the difference is not only where the bytes
came from.

A corpus file yields many rows and they all share the file's evidence
identifier, so an exception cites the export it was read out of. A collection
stores one evidence record per source object, so every row here carries the
evidence identifier of *its own* commit. An exception then cites the bytes of
exactly one change, which is the difference between "this is in the July export"
and "this is that commit, and here is its digest".

Two rules this module keeps, both of which are easy to break silently:

* **it reads the vaulted bytes, never the collector's memory.** Projecting from
  what the adapter returned would produce a population that was never hashed and
  could not be re-verified, while looking identical in the result;
* **it projects only the declared columns.** The signed manifests set
  ``additionalProperties: false``, so a projection carrying an extra field is a
  projection the schema never validated and the engine will reject -- late, and
  with a message about the schema rather than about the projection.

It contains no control logic. Whether a commit is an exception is decided by the
signed procedure, in the sandbox, from these inputs.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from .connectors.repository import ConnectorRepository
from .control_testing.definitions import ControlTestDataset
from .db.session import Database
from .vault import EvidenceVault


class ProjectionError(ValueError):
    """A population could not be built. The reason names what was missing."""


@dataclass(frozen=True)
class CollectedRow:
    """One collected object, with its payload and its own evidence identifier."""

    source_object_id: str
    evidence_id: str
    payload: dict[str, Any]
    source_locator: str


@dataclass
class CollectionReader:
    """Reads the objects of one or more runs back out of the vault."""

    database: Database
    vault: EvidenceVault
    tenant_id: str
    actor_id: str = "collection-projection"

    def rows(self, run_id: str) -> list[CollectedRow]:
        with self.database.read_session() as session:
            collected = ConnectorRepository(session).list_collected_objects(self.tenant_id, run_id)
            records = [
                (item.source_object_id, item.evidence_id, item.source_locator)
                for item in collected
            ]
        rows: list[CollectedRow] = []
        for source_object_id, evidence_id, locator in records:
            raw = self.vault.read_bytes(
                self.tenant_id,
                evidence_id,
                actor_id=self.actor_id,
                actor_type="service",
                purpose="projecting a collected population into a control-test dataset",
            )
            try:
                payload = json.loads(raw)
            except json.JSONDecodeError as exc:
                raise ProjectionError(
                    f"collected object {source_object_id} is not the JSON this projection expects"
                ) from exc
            if not isinstance(payload, dict):
                raise ProjectionError(
                    f"collected object {source_object_id} is not a JSON object"
                )
            rows.append(
                CollectedRow(
                    source_object_id=source_object_id,
                    evidence_id=evidence_id,
                    payload=payload,
                    source_locator=locator,
                )
            )
        return rows


@dataclass
class BoundPopulation:
    """The datasets a signed test will run over, and the parameters it needs.

    ``period`` is part of the binding rather than something the caller of
    ``tests.execute`` supplies. A model that can choose the period can choose
    the population, which is the same problem as choosing the dataset wearing a
    different hat: run the same procedure over a fortnight with nothing in it
    and the control passes.
    """

    test_id: str
    version: str | None
    datasets: list[ControlTestDataset]
    parameters: dict[str, Any] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)
    period: tuple[date, date] | None = None


def _int_or_none(value: Any) -> int | None:
    return int(value) if isinstance(value, int) else None


def project_scm_02(
    *,
    reader: CollectionReader,
    commits_run_id: str,
    commit_reviews_run_id: str,
    required_approvals: int = 1,
    period: tuple[date, date] | None = None,
) -> BoundPopulation:
    """SCM-02: commits on the default branch, and the review path of each.

    The two runs are separate collections of the same underlying walk, so they
    can disagree: a commit can appear in one and not the other when a lookup
    budget ran out mid-collection. That disagreement is preserved rather than
    smoothed over. A commit with no review row keeps ``association_determined:
    false``, and the procedure reports it as a limitation -- because the
    alternative, defaulting a missing join to "no pull request", turns a
    collection gap into a finding against somebody's engineering team.
    """

    commits = reader.rows(commits_run_id)
    if not commits:
        raise ProjectionError(
            "the commits collection returned no objects; there is no population to test"
        )
    reviews = {row.source_object_id: row for row in reader.rows(commit_reviews_run_id)}

    commit_records: list[dict[str, Any]] = []
    review_records: list[dict[str, Any]] = []
    notes: list[str] = []

    for row in commits:
        payload = row.payload
        sha = str(payload.get("sha") or row.source_object_id)
        repository = str(
            (payload.get("repository") or "")
            or _repository_from_locator(row.source_locator)
        )
        committed_at = ((payload.get("commit") or {}).get("author") or {}).get("date")
        if not committed_at:
            raise ProjectionError(f"commit {sha} carries no author date")
        commit_records.append(
            {
                "commit_sha": sha,
                "repository": repository,
                "committed_at": str(committed_at),
                "author_login": (payload.get("author") or {}).get("login"),
                "parent_count": len(payload.get("parents") or []),
                "evidence_id": row.evidence_id,
            }
        )

        review = reviews.get(sha)
        if review is None:
            review_records.append(
                {
                    "commit_sha": sha,
                    "association_determined": False,
                    "merged_pull_request": None,
                    "pull_request_states": [],
                    "approvals": None,
                    "approvals_determined": False,
                    "evidence_id": None,
                }
            )
            continue
        review_records.append(_review_record(sha, review))

    determined = sum(1 for item in review_records if item["association_determined"])
    if determined < len(commit_records):
        notes.append(
            f"{len(commit_records) - determined} of {len(commit_records)} commits had no "
            "determined pull-request association within the collection's lookup budget"
        )

    return BoundPopulation(
        test_id="SCM-02",
        version=None,
        datasets=[
            ControlTestDataset(
                name="commits",
                records=commit_records,
                expected_count=len(commit_records),
                evidence_ids=sorted({row.evidence_id for row in commits}),
            ),
            ControlTestDataset(
                name="commit_reviews",
                records=review_records,
                evidence_ids=sorted({row.evidence_id for row in reviews.values()}),
            ),
        ],
        parameters={
            "expected_population_count": len(commit_records),
            "required_approvals": int(required_approvals),
        },
        notes=notes,
        period=period,
    )


def _review_record(sha: str, review: CollectedRow) -> dict[str, Any]:
    payload = review.payload
    association = payload.get("association") or {}
    associated = payload.get("associated_pull_requests") or []
    states = [str(item.get("state")) for item in associated if item.get("state")]

    # The *merged* pull request, and only a merged one. A pull request is
    # associated with a commit whether it merged or not, and taking the first
    # association would score an abandoned pull request as the review path.
    merged = None
    approvals = None
    approvals_determined = False
    for item in associated:
        if not item.get("merged_at"):
            continue
        number = _int_or_none(item.get("number"))
        if number is None:
            continue
        # More than one merged pull request can carry a commit, after a revert
        # and a re-merge. The lowest number is the earliest, which is the one
        # that first put this change on the branch.
        if merged is None or number < merged:
            merged = number
            approvals = _int_or_none(item.get("approvals"))
            approvals_determined = bool(item.get("approvals_determined"))
    return {
        "commit_sha": sha,
        "association_determined": bool(association.get("determined")),
        "merged_pull_request": merged,
        "pull_request_states": states,
        "approvals": approvals,
        "approvals_determined": approvals_determined,
        "evidence_id": review.evidence_id,
    }


def _repository_from_locator(locator: str) -> str:
    """Recover ``owner/repo`` from a commit URL, for a payload that omits it."""

    parts = [item for item in locator.split("/") if item]
    if "github.com" in locator and len(parts) >= 4:
        try:
            index = parts.index("github.com")
        except ValueError:
            return ""
        if len(parts) > index + 2:
            return f"{parts[index + 1]}/{parts[index + 2]}"
    return ""
