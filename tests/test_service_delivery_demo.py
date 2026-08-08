"""The service-delivery engagement, and the review screen built from it.

The condition under test is one no single system can see: three internal sources
agree with each other and all of them disagree with the customer contract. The
assertions below are about the two things that make that finding usable — that
the right incidents were selected, and that the reasoning survives into the
projection a reviewer reads.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from assuranceos.db import Database
from assuranceos.demo import run_golden_engagement
from assuranceos.ledger import AuditLedger
from assuranceos.product import finding_detail
from assuranceos.service_delivery_demo import (
    DEMO_TENANT,
    SLA_ENGAGEMENT_ID,
    run_service_delivery_demo,
)

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def result(tmp_path: Path):
    database = Database.from_sqlite_path(tmp_path / "service-delivery.db")
    database.create_schema()
    # Fieldwork collects the corpus into the vault before any test cites it.
    # Running the engagement against an empty vault would leave the finding
    # pointing at evidence identifiers that resolve to nothing, which is what
    # the citation assertions below are there to catch.
    run_golden_engagement(ROOT / "demo/asteria", AuditLedger(tmp_path / "service-delivery.db"))
    try:
        yield database, run_service_delivery_demo(database=database, repository_root=ROOT)
    finally:
        database.dispose()


def test_the_finding_reports_the_breaches_and_not_the_lookalikes(result):
    _, outcome = result

    assert outcome["conclusion"] == "ineffective"
    assert outcome["engagement_id"] == SLA_ENGAGEMENT_ID
    assert sorted(outcome["breaches"]) == ["jira:INC-4402", "jira:INC-4419", "jira:INC-4424"]

    # The two incidents that look like breaches and are not.
    assert "jira:INC-4413" not in outcome["breaches"]
    assert "jira:INC-4361" not in outcome["breaches"]

    # And the reason the breaches went unnoticed is itself reported.
    assert "superseded target" in (outcome["design_gap"] or "")


def test_the_review_screen_can_show_why_the_finding_stands(result):
    """The projection has to carry the argument, not just the conclusion."""
    database, outcome = result
    detail = finding_detail(database, DEMO_TENANT, outcome["finding_id"])
    assert detail is not None

    # Three independent systems, each cited with a digest.
    assert {item["source_type"] for item in detail["evidence"]} == {"confluence", "jira", "legal"}
    assert all(len(item["sha256"]) == 64 for item in detail["evidence"])

    # The three numbers the reconciliation card compares come from the test.
    gap = next(
        item for item in detail["exceptions"] if item["classification"] == "control_design_gap"
    )
    assert gap["attributes"]["contractual_hours"] == 4
    assert gap["attributes"]["documented_hours"] == 8
    assert gap["attributes"]["operated_hours"] == [8]

    # The tested population is on screen, including the rows that passed —
    # a table of only the failures cannot show that the control works elsewhere.
    rows = {row["incident_id"]: row for row in detail["test_run"]["rows"]}
    assert len(rows) == 5
    assert rows["INC-4413"]["classification"] == "effective"
    assert rows["INC-4413"]["contractual_target_hours"] == 8

    # The money, because "a control gap" and "EUR 7,200 a month" are not the
    # same sentence to the person who has to prioritise it.
    exposure = detail["test_run"]["metrics"]["service_credit_exposure"]
    assert exposure[0]["customer"] == "Northwind Trading BV"
    assert exposure[0]["credit_value_eur"] == 7200.0

    # What was rejected, and the gate that is still open.
    assert len(detail["finding"]["contradictions"]) == 3
    assert detail["finding"]["status"] == "proposed"
    assert detail["finding"]["approval_blockers"]


def test_the_documented_target_is_read_from_the_procedure_not_configured(result):
    """A hard-coded internal target would make the drift undetectable.

    If the number the test compares against came from source rather than from
    the page, the page could be corrected — or corrupted — without changing the
    result, and the control would be testing this repository instead of Asteria.
    """
    from assuranceos.corpus import AsteriaCorpus

    corpus = AsteriaCorpus(ROOT / "demo/asteria")
    documented = next(
        item for item in corpus.sla_datasets() if item.name == "documented_targets"
    )
    assert {row["priority"]: row["response_hours"] for row in documented.records} == {
        "P1": 8,
        "P2": 24,
        "P3": 72,
    }
    assert all(
        row["document_ref"] == "confluence/incident_response_plan.md"
        for row in documented.records
    )


def test_only_the_human_gate_is_left_open(result):
    """The gates the platform can clear itself must not be shown as the reviewer's.

    The register once reported 'human approval required' as the sole blocker
    while the service was refusing the approval for two further reasons. A
    reviewer told to sign, whose signature is then rejected, has been misled by
    the screen rather than protected by the gate.
    """
    from assuranceos.adjudication.definitions import AdjudicationRequest, HumanDecision
    from assuranceos.adjudication.service import AdjudicationService

    database, outcome = result
    service = AdjudicationService(database)
    assert (
        service.approval_blockers(tenant_id=DEMO_TENANT, finding_id=outcome["finding_id"])
        == []
    )

    detail = finding_detail(database, DEMO_TENANT, outcome["finding_id"])
    assert detail["finding"]["approval_blockers"] == ["human approval required"]

    # And the approval the screen offers actually succeeds.
    status = service.adjudicate(
        tenant_id=DEMO_TENANT,
        request=AdjudicationRequest(
            finding_id=outcome["finding_id"],
            decision=HumanDecision.APPROVE,
            actor_id="dana.director@asteria.example",
            reason="Reviewed against the engagement file.",
            idempotency_key=f"test:approve:{outcome['finding_id']}",
        ),
    )
    assert status.value == "approved"
