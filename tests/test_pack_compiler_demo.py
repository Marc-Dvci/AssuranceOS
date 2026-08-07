"""The Audit Pack compiler demonstration, checked against canonical state.

The demonstration this component owes is not "a pack compiled". It is that the
engagement which runs is a function of a signed artefact, that the same inputs
produce the same graph, and that each way a pack can be wrong produces its own
refusal rather than a partially built audit.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from assuranceos.db.session import Database
from assuranceos.standards.demo import run_pack_compiler_demo

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def database(tmp_path):
    db = Database.from_sqlite_path(tmp_path / "packs.db")
    db.create_schema()
    try:
        yield db
    finally:
        db.dispose()


@pytest.fixture
def result(database) -> dict:
    return run_pack_compiler_demo(database=database, repository_root=ROOT)


def test_the_graph_in_the_database_is_the_graph_the_pack_describes(result):
    """Read back from the database, not from the compiler's return value."""
    assert result["task_count"] == 11
    assert len(result["tasks_from_canonical_state"]) == 11
    assert result["gates_from_canonical_state"] == [
        "engagement_scope_approval",
        "finding_approval",
        "finding_closure_approval",
        "report_issuance",
    ]


def test_compilation_is_deterministic(result):
    assert result["compilation_is_deterministic"]


def test_two_packs_produce_two_different_graphs(result):
    """A platform whose engagements all look the same has a template, not a compiler."""
    assert result["packs_produce_different_graphs"]
    assert result["second_pack_task_count"] != result["task_count"]


def test_provenance_names_every_version_the_engagement_depended_on(result):
    provenance = result["provenance"]
    assert provenance["standard"] == "AST-SCM-POL@4.0"
    assert provenance["control_tests"] == {"SCM-01": "2.0.0"}
    assert len(provenance["criteria"]) == 3
    assert provenance["platform_version"] == "0.8.0"


def test_every_refusal_is_distinct_and_says_why(result):
    refusals = result["refusals"]
    assert set(refusals) == {
        "unentitled_standard",
        "criteria_not_effective_for_period",
        "pinned_control_test_missing",
        "pack_not_approved",
        "already_compiled",
        "tampered_pack",
    }
    # Every one produced a message, and no two are the same message. A gate that
    # refuses everything with one sentence cannot be routed on.
    assert all(refusals.values())
    assert len(set(refusals.values())) == len(refusals)

    assert "holds no entitlement" in refusals["unentitled_standard"]
    assert "do not cover the audit period" in refusals["criteria_not_effective_for_period"]
    assert "is not released" in refusals["pinned_control_test_missing"]
    assert "only from an approved pack" in refusals["pack_not_approved"]
    assert "already compiled" in refusals["already_compiled"]
    assert "file manifest does not match" in refusals["tampered_pack"]


def test_the_demonstration_leaves_the_repository_packs_untouched(result):
    """The tamper case runs on a copy.

    A demonstration that leaves the working tree modified has produced a second
    problem in the course of illustrating the first.
    """
    from assuranceos.standards import AuditPackRegistry

    key = (ROOT / "security/release-keys/audit-pack-release-public.pem").read_bytes()
    registry = AuditPackRegistry(ROOT / "audit-packs", trusted_public_key=key).load()
    assert len(registry.list()) == 3
