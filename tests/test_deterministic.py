from pathlib import Path
from assuranceos.deterministic import run_scm_population_test


def test_seeded_scm_population():
    root = Path(__file__).resolve().parents[1]
    result = run_scm_population_test(root / "demo/asteria")
    assert result["population_count"] == 3
    assert result["exception_count"] == 1
    assert result["exceptions"][0]["pull_request_id"] == "PR-1002"
    assert next(r for r in result["all_results"] if r["pull_request_id"] == "PR-1003")["classification"] == "approved_exception"
    assert next(r for r in result["all_results"] if r["pull_request_id"] == "PR-1004")["classification"] == "out_of_period"
