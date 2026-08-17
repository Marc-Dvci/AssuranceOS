"""Fail the build on CodeQL findings, without depending on code scanning.

Uploading SARIF to the code-scanning API needs code scanning enabled, and a
private repository without Advanced Security does not have it. The analysis is
the part that matters, so the workflow keeps running CodeQL and this script
decides the outcome from the SARIF the run produced. The gate therefore behaves
the same whether the repository is public, private, or moving between the two.

A finding fails the build when its rule carries a security severity at or above
the threshold, which is CVSS-style and set to 7.0, the floor GitHub itself uses
for "high". Everything below that is printed rather than ignored, because a
report nobody prints is a report nobody reads.

Two things can excuse a finding. A suppression carried in the SARIF itself, which
is what the ingesting service writes once it has accepted one, is honoured and
counted separately. And ``security/codeql-exclusions.toml`` declares the findings
this repository has read and decided are not defects, keyed on rule and file, each
carrying the reason it was excused and the tests the reasoning rests on. That
declaration is printed with its reason on every run, and an entry matching no
finding fails the build, so an exclusion cannot outlive the thing it excused.

    python scripts/check_codeql_sarif.py var/codeql-results
    python scripts/check_codeql_sarif.py var/codeql-results --threshold 9.0
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys
import textwrap
import tomllib
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

DEFAULT_THRESHOLD = 7.0
ROOT = pathlib.Path(__file__).resolve().parents[1]
DEFAULT_EXCLUSIONS = ROOT / "security" / "codeql-exclusions.toml"


@dataclass(frozen=True)
class Exclusion:
    """One declared decision not to treat a rule's findings in a file as defects."""

    rule: str
    path: str
    reason: str
    controls: tuple[str, ...] = ()

    def covers(self, rule_id: str, uri: str | None) -> bool:
        return rule_id == self.rule and uri == self.path


@dataclass
class Outcome:
    blocking: list[str] = field(default_factory=list)
    suppressed: list[str] = field(default_factory=list)
    excluded: dict[int, list[str]] = field(default_factory=lambda: defaultdict(list))
    reported: dict[str, list[str]] = field(default_factory=lambda: defaultdict(list))

    @property
    def total(self) -> int:
        return (
            len(self.blocking)
            + len(self.suppressed)
            + sum(len(items) for items in self.excluded.values())
            + sum(len(items) for items in self.reported.values())
        )


def load_exclusions(path: pathlib.Path) -> list[Exclusion]:
    """Read the declared exclusions, refusing an entry that omits its reasoning.

    An exclusion without a reason is the comment-beside-the-line problem again in
    a different file, so the loader will not accept one.
    """
    document = tomllib.loads(path.read_text(encoding="utf-8"))
    exclusions = []
    for index, entry in enumerate(document.get("exclusion", []), start=1):
        missing = [key for key in ("rule", "path", "reason") if not str(entry.get(key, "")).strip()]
        if missing:
            raise ValueError(f"exclusion {index} in {path.name} is missing: {', '.join(missing)}")
        exclusions.append(
            Exclusion(
                rule=entry["rule"],
                path=entry["path"],
                reason=entry["reason"].strip(),
                controls=tuple(entry.get("controls", [])),
            )
        )
    return exclusions


def _rule_index(run: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Rules by id, from wherever this SARIF producer put them."""
    driver = run.get("tool", {}).get("driver", {})
    rules = list(driver.get("rules", []))
    for extension in run.get("tool", {}).get("extensions", []):
        rules.extend(extension.get("rules", []))
    return {rule["id"]: rule for rule in rules if "id" in rule}


def _severity(rule: dict[str, Any]) -> float | None:
    raw = rule.get("properties", {}).get("security-severity")
    if raw is None:
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def _uri(result: dict[str, Any]) -> str | None:
    for location in result.get("locations", []):
        uri = location.get("physicalLocation", {}).get("artifactLocation", {}).get("uri")
        if uri:
            return uri
    return None


def _location(result: dict[str, Any]) -> str:
    for location in result.get("locations", []):
        physical = location.get("physicalLocation", {})
        uri = physical.get("artifactLocation", {}).get("uri")
        if not uri:
            continue
        line = physical.get("region", {}).get("startLine")
        return f"{uri}:{line}" if line else uri
    return "(no location)"


def classify(
    documents: list[dict[str, Any]],
    *,
    threshold: float,
    exclusions: list[Exclusion],
) -> Outcome:
    """Sort every result into suppressed, declared, blocking or merely reported."""
    outcome = Outcome()
    for document in documents:
        for run in document.get("runs", []):
            rules = _rule_index(run)
            for result in run.get("results", []):
                rule_id = result.get("ruleId", "(unknown rule)")
                rule = rules.get(rule_id, {})
                severity = _severity(rule)
                message = result.get("message", {}).get("text", "").strip()
                entry = f"{rule_id}  {_location(result)}\n    {message}"
                declared = next(
                    (
                        index
                        for index, exclusion in enumerate(exclusions)
                        if exclusion.covers(rule_id, _uri(result))
                    ),
                    None,
                )
                if result.get("suppressions"):
                    outcome.suppressed.append(entry)
                elif declared is not None:
                    outcome.excluded[declared].append(entry)
                elif severity is not None and severity >= threshold:
                    outcome.blocking.append(f"{severity:.1f}  {entry}")
                else:
                    label = "no security severity" if severity is None else f"{severity:.1f}"
                    outcome.reported[label].append(entry)
    return outcome


def report(outcome: Outcome, exclusions: list[Exclusion], *, threshold: float) -> int:
    """Print every result and return the exit status the build should take."""
    if outcome.suppressed:
        print(f"\n  suppressed in the report: {len(outcome.suppressed)}")
        for entry in outcome.suppressed:
            print(f"    {entry}")

    for index, exclusion in enumerate(exclusions):
        entries = outcome.excluded.get(index, [])
        print(f"\n  declared not a defect ({exclusion.rule} in {exclusion.path}): {len(entries)}")
        for line in textwrap.wrap(" ".join(exclusion.reason.split()), width=88):
            print(f"    {line}")
        for control in exclusion.controls:
            print(f"    held up by {control}")
        for entry in entries:
            print(f"      {entry}")

    for label in sorted(outcome.reported, reverse=True):
        print(f"\n  below threshold ({label}): {len(outcome.reported[label])}")
        for entry in outcome.reported[label]:
            print(f"    {entry}")

    stale = [exclusion for index, exclusion in enumerate(exclusions) if not outcome.excluded.get(index)]
    if stale:
        print(f"\nFAIL: {len(stale)} declared exclusion(s) match nothing in this report")
        for exclusion in stale:
            print(f"  {exclusion.rule} in {exclusion.path}")
        print("  The finding is gone, so remove the exclusion with it.")
        return 1

    if outcome.blocking:
        print(f"\nFAIL: {len(outcome.blocking)} at or above security severity {threshold}")
        for entry in outcome.blocking:
            print(f"  {entry}")
        return 1

    print(f"\nOK: nothing at or above security severity {threshold}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=pathlib.Path)
    parser.add_argument("--threshold", type=float, default=DEFAULT_THRESHOLD)
    parser.add_argument("--exclusions", type=pathlib.Path, default=DEFAULT_EXCLUSIONS)
    args = parser.parse_args()

    files = sorted(args.directory.glob("*.sarif"))
    if not files:
        print(f"No SARIF files in {args.directory}", file=sys.stderr)
        return 1

    if not args.exclusions.is_file():
        print(f"No exclusion declaration at {args.exclusions}", file=sys.stderr)
        return 1
    exclusions = load_exclusions(args.exclusions)

    documents = [json.loads(path.read_text(encoding="utf-8")) for path in files]
    outcome = classify(documents, threshold=args.threshold, exclusions=exclusions)
    print(f"CodeQL: {outcome.total} results across {len(files)} SARIF files")
    return report(outcome, exclusions, threshold=args.threshold)


if __name__ == "__main__":
    raise SystemExit(main())
