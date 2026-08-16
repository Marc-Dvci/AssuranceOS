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

Inline ``# codeql[rule-id]`` suppressions are honoured, and counted separately
so that a suppression is visible in the log rather than silent. A suppression
lives beside the line it excuses, where the reason for it can be read.

    python scripts/check_codeql_sarif.py var/codeql-results
    python scripts/check_codeql_sarif.py var/codeql-results --threshold 9.0
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys
from collections import defaultdict
from typing import Any

DEFAULT_THRESHOLD = 7.0


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


def _location(result: dict[str, Any]) -> str:
    for location in result.get("locations", []):
        physical = location.get("physicalLocation", {})
        uri = physical.get("artifactLocation", {}).get("uri")
        if not uri:
            continue
        line = physical.get("region", {}).get("startLine")
        return f"{uri}:{line}" if line else uri
    return "(no location)"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=pathlib.Path)
    parser.add_argument("--threshold", type=float, default=DEFAULT_THRESHOLD)
    args = parser.parse_args()

    files = sorted(args.directory.glob("*.sarif"))
    if not files:
        print(f"No SARIF files in {args.directory}", file=sys.stderr)
        return 1

    blocking: list[str] = []
    suppressed: list[str] = []
    reported: dict[str, list[str]] = defaultdict(list)

    for path in files:
        document = json.loads(path.read_text(encoding="utf-8"))
        for run in document.get("runs", []):
            rules = _rule_index(run)
            for result in run.get("results", []):
                rule_id = result.get("ruleId", "(unknown rule)")
                rule = rules.get(rule_id, {})
                severity = _severity(rule)
                message = result.get("message", {}).get("text", "").strip()
                entry = f"{rule_id}  {_location(result)}\n    {message}"
                if result.get("suppressions"):
                    suppressed.append(entry)
                elif severity is not None and severity >= args.threshold:
                    blocking.append(f"{severity:.1f}  {entry}")
                else:
                    label = "no security severity" if severity is None else f"{severity:.1f}"
                    reported[label].append(entry)

    total = len(blocking) + len(suppressed) + sum(len(items) for items in reported.values())
    print(f"CodeQL: {total} results across {len(files)} SARIF files")

    if suppressed:
        print(f"\n  suppressed in source: {len(suppressed)}")
        for entry in suppressed:
            print(f"    {entry}")

    for label in sorted(reported, reverse=True):
        print(f"\n  below threshold ({label}): {len(reported[label])}")
        for entry in reported[label]:
            print(f"    {entry}")

    if blocking:
        print(f"\nFAIL: {len(blocking)} at or above security severity {args.threshold}")
        for entry in blocking:
            print(f"  {entry}")
        return 1

    print(f"\nOK: nothing at or above security severity {args.threshold}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
