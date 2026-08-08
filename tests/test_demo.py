from pathlib import Path

from assuranceos.db.models import EvidenceRecord
from assuranceos.demo import TENANT_ID, run_golden_engagement, source_locator
from assuranceos.ledger import AuditLedger

ROOT = Path(__file__).resolve().parents[1]
DEMO_ROOT = ROOT / "demo" / "asteria"


def test_golden_demo(tmp_path):
    ledger = AuditLedger(tmp_path / "ledger.db")
    result = run_golden_engagement(DEMO_ROOT, ledger)
    assert result["finding"]["status"] == "proposed"
    assert result["security_event"]["canonical_state_mutated"] is False
    assert len(ledger.list_events(TENANT_ID)) == result["event_count"]


def test_golden_demo_collects_the_whole_corpus_but_cites_only_its_support(tmp_path):
    """Fieldwork collects everything; the finding cites what supports it.

    These are separate numbers on purpose. An evidence list that grows with the
    corpus is a directory listing, and a finding that cites 56 files including
    the marketing site has not established a chain of custody for anything.
    """
    ledger = AuditLedger(tmp_path / "ledger.db")
    result = run_golden_engagement(DEMO_ROOT, ledger)

    with ledger.database.read_session() as session:
        records = list(session.query(EvidenceRecord).filter_by(tenant_id=TENANT_ID))

    assert len(records) == 56
    assert len(result["finding"]["evidence_ids"]) == 4
    assert set(result["finding"]["evidence_ids"]) <= {item.evidence_id for item in records}

    # Exactly one record is tainted: the policy page carrying the injection.
    tainted = [item for item in records if item.tainted]
    assert [item.source_locator for item in tainted] == [
        "confluence://asteria/change_management_policy.md"
    ]

    # Every record names the system it came from, and the workbooks keep their
    # real media type rather than being flattened to text.
    assert all("://" in item.source_locator for item in records)
    workbooks = [item for item in records if item.source_locator.endswith(".xlsx")]
    assert len(workbooks) == 5
    assert all(
        item.mime_type
        == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        for item in workbooks
    )


def test_the_observed_condition_is_written_from_the_population(tmp_path):
    ledger = AuditLedger(tmp_path / "ledger.db")
    result = run_golden_engagement(DEMO_ROOT, ledger)
    condition = result["test_result"]
    assert condition["population_count"] == 43
    assert condition["exception_count"] == 3


def test_source_locator_names_the_system_not_the_directory():
    sources = DEMO_ROOT / "sources"
    assert (
        source_locator(sources / "hr" / "terminations.csv", DEMO_ROOT)
        == "workday://asteria/terminations.csv"
    )
    assert (
        source_locator(sources / "cloud" / "iam_policy_bindings.json", DEMO_ROOT)
        == "gcp://asteria/iam_policy_bindings.json"
    )
    # Public sources are public: they get a URL, not an internal scheme.
    assert (
        source_locator(sources / "public" / "trust_center.md", DEMO_ROOT)
        == "https://asteria-demo.invalid/trust_center.md"
    )
