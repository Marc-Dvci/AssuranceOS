from pathlib import Path

from assuranceos.control_testing.demo import run_control_test_demo
from assuranceos.db.session import Database


def test_control_test_demo_runs_every_released_procedure(tmp_path):
    """Both signed procedures run over the collected corpus, not over constants.

    The exceptions are asserted by identifier. A count on its own would pass if
    the projection dropped one seeded defect and invented another, which is
    precisely the failure this demonstration exists to rule out.
    """
    root = Path(__file__).resolve().parents[1]
    database = Database.from_sqlite_path(tmp_path / "demo.db")
    database.create_schema()
    try:
        result = run_control_test_demo(database, root)
        assert {item["test_id"] for item in result["released_tests"]} >= {"SCM-01", "IAM-01", "SLA-01"}
        assert result["executed_tests"] == ["IAM-01", "SCM-01", "SLA-01"]
        assert len(result["runs"]) == 3
        # A released procedure this corpus cannot supply is named with its
        # reason, so "not run" never looks the same as "not noticed".
        assert {item["test_id"] for item in result["not_run"]} == {"SCM-02"}

        runs = {item["test_id"]: item for item in result["runs"]}
        assert all(item["conclusion"] == "ineffective" for item in runs.values())
        assert all(item["population_complete"] for item in runs.values())

        scm = runs["SCM-01"]
        assert scm["population_count"] == 44
        assert [item["subject_ref"].rsplit("#", 1)[1] for item in scm["exceptions"]] == [
            "PR-1002",
            "PR-1033",
            "PR-1021",
        ]

        iam = runs["IAM-01"]
        assert iam["population_count"] == 18
        assert [item["exception_key"] for item in iam["exceptions"]] == ["IAM-01:c-0003"]

        # SLA-01 has to separate three things that look alike: a breach of the
        # amended contract, a compliant response under a *different* customer's
        # unamended contract, and the design gap that let the breaches be
        # recorded as met.
        sla = runs["SLA-01"]
        assert sla["population_count"] == 9
        assert sorted(item["exception_key"] for item in sla["exceptions"]) == [
            "SLA-01:breach:INC-4402",
            "SLA-01:breach:INC-4419",
            "SLA-01:breach:INC-4424",
            "SLA-01:design:Northwind Trading BV:P1",
        ]
        tested = {row["incident_id"]: row for row in sla["rows"]}
        # Contoso is still on an 8-hour target and answered in 5.75 hours.
        assert tested["INC-4413"]["classification"] == "effective"
        assert tested["INC-4413"]["contractual_target_hours"] == 8
        # The March incident predates the audit period and is not tested.
        assert "INC-4361" not in tested
        # Every breach was recorded against the superseded internal target.
        assert all(
            row["operated_target_hours"] == 8
            for row in sla["rows"]
            if row["classification"] == "control_exception"
        )

        # The whole corpus was collected, not only the files the tests read.
        assert result["corpus"]["file_count"] == 56
        assert set(result["corpus"]["systems"]) == {
            "cloud", "confluence", "finance", "github",
            "governance", "hr", "identity", "jira", "legal", "public",
        }

        # The access review is an observation with its own evidence, and it is
        # overdue against the quarterly requirement.
        observation = result["access_review_observation"]
        assert observation["latest_completed_campaign"] == "ARC-2025-Q4"
        assert observation["within_required_interval"] is False
        assert observation["evidence_id"].startswith("evd_")
    finally:
        database.dispose()
