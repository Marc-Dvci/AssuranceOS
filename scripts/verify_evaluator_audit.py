"""Run the whole evaluator audit against a real repository, and print what it found.

The sandbox verification proves a connector reads a provider. This proves the
rest: that what it read reconciles into a declared population, that a procedure
signed before it saw the data reaches a conclusion inside its sandbox, that a
governed agent ran the procedure rather than forming an opinion, that a tool
outside its envelope is denied, and that what comes out is a finding waiting for
a person.

    python scripts/verify_evaluator_audit.py --repository Marc-Dvci/AssuranceOS --days 2

A public repository needs no credential, at sixty requests an hour. The audit
costs roughly one request per commit, so a wide period on a busy repository wants
a token:

    ASSURANCEOS_SANDBOX_TOKEN=github_pat_... python scripts/verify_evaluator_audit.py \\
        --repository owner/repo --days 30 --authenticate

Nothing it writes survives it: the workspace, its credential and its evidence go
away at the end unless --keep is passed.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from assuranceos.db.session import Database  # noqa: E402
from assuranceos.evaluator_audit import AuditRequest, WorkspaceAudit  # noqa: E402
from assuranceos.evaluator_sandbox import EvaluatorSandbox, SandboxLimits  # noqa: E402
from assuranceos.vault import BaselineContentInspector, EvidenceVault  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
TOKEN_VARIABLE = "ASSURANCEOS_SANDBOX_TOKEN"


def _model_client(mode: str):
    if mode == "mock":
        return None
    from assuranceos.governance.models_client import build_client

    return build_client(
        mode,
        model=os.getenv("ASSURANCEOS_GEMINI_MODEL") if "gem" in mode else None,
        project=os.getenv("GOOGLE_CLOUD_PROJECT"),
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", required=True, help="owner/repository")
    parser.add_argument("--days", type=int, default=7)
    parser.add_argument("--required-approvals", type=int, default=1)
    parser.add_argument("--company", default="Audit Verification Ltd")
    parser.add_argument("--model-mode", default="mock", choices=["mock", "vertex", "gemini", "local"])
    parser.add_argument(
        "--authenticate",
        action="store_true",
        help=f"send a token read from {TOKEN_VARIABLE}",
    )
    parser.add_argument("--keep", action="store_true")
    parser.add_argument("--receipt", type=Path)
    args = parser.parse_args()

    credentials: dict[str, str] = {}
    if args.authenticate:
        token = os.getenv(TOKEN_VARIABLE)
        if not token:
            raise SystemExit(f"--authenticate needs {TOKEN_VARIABLE}")
        credentials["token"] = token.strip()

    end = datetime.now(timezone.utc).date()
    start = end - timedelta(days=args.days)

    with tempfile.TemporaryDirectory(prefix="assuranceos-audit-") as work:
        root = Path(work)
        database = Database.from_sqlite_path(root / "audit.db")
        database.create_schema()
        try:
            sandbox = EvaluatorSandbox(
                database,
                EvidenceVault.local(
                    database, root / "objects", inspector=BaselineContentInspector()
                ),
                limits=SandboxLimits(audit_period_days=max(args.days, 30)),
            )
            workspace = sandbox.create_workspace(args.company)
            attached = sandbox.connect(
                workspace.workspace_id,
                provider="github",
                base_url="https://api.github.com",
                stream="pull_requests",
                scope_value=args.repository,
                credentials=credentials,
            )
            connector_id = attached["connector"]["connector_instance_id"]
            print(f"workspace   {workspace.workspace_id}")
            print(f"connector   GitHub, authenticated: {attached['connector']['authenticated']}")

            audit = WorkspaceAudit(
                sandbox, repository_root=ROOT, model_client=_model_client(args.model_mode)
            )
            report = audit.run(
                AuditRequest(
                    workspace_id=workspace.workspace_id,
                    connector_instance_id=connector_id,
                    repository=args.repository,
                    period_start=start,
                    period_end=end,
                    required_approvals=args.required_approvals,
                )
            )
            _print(report)
            if args.receipt:
                args.receipt.parent.mkdir(parents=True, exist_ok=True)
                args.receipt.write_text(
                    json.dumps(_receipt(report), indent=2) + "\n", encoding="utf-8", newline="\n"
                )
                print(f"receipt     {args.receipt}")
            if not args.keep:
                sandbox.delete_workspace(workspace.workspace_id)
                print("teardown    workspace deleted")
            return 0 if _acceptable(report) else 1
        finally:
            database.dispose()


def _print(report: dict) -> None:
    collection, test, agent, finding = (
        report["collection"],
        report["control_test"],
        report["agent"],
        report["finding"],
    )
    print(f"period      {report['period']['start']} to {report['period']['end']}")
    print(f"grant       {report['grant']['allowed_streams']}, read-only "
          f"{report['grant']['read_only']}, revoked {report['grant']['revoked']['status']}")
    print(f"collected   {collection['commits']['objects_ingested']} commits, "
          f"{collection['commit_reviews']['objects_ingested']} review paths")
    for note in report["population"]["notes"]:
        print(f"            note: {note}")
    print(f"procedure   {test['test_id']}@{test['version']} -> {test['conclusion']}")
    print(f"            population {test['population_count']}, complete "
          f"{test['population_complete']}, exceptions {test['exception_count']}")
    print(f"            result digest {str(test['result_manifest_hash'])[:24]}...")
    for item in (test["exceptions"] or [])[:5]:
        print(f"            {item['classification']}: {item['subject_ref']}")
    print(f"agent       {agent['model']}, {len(agent['tool_calls_allowed'])} tool call(s), "
          f"{len(agent['denials'])} denial(s) -> {agent['conclusion']}")
    for denial in agent["denials"] or []:
        detail = denial if isinstance(denial, str) else json.dumps(denial, default=str)
        print(f"            denied: {detail[:300]}")
        if "enforced sandbox" in detail or "resource limits cannot be enforced" in detail:
            # The deterministic runtime refuses to run where it cannot impose
            # POSIX resource limits, which is the intended production behaviour
            # and reads as a mystery on a Windows laptop.
            print(
                "            this platform has no POSIX rlimits; re-run with "
                "ASSURANCEOS_CONTROL_TEST_ALLOW_DEGRADED_SANDBOX=true"
            )
    print(f"            {str(agent['summary'] or '')[:200]}")
    probe = agent["boundary_probe"]
    print(f"boundary    connector.write denied: {probe['denied']} ({probe.get('stage')})")
    if finding.get("proposed"):
        print(f"finding     {finding['finding_id']} {finding['severity']}, {finding['status']}")
        print(f"            {finding['title']}")
    else:
        print(f"finding     none proposed: {finding.get('reason')}")


def _acceptable(report: dict) -> bool:
    """What has to hold for this run to be worth anything."""

    test, agent = report["control_test"], report["agent"]
    checks = {
        "the signed procedure ran": test.get("status") == "succeeded",
        "the population reconciled": bool(test.get("population_complete")),
        "the agent executed the procedure": bool(test.get("run_id")),
        "a tool outside the envelope was denied": bool(agent["boundary_probe"]["denied"]),
        # Without this the script passes on a run whose model reply was cut off
        # part-way through its JSON, which is a broken demonstration sitting on
        # top of a sound signed result.
        "the agent reached a conclusion": bool(agent.get("conclusion")),
        "the grant was revoked": report["grant"]["revoked"]["status"] == "revoked",
    }
    for name, passed in checks.items():
        if not passed:
            print(f"FAILED      {name}")
    print()
    print("VERIFIED" if all(checks.values()) else "NOT VERIFIED")
    return all(checks.values())


def _receipt(report: dict) -> dict:
    """A publishable record. No credential, no header, no evidence content."""

    test = report["control_test"]
    return {
        "schema": "assuranceos.evaluator-audit-proof.v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "repository": report["repository"],
        "period": report["period"],
        "grant": {
            "allowed_streams": report["grant"]["allowed_streams"],
            "read_only": report["grant"]["read_only"],
            "revoked_at": report["grant"]["revoked"]["revoked_at"],
        },
        "collection": report["collection"],
        "control_test": {
            "test_id": test["test_id"],
            "version": test["version"],
            "conclusion": test["conclusion"],
            "population_count": test["population_count"],
            "population_complete": test["population_complete"],
            "exception_count": test["exception_count"],
            "result_manifest_hash": test["result_manifest_hash"],
        },
        "agent": {
            "model": report["agent"]["model"],
            "conclusion": report["agent"]["conclusion"],
            "denied_out_of_envelope_tool": report["agent"]["boundary_probe"]["denied"],
        },
        "finding": {
            "proposed": report["finding"].get("proposed"),
            "severity": report["finding"].get("severity"),
            "status": report["finding"].get("status"),
        },
    }


if __name__ == "__main__":
    raise SystemExit(main())
