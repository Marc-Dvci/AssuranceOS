"""The gate that decides the build from a CodeQL report.

The interesting property is not that a high finding fails. It is that the two
ways of excusing one behave differently: a declared exclusion is printed with its
reasoning every run, and it fails the build once the finding it was written for is
gone, so the declaration cannot quietly grow into a list of things nobody has
looked at since.
"""

from __future__ import annotations

import pytest

from scripts.check_codeql_sarif import (
    DEFAULT_EXCLUSIONS,
    Exclusion,
    classify,
    load_exclusions,
    report,
)

RULE = "py/path-injection"
PATH = "src/assuranceos/vault/storage.py"


def sarif(*results: tuple[str, str, int, float]) -> dict:
    """A minimal report carrying one rule definition per distinct rule id."""
    rules = {
        rule_id: {
            "id": rule_id,
            "properties": {"security-severity": str(severity)},
        }
        for rule_id, _, _, severity in results
    }
    return {
        "runs": [
            {
                "tool": {"driver": {"rules": list(rules.values())}},
                "results": [
                    {
                        "ruleId": rule_id,
                        "message": {"text": "This path depends on a user-provided value."},
                        "locations": [
                            {
                                "physicalLocation": {
                                    "artifactLocation": {"uri": uri},
                                    "region": {"startLine": line},
                                }
                            }
                        ],
                    }
                    for rule_id, uri, line, _ in results
                ],
            }
        ]
    }


def declared(rule: str = RULE, path: str = PATH) -> Exclusion:
    return Exclusion(rule=rule, path=path, reason="checked and rejected", controls=("a test",))


def test_a_high_finding_with_no_declaration_fails_the_build(capsys):
    outcome = classify([sarif((RULE, PATH, 116, 7.5))], threshold=7.0, exclusions=[])

    assert report(outcome, [], threshold=7.0) == 1
    assert "FAIL: 1 at or above security severity" in capsys.readouterr().out


def test_a_declared_exclusion_covers_every_line_of_its_rule_and_file(capsys):
    """Keyed on rule and file, so the twelve sinks in one module are one decision."""
    exclusions = [declared()]
    outcome = classify(
        [sarif((RULE, PATH, 116, 7.5), (RULE, PATH, 135, 7.5), (RULE, PATH, 204, 7.5))],
        threshold=7.0,
        exclusions=exclusions,
    )

    assert outcome.blocking == []
    assert len(outcome.excluded[0]) == 3
    assert report(outcome, exclusions, threshold=7.0) == 0

    printed = capsys.readouterr().out
    assert "declared not a defect" in printed
    assert "checked and rejected" in printed, "the reasoning is printed on every run"
    assert "held up by a test" in printed


def test_an_exclusion_that_matches_nothing_fails_the_build(capsys):
    """The finding is gone, so the exclusion has to go with it."""
    exclusions = [declared()]
    outcome = classify([sarif(("py/stack-trace-exposure", "src/api.py", 10, 5.4))], threshold=7.0, exclusions=exclusions)

    assert report(outcome, exclusions, threshold=7.0) == 1
    printed = capsys.readouterr().out
    assert "declared exclusion(s) match nothing" in printed
    assert "remove the exclusion with it" in printed


def test_an_exclusion_does_not_reach_a_second_file(capsys):
    """Same rule, another module, and the decision made about the vault says nothing about it."""
    exclusions = [declared()]
    outcome = classify(
        [sarif((RULE, PATH, 116, 7.5), (RULE, "src/assuranceos/exports.py", 42, 7.5))],
        threshold=7.0,
        exclusions=exclusions,
    )

    assert len(outcome.blocking) == 1
    assert "src/assuranceos/exports.py:42" in outcome.blocking[0]
    assert report(outcome, exclusions, threshold=7.0) == 1


def test_a_suppression_carried_in_the_report_is_counted_apart():
    result = sarif((RULE, PATH, 116, 7.5))
    result["runs"][0]["results"][0]["suppressions"] = [{"kind": "inSource"}]

    outcome = classify([result], threshold=7.0, exclusions=[])

    assert outcome.suppressed and not outcome.blocking


def test_an_exclusion_without_a_reason_is_refused(tmp_path):
    path = tmp_path / "exclusions.toml"
    path.write_text('[[exclusion]]\nrule = "py/path-injection"\npath = "a.py"\n', encoding="utf-8")

    with pytest.raises(ValueError, match="missing: reason"):
        load_exclusions(path)


def test_the_repositorys_own_declaration_states_a_reason_and_its_controls():
    exclusions = load_exclusions(DEFAULT_EXCLUSIONS)

    assert exclusions, "the declaration is the record of what is knowingly not fixed"
    for exclusion in exclusions:
        assert len(exclusion.reason.split()) >= 20, f"{exclusion.rule} states no reasoning"
        assert exclusion.controls, f"{exclusion.rule} names no test holding it up"


def test_every_control_the_declaration_cites_still_exists():
    """A cited test that has been renamed leaves the exclusion resting on nothing."""
    root = DEFAULT_EXCLUSIONS.parents[1]

    for exclusion in load_exclusions(DEFAULT_EXCLUSIONS):
        for control in exclusion.controls:
            module, _, name = control.partition("::")
            source = root / module
            assert source.is_file(), f"{exclusion.rule} cites a module that is gone: {module}"
            assert f"def {name}(" in source.read_text(encoding="utf-8"), (
                f"{exclusion.rule} cites a test that is gone: {control}"
            )


def test_the_code_the_declaration_excuses_carries_no_stale_marker():
    """The inline syntax does not reach this gate, so leaving one reads as a decision
    that is doing nothing. The declaration is the only place an exclusion lives."""
    root = DEFAULT_EXCLUSIONS.parents[1]

    for exclusion in load_exclusions(DEFAULT_EXCLUSIONS):
        source = (root / exclusion.path).read_text(encoding="utf-8")
        assert f"codeql[{exclusion.rule}]" not in source, (
            f"{exclusion.path} still carries an inline marker for {exclusion.rule}"
        )
