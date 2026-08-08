from pathlib import Path
from assuranceos.deterministic import run_scm_population_test


def test_seeded_scm_population():
    """The SCM-01 population over the full Asteria corpus.

    The counts are asserted alongside the named seeded conditions rather than on
    their own. A population count that drifts is a corpus change; a *named*
    record that changes classification is a regression in the test logic, and
    the two failures should not look alike.
    """
    root = Path(__file__).resolve().parents[1]
    result = run_scm_population_test(root / "demo/asteria")

    assert len(result["all_results"]) == 44
    assert result["population_count"] == 43
    assert result["exception_count"] == 3
    assert result["conclusion"] == "ineffective"

    by_id = {item["pull_request_id"]: item for item in result["all_results"]}
    # Seeded defects: no independent approval, an emergency change whose
    # retrospective approval was never filed, and a merge with no change ticket.
    assert [item["pull_request_id"] for item in result["exceptions"]] == [
        "PR-1002",
        "PR-1021",
        "PR-1033",
    ]
    # Seeded non-findings: an active approved exception, and a merge that falls
    # outside the period once its UTC offset is normalised.
    assert by_id["PR-1003"]["classification"] == "approved_exception"
    assert by_id["PR-1004"]["classification"] == "out_of_period"
    assert by_id["PR-1001"]["classification"] == "effective"
