"""The corpus projection, and the workbook reader it depends on.

Two things are worth testing here and they are different. The workbook reader is
a parser, so it is tested on what it refuses as much as on what it reads. The
corpus projection is a contract between the files on disk and the signed test
manifests, so it is tested by validating the projected rows against the manifest
schemas rather than against a hand-copied expectation — a projection asserted
against a literal only proves the literal was copied correctly.
"""

from __future__ import annotations

import zipfile
from datetime import date
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from assuranceos.control_testing.registry import ControlTestRegistry
from assuranceos.corpus import AsteriaCorpus
from assuranceos.spreadsheet import WorkbookError, read_workbook, write_workbook

ROOT = Path(__file__).resolve().parents[1]
DEMO_ROOT = ROOT / "demo" / "asteria"


@pytest.fixture(scope="module")
def corpus() -> AsteriaCorpus:
    return AsteriaCorpus(DEMO_ROOT)


@pytest.fixture(scope="module")
def registry():
    return ControlTestRegistry(
        ROOT / "tests-library",
        trusted_public_key=(
            ROOT / "security/release-keys/control-test-release-public.pem"
        ).read_bytes(),
    ).load()


# -- the workbook reader -------------------------------------------------------


def test_workbook_round_trips_values_and_types(tmp_path):
    path = write_workbook(
        tmp_path / "register.xlsx",
        {
            "Sheet1": (
                ["ref", "count", "ratio", "closed", "note"],
                [
                    ["A-1", 3, 1.5, True, "first"],
                    ["A-2", 0, 0.25, False, "second & third < fourth"],
                ],
            )
        },
    )
    sheet = read_workbook(path).sheet("Sheet1")
    assert sheet.columns == ("ref", "count", "ratio", "closed", "note")
    assert sheet.rows[0] == {"ref": "A-1", "count": 3, "ratio": 1.5, "closed": True, "note": "first"}
    # Escaped characters survive the XML round trip unchanged.
    assert sheet.rows[1]["note"] == "second & third < fourth"
    assert sheet.column("count") == [3, 0]


def test_workbook_writes_identical_bytes_for_identical_data(tmp_path):
    """A rebuild that changed no data must not change the evidence hash."""
    payload = {"S": (["a"], [["x"], ["y"]])}
    first = write_workbook(tmp_path / "one.xlsx", payload).read_bytes()
    second = write_workbook(tmp_path / "two.xlsx", payload).read_bytes()
    assert first == second


def test_workbook_keeps_multiple_sheets_in_order(tmp_path):
    path = write_workbook(
        tmp_path / "two.xlsx",
        {"First": (["a"], [["1"]]), "Second": (["b"], [["2"]])},
    )
    workbook = read_workbook(path)
    assert workbook.sheet_names == ("First", "Second")
    assert workbook.sheet("Second").rows == ({"b": "2"},)


def test_workbook_refuses_a_formula_cell(tmp_path):
    """A cached formula result is another program's output, not a read value."""
    path = write_workbook(tmp_path / "formula.xlsx", {"S": (["a", "b"], [[1, 2]])})
    _inject_formula(path)
    with pytest.raises(WorkbookError, match="formula"):
        read_workbook(path)


def test_workbook_reports_a_missing_sheet_by_name(tmp_path):
    path = write_workbook(tmp_path / "one.xlsx", {"Only": (["a"], [["1"]])})
    with pytest.raises(WorkbookError, match="Only"):
        read_workbook(path).sheet("Absent")


def test_workbook_rejects_xml_entities(tmp_path):
    path = write_workbook(tmp_path / "entity.xlsx", {"Only": (["a"], [["value"]])})
    with zipfile.ZipFile(path) as archive:
        parts = {name: archive.read(name) for name in archive.namelist()}
    target = "xl/worksheets/sheet1.xml"
    parts[target] = parts[target].replace(
        b'<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">',
        b'<!DOCTYPE worksheet [<!ENTITY xxe SYSTEM "file:///etc/passwd">]>'
        b'<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">',
    ).replace(b">value<", b">&xxe;<")
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, content in parts.items():
            archive.writestr(name, content)

    with pytest.raises(WorkbookError, match="unsafe"):
        read_workbook(path)


def _inject_formula(path: Path) -> None:
    with zipfile.ZipFile(path) as archive:
        parts = {name: archive.read(name) for name in archive.namelist()}
    target = "xl/worksheets/sheet1.xml"
    parts[target] = parts[target].replace(
        b'<c r="B2"><v>2</v></c>', b'<c r="B2"><f>A2+1</f><v>2</v></c>'
    )
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, content in parts.items():
            archive.writestr(name, content)


# -- collection ----------------------------------------------------------------


def test_every_corpus_file_is_hashed_on_collection(corpus):
    assert len(corpus) == 56
    for item in corpus:
        assert item.evidence_id.startswith("evd_")
        assert len(item.evidence.sha256) == 64
        assert item.system == item.relative_path.split("/", 1)[0]
    # Distinct files produce distinct evidence identifiers.
    assert len({item.evidence_id for item in corpus}) == len(corpus)


def test_collection_summary_covers_every_source_system(corpus):
    summary = corpus.collection_summary()
    assert summary["file_count"] == 56
    assert set(summary["systems"]) == {
        "cloud", "confluence", "finance", "github",
        "governance", "hr", "identity", "jira", "legal", "public",
    }
    assert sum(item["file_count"] for item in summary["systems"].values()) == 56


def test_missing_file_is_named(corpus):
    with pytest.raises(FileNotFoundError, match="jira/absent.json"):
        corpus.file("jira/absent.json")


def test_absent_corpus_is_refused(tmp_path):
    with pytest.raises(FileNotFoundError):
        AsteriaCorpus(tmp_path)


# -- projection ----------------------------------------------------------------


@pytest.mark.parametrize(
    "projection, test_id, version",
    [("scm_datasets", "SCM-01", "2.0.0"), ("iam_datasets", "IAM-01", "1.0.0")],
)
def test_projected_rows_satisfy_the_signed_manifest(corpus, registry, projection, test_id, version):
    release = registry.get(test_id, version)
    contracts = {item.name: item for item in release.manifest.datasets}
    datasets = getattr(corpus, projection)()

    assert {item.name for item in datasets} == set(contracts)
    for dataset in datasets:
        validator = Draft202012Validator(contracts[dataset.name].row_schema)
        assert dataset.records, f"{dataset.name} projected no rows"
        for row in dataset.records:
            validator.validate(row)
        # Every row carries the identifier of the file it was read out of.
        assert all(row.get("evidence_id") for row in dataset.records)

    population = next(
        item for item in datasets if contracts[item.name].role == "population"
    )
    assert population.expected_count == len(population.records)


def test_scm_projection_counts_approvals_and_keeps_the_seeded_records(corpus):
    datasets = {item.name: item.records for item in corpus.scm_datasets()}
    by_id = {row["pull_request_id"]: row for row in datasets["pull_requests"]}
    assert len(by_id) == 44
    assert by_id["PR-1001"]["approvals"] == 1
    assert by_id["PR-1002"]["approvals"] == 0
    assert by_id["PR-1003"]["exception_key"] == "EXC-SVC-001"
    assert by_id["PR-1033"]["change_ticket"] is None
    # The offset is preserved rather than rewritten; normalising it is the
    # control test's job and the evidence must keep what the source recorded.
    assert by_id["PR-1004"]["merged_at"] == "2026-07-01T00:30:00+02:00"


def test_iam_reference_is_scoped_to_the_leaver_population(corpus):
    datasets = {item.name: item.records for item in corpus.iam_datasets()}
    leavers = {row["user_id"] for row in datasets["terminated_users"]}
    accounts = {row["user_id"] for row in datasets["directory_accounts"]}
    assert len(leavers) == 18
    assert accounts == leavers

    by_id = {row["user_id"]: row for row in datasets["directory_accounts"]}
    # The terminated contractor is still enabled; that is the seeded defect.
    assert by_id["c-0003"]["enabled"] is True
    assert by_id["c-0003"]["exception_key"] is None
    # Exactly one retained account is covered by an approved exception.
    retained = [row for row in datasets["directory_accounts"] if row["exception_key"]]
    assert [row["exception_key"] for row in retained] == ["EXC-IAM-004"]
    assert {"EXC-IAM-004"} <= {
        row["exception_key"] for row in datasets["approved_exceptions"] if row["active"]
    }


# -- observation ---------------------------------------------------------------


def test_access_review_is_overdue_against_the_quarterly_requirement(corpus):
    observation = corpus.access_review_status()
    assert observation["control_ref"] == "PAM-01"
    assert observation["latest_completed_campaign"] == "ARC-2025-Q4"
    assert observation["latest_completed_on"] == "2025-12-19"
    assert observation["days_since_completed_review"] == 224
    assert observation["within_required_interval"] is False
    # An abandoned campaign and one never started are reported as incomplete
    # rather than counted towards the requirement.
    assert [item["campaign_id"] for item in observation["incomplete_campaigns"]] == [
        "ARC-2026-Q1",
        "ARC-2026-Q2",
    ]
    assert observation["evidence_id"] == corpus.file(
        "identity/access_review_campaigns.xlsx"
    ).evidence_id


def test_access_review_would_pass_immediately_after_a_campaign_closed(corpus):
    """The rule is the interval, not the calendar year the register ends in."""
    observation = corpus.access_review_status(as_at=date(2026, 1, 15))
    assert observation["days_since_completed_review"] == 27
    assert observation["within_required_interval"] is True
