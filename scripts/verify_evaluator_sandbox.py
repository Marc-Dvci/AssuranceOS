"""Drive the evaluator sandbox against a real provider, end to end.

Contract tests prove the adapter parses what a provider is documented to send.
They cannot prove that the credential scheme is right, that the host allowlist
admits the real API, that pagination terminates, or that what arrives hashes to
something the vault will accept. Only a run against the provider does that, so
this script exists to be run rather than to be believed.

It creates a disposable workspace, attaches one provider, checks its health,
collects under a purpose-bound read-only grant, prints the digests that landed,
and deletes the workspace with its credential. Nothing it writes survives it.

    python scripts/verify_evaluator_sandbox.py --provider github \\
        --base-url https://api.github.com --scope Marc-Dvci/AssuranceOS

A public GitHub repository needs no credential. Every other provider does, and
the value is read from the environment rather than the command line, because a
command line is recorded by the shell:

    ASSURANCEOS_SANDBOX_TOKEN=... python scripts/verify_evaluator_sandbox.py \\
        --provider jira --base-url https://site.atlassian.net \\
        --stream issues --scope CHANGE --credential-field token \\
        --credential-field email=someone@example.com
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from assuranceos.db.session import Database  # noqa: E402
from assuranceos.evaluator_sandbox import (  # noqa: E402
    PROVIDERS,
    EvaluatorSandbox,
    SandboxError,
)
from assuranceos.vault import BaselineContentInspector, EvidenceVault  # noqa: E402

TOKEN_VARIABLE = "ASSURANCEOS_SANDBOX_TOKEN"


def _credentials(pairs: list[str]) -> dict[str, str]:
    """Read credential fields, taking the secret one from the environment.

    ``--credential-field token`` with no value means "read
    ASSURANCEOS_SANDBOX_TOKEN"; ``--credential-field email=x`` supplies a value
    that is not a secret. The asymmetry is the point: an API token typed into a
    command line ends up in shell history and in any process listing taken while
    it runs.
    """

    values: dict[str, str] = {}
    for pair in pairs:
        if "=" in pair:
            name, value = pair.split("=", 1)
            values[name.strip()] = value.strip()
            continue
        secret = os.getenv(TOKEN_VARIABLE)
        if not secret:
            raise SystemExit(f"{pair} takes its value from {TOKEN_VARIABLE}, which is not set")
        values[pair.strip()] = secret.strip()
    return values


def _write_receipt(path, profile, base_url, stream, scope, attached, health, run, records) -> None:
    """Record enough that the run can be argued with rather than believed.

    Deliberately absent: the credential, any header value, and the credential
    reference. Present: the host, the grant that authorised the read, the
    counts, and the first digest, so a reader can fetch the same object from the
    same provider and hash it themselves.
    """

    grant = attached["grant"]
    payload = {
        "schema": "assuranceos.live-collection-proof.v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "provider": {
            "connector_type": profile.connector_type,
            "display_name": profile.display_name,
            "host": urlsplit(base_url).hostname,
            "authenticated": attached["connector"]["authenticated"],
            "allowed_hosts": list(profile.allowed_hosts),
        },
        "grant": {
            "purpose": grant["purpose"],
            "allowed_streams": grant["allowed_streams"],
            "resource_selectors": grant["resource_selectors"],
            "read_only": grant["read_only"],
            "expires_at": grant["expires_at"],
        },
        "health": {"status": health["status"], "checked_at": health["checked_at"]},
        "run": {
            "stream": stream,
            "scope": scope,
            "status": run.status,
            "objects_seen": run.objects_seen,
            "objects_ingested": run.objects_ingested,
            "pages": run.metrics.get("pages"),
            "schema_fingerprint": run.schema_fingerprint,
        },
        "evidence": {
            "count": len(records),
            "first_locator": records[0].source_locator if records else None,
            "first_sha256": records[0].content_sha256 if records else None,
            "all_verified": all(r.integrity_status == "verified" for r in records),
        },
        "refusal": {
            "checked": "a collection outside the approved scope",
            "result": "refused",
        },
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8", newline="\n")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--provider", default="github", choices=sorted(PROVIDERS))
    parser.add_argument("--base-url")
    parser.add_argument("--stream")
    parser.add_argument("--scope", required=True, help="repository, project keys, space ids, ...")
    parser.add_argument("--company", default="Sandbox Verification Ltd")
    parser.add_argument(
        "--credential-field",
        action="append",
        default=[],
        metavar="NAME[=VALUE]",
        help=f"a value with no '=' is read from {TOKEN_VARIABLE}",
    )
    parser.add_argument("--keep", action="store_true", help="leave the workspace in place")
    parser.add_argument(
        "--receipt",
        type=Path,
        help="write a publishable record of this run (never the credential)",
    )
    args = parser.parse_args()

    profile = PROVIDERS[args.provider]
    base_url = args.base_url or profile.base_url_example
    stream = args.stream or profile.streams[0].name

    with tempfile.TemporaryDirectory(prefix="assuranceos-sandbox-") as work:
        root = Path(work)
        database = Database.from_sqlite_path(root / "sandbox.db")
        database.create_schema()
        try:
            sandbox = EvaluatorSandbox(
                database,
                EvidenceVault.local(database, root / "objects", inspector=BaselineContentInspector()),
            )
            workspace = sandbox.create_workspace(args.company)
            print(f"workspace   {workspace.workspace_id}  ({workspace.credential_storage})")

            attached = sandbox.connect(
                workspace.workspace_id,
                provider=args.provider,
                base_url=base_url,
                stream=stream,
                scope_value=args.scope,
                credentials=_credentials(args.credential_field),
            )
            connector_id = attached["connector"]["connector_instance_id"]
            print(f"connector   {profile.display_name} at {base_url}")
            print(f"            authenticated: {attached['connector']['authenticated']}")
            print(f"grant       {attached['grant']['purpose']}")
            print(f"            streams {attached['grant']['allowed_streams']}, "
                  f"scope {attached['grant']['resource_selectors']}, "
                  f"read-only {attached['grant']['read_only']}, "
                  f"expires {attached['grant']['expires_at']}")

            health = sandbox.health(workspace.workspace_id, connector_id)
            print(f"health      {health['status']}  {json.dumps(health['details'], default=str)}")
            if health["status"] != "healthy":
                raise SandboxError(f"provider health is {health['status']}")

            run = sandbox.collect(
                workspace.workspace_id, connector_id, stream=stream, scope_value=args.scope
            )
            print(
                f"collection  {run.status}: {run.objects_seen} seen, "
                f"{run.objects_ingested} ingested, {run.objects_unchanged} unchanged, "
                f"{run.metrics.get('pages')} page(s)"
            )

            records = sandbox.vault.list(workspace.tenant_id)
            print(f"evidence    {len(records)} records in the vault")
            for record in records[:3]:
                print(
                    f"            {record.content_sha256[:16]}...  "
                    f"{record.integrity_status}  {record.source_locator}"
                )

            # The refusal is part of the proof. A grant that only ever permits
            # has not been observed to be a grant at all.
            try:
                sandbox.collect(
                    workspace.workspace_id,
                    connector_id,
                    stream=stream,
                    scope_value="assuranceos-not-granted/elsewhere"
                    if args.provider == "github"
                    else "NOT-GRANTED",
                )
            except Exception as exc:
                print(f"refusal     out-of-scope collection refused: {type(exc).__name__}")
            else:
                raise SandboxError("an out-of-scope collection was NOT refused")

            if args.receipt:
                _write_receipt(args.receipt, profile, base_url, stream, args.scope, attached, health, run, records)
                print(f"receipt     {args.receipt}")

            if args.keep:
                print("workspace   kept")
            else:
                removed = sandbox.delete_workspace(workspace.workspace_id)
                print(f"teardown    workspace deleted, {removed['secrets_destroyed']} credential(s) destroyed")
            print("\nVERIFIED")
            return 0
        finally:
            database.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
