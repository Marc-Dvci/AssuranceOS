"""Golden cases that are executed, not merely hashed.

A ``golden_cases/`` directory is part of a signed control-test package and is
covered by its release digest, which proves the file did not change. It proves
nothing about whether the procedure still agrees with it, and a case nobody runs
is a comment with a JSON extension.

So these run. Each case carries its own inputs, the procedure is executed
through the same worker contract the engine uses, and the conclusion, the
exception keys and the per-row classifications are all compared.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
LIBRARY = ROOT / "tests-library"

#: Packages whose golden cases carry inputs and are therefore executable here.
#: Named explicitly rather than discovered, so that a package losing its
#: executable cases fails this test instead of silently reducing it to nothing.
EXECUTABLE = {"scm/reviewed-change-path"}


def _cases() -> list[tuple[str, Path]]:
    found: list[tuple[str, Path]] = []
    for package in sorted(EXECUTABLE):
        directory = LIBRARY / package / "golden_cases"
        assert directory.is_dir(), f"{package} has no golden_cases directory"
        paths = sorted(directory.glob("*.json"))
        assert paths, f"{package} has no golden cases"
        found.extend((f"{package}::{path.stem}", path) for path in paths)
    return found


CASES = _cases()


def _load_procedure(package: str):
    module_path = LIBRARY / package / "test.py"
    spec = importlib.util.spec_from_file_location(f"golden_{package.replace('/', '_')}", module_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.execute


def test_every_executable_package_is_present():
    assert {name.split("::")[0] for name, _ in CASES} == EXECUTABLE


@pytest.mark.parametrize("name,path", CASES, ids=[name for name, _ in CASES])
def test_golden_case(name: str, path: Path):
    package = name.split("::")[0]
    case = json.loads(path.read_text(encoding="utf-8"))
    for key in ("context", "parameters", "datasets", "expected_conclusion"):
        assert key in case, f"{name} is missing {key}"

    execute = _load_procedure(package)
    result = execute(
        datasets=case["datasets"],
        parameters=case["parameters"],
        context=case["context"],
    )

    assert result["conclusion"] == case["expected_conclusion"], name
    assert sorted(item["exception_key"] for item in result["exceptions"]) == sorted(
        case.get("expected_exception_keys", [])
    ), name
    if "expected_limitation_count" in case:
        assert len(result["limitations"]) == case["expected_limitation_count"], name
    expected_rows = case.get("expected_classifications")
    if expected_rows:
        actual = {row["commit_sha"]: row["classification"] for row in result["rows"]}
        assert actual == expected_rows, name


def test_an_unmerged_pull_request_is_not_a_compensating_control():
    """The row this procedure exists to get right, asserted on its own.

    A repository where somebody opens a pull request and pushes the code
    directly anyway is the exact shape a change control fails in, and it is the
    shape a naive implementation scores as a pass because a pull request exists.
    """

    execute = _load_procedure("scm/reviewed-change-path")
    result = execute(
        datasets={
            "commits": [
                {
                    "commit_sha": "x1",
                    "repository": "acme/platform",
                    "committed_at": "2026-08-04T09:00:00Z",
                    "author_login": "alice",
                    "parent_count": 1,
                    "evidence_id": "ev_x",
                }
            ],
            "commit_reviews": [
                {
                    "commit_sha": "x1",
                    "association_determined": True,
                    "merged_pull_request": None,
                    "pull_request_states": ["open"],
                    "approvals": 5,
                    "approvals_determined": True,
                    "evidence_id": "ev_rx",
                }
            ],
        },
        parameters={"expected_population_count": 1, "required_approvals": 1},
        context={"period_start": "2026-08-01", "period_end": "2026-08-31"},
    )
    assert result["conclusion"] == "ineffective"
    assert result["exceptions"][0]["classification"] == "unreviewed_change"
    assert "not a compensating control" in result["exceptions"][0]["reason"]


def test_an_undetermined_association_is_neither_a_pass_nor_an_exception():
    execute = _load_procedure("scm/reviewed-change-path")
    result = execute(
        datasets={
            "commits": [
                {
                    "commit_sha": "y1",
                    "repository": "acme/platform",
                    "committed_at": "2026-08-04T09:00:00Z",
                    "author_login": "alice",
                    "parent_count": 1,
                    "evidence_id": "ev_y",
                }
            ],
            "commit_reviews": [
                {
                    "commit_sha": "y1",
                    "association_determined": False,
                    "merged_pull_request": None,
                    "pull_request_states": [],
                    "approvals": None,
                    "approvals_determined": False,
                    "evidence_id": "ev_ry",
                }
            ],
        },
        parameters={"expected_population_count": 1, "required_approvals": 1},
        context={"period_start": "2026-08-01", "period_end": "2026-08-31"},
    )
    assert result["exceptions"] == []
    assert result["conclusion"] == "insufficient_evidence"
    assert result["rows"][0]["classification"] == "not_determined"


def test_a_missing_review_row_is_treated_as_undetermined_rather_than_as_absence():
    """A commit with no review row at all must not be scored as unreviewed.

    The two differ by whether anything was looked up, and a join that silently
    turns a missing row into a negative finding invents exceptions from a
    collection gap.
    """

    execute = _load_procedure("scm/reviewed-change-path")
    result = execute(
        datasets={
            "commits": [
                {
                    "commit_sha": "z1",
                    "repository": "acme/platform",
                    "committed_at": "2026-08-04T09:00:00Z",
                    "author_login": "alice",
                    "parent_count": 1,
                    "evidence_id": "ev_z",
                }
            ],
            "commit_reviews": [],
        },
        parameters={"expected_population_count": 1, "required_approvals": 1},
        context={"period_start": "2026-08-01", "period_end": "2026-08-31"},
    )
    assert result["exceptions"] == []
    assert result["rows"][0]["classification"] == "not_determined"


def test_required_approvals_of_zero_accepts_any_merged_pull_request():
    execute = _load_procedure("scm/reviewed-change-path")
    result = execute(
        datasets={
            "commits": [
                {
                    "commit_sha": "w1",
                    "repository": "acme/platform",
                    "committed_at": "2026-08-04T09:00:00Z",
                    "author_login": "alice",
                    "parent_count": 2,
                    "evidence_id": "ev_w",
                }
            ],
            "commit_reviews": [
                {
                    "commit_sha": "w1",
                    "association_determined": True,
                    "merged_pull_request": 7,
                    "pull_request_states": ["closed"],
                    "approvals": None,
                    "approvals_determined": False,
                    "evidence_id": "ev_rw",
                }
            ],
        },
        parameters={"expected_population_count": 1, "required_approvals": 0},
        context={"period_start": "2026-08-01", "period_end": "2026-08-31"},
    )
    assert result["conclusion"] == "effective"
    assert result["limitations"] == []
