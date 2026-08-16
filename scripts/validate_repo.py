from __future__ import annotations

import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

import yaml
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from jsonschema import Draft202012Validator

from assuranceos.agent_release import verify_agent_release
from assuranceos.audit_pack_release import verify_audit_pack_release
from assuranceos.control_testing import ControlTestRegistry
from assuranceos.standards import AuditPackRegistry

ROOT = Path(__file__).resolve().parents[1]
REQUIRED_PROMPT_SECTIONS = ["ROLE", "AUTHORITY", "NON_GOALS", "CANONICAL_CONTEXT", "OBJECTIVE", "REQUIRED_PROCEDURE", "TOOL_RULES", "EVIDENCE_RULES", "ABSTAIN_OR_ESCALATE_WHEN", "OUTPUT", "SELF_CHECK"]
REQUIRED_FILES = ["manifest.yaml", "system_prompt.md", "input.schema.json", "output.schema.json", "company_context.schema.json", "tools.yaml", "policy.yaml", "model_profiles.yaml", "evaluations.yaml", "known_limitations.md", "README.md", "release.json", "release.signature.json"]
# Named for what it holds, which is regular expressions, and not for what it
# looks for. A constant whose name reads as a secret makes every string that
# passes through it look like a leaked credential to a static analyser, and the
# scan then reports itself.
SCAN_PATTERNS = {
    "Google API key": re.compile(rb"AIza[0-9A-Za-z_-]{35}"),
    "GitHub token": re.compile(rb"gh[pousr]_[A-Za-z0-9]{36,255}"),
    "AWS access key": re.compile(rb"(?:AKIA|ASIA)[A-Z0-9]{16}"),
    "Slack token": re.compile(rb"xox[baprs]-[A-Za-z0-9-]{20,}"),
}
PUBLIC_TEST_SENTINELS = (b"AKIAIOSFODNN7EXAMPLE",)


def fail(message: str) -> None:
    # CodeQL reads a path from the secret scan to this line and calls it
    # clear-text logging of sensitive data. The scan reports the pattern's name
    # and the file it matched in, never the matched bytes, which is the whole
    # point of a secret check that is safe to run in CI logs.
    print(f"ERROR: {message}", file=sys.stderr)  # codeql[py/clear-text-logging-sensitive-data]
    raise SystemExit(1)


def _tracked_pem_files() -> list[Path]:
    """Every ``.pem`` git tracks, or every one outside ``var/`` if git is absent.

    The fallback is deliberately the *stricter* reading of "outside the ignored
    working directories": a release archive extracted without a git directory
    should still be checked, and the only thing it can safely assume is the
    documented location for local keys.
    """
    try:
        listing = subprocess.run(
            ["git", "ls-files", "-z", "*.pem"],
            cwd=ROOT,
            capture_output=True,
            check=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return [
            path
            for path in ROOT.rglob("*.pem")
            if "var" not in path.relative_to(ROOT).parts
            and ".venv" not in path.relative_to(ROOT).parts
        ]
    return [
        ROOT / name.decode("utf-8")
        for name in listing.stdout.split(b"\0")
        if name and (ROOT / name.decode("utf-8")).is_file()
    ]


def _tracked_files() -> list[Path]:
    try:
        listing = subprocess.run(
            ["git", "ls-files", "-z"],
            cwd=ROOT,
            capture_output=True,
            check=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return []
    return [
        ROOT / name.decode("utf-8")
        for name in listing.stdout.split(b"\0")
        if name and (ROOT / name.decode("utf-8")).is_file()
    ]


def _reject_committed_secrets() -> None:
    for path in _tracked_files():
        if path.stat().st_size > 2_000_000:
            continue
        content = path.read_bytes()
        for sentinel in PUBLIC_TEST_SENTINELS:
            content = content.replace(sentinel, b"")
        for pattern_name, pattern in SCAN_PATTERNS.items():
            if pattern.search(content):
                fail(f"{pattern_name} pattern found in tracked file: {path.relative_to(ROOT)}")


def main() -> None:
    _reject_committed_secrets()
    agent_dirs = sorted(path for path in (ROOT / "agents").iterdir() if path.is_dir())
    if len(agent_dirs) != 19:
        fail(f"expected 19 agent packages, found {len(agent_dirs)}")

    release_key = (ROOT / "security/release-keys/agent-release-public.pem").read_bytes()
    execution_key_path = ROOT / "security/release-keys/execution-envelope-public.pem"
    if not execution_key_path.is_file():
        fail("missing execution-envelope public key")
    execution_key = serialization.load_pem_public_key(execution_key_path.read_bytes())
    if not isinstance(execution_key, Ed25519PublicKey):
        fail("execution-envelope trust key must be Ed25519")
    # The invariant is "no private key is *committed*", so the set walked is the
    # set git tracks. Walking the whole tree instead also flags a signing key an
    # operator legitimately keeps in the gitignored `var/`, which trains people to
    # ignore the check — the failure mode worth avoiding here.
    for pem_path in _tracked_pem_files():
        if b"PRIVATE KEY" in pem_path.read_bytes():
            fail(f"private key material must not be committed: {pem_path.relative_to(ROOT)}")

    for agent_dir in agent_dirs:
        for filename in REQUIRED_FILES:
            if not (agent_dir / filename).exists():
                fail(f"{agent_dir.name}: missing {filename}")
        manifest = yaml.safe_load((agent_dir / "manifest.yaml").read_text())
        tools = yaml.safe_load((agent_dir / "tools.yaml").read_text())
        policy = yaml.safe_load((agent_dir / "policy.yaml").read_text())
        prompt = (agent_dir / "system_prompt.md").read_text()
        if manifest["agent_id"] != agent_dir.name:
            fail(f"{agent_dir.name}: manifest id mismatch")
        for section in REQUIRED_PROMPT_SECTIONS:
            if f"# {section}" not in prompt:
                fail(f"{agent_dir.name}: missing prompt section {section}")
        tool_names = {tool["name"] for tool in tools["tools"]}
        if len(tool_names) != len(tools["tools"]):
            fail(f"{agent_dir.name}: duplicate tool declaration")
        if policy.get("default_effect") != "deny":
            fail(f"{agent_dir.name}: policy must default deny")
        for schema_name in ["input.schema.json", "output.schema.json", "company_context.schema.json"]:
            schema = json.loads((agent_dir / schema_name).read_text())
            Draft202012Validator.check_schema(schema)
        prompt_hash = hashlib.sha256(prompt.encode()).hexdigest()
        if manifest.get("status") != "released":
            fail(f"{agent_dir.name}: package is not released")
        if manifest.get("release", {}).get("prompt_hash") != prompt_hash:
            fail(f"{agent_dir.name}: prompt hash does not match release metadata")
        try:
            verify_agent_release(agent_dir, release_key)
        except ValueError as exc:
            fail(str(exc))

    # Audit Packs carry their own release key. Two artefact classes, two keys, so
    # compromising the agent-package review path does not let anyone publish a
    # methodology.
    pack_key_path = ROOT / "security/release-keys/audit-pack-release-public.pem"
    if not pack_key_path.is_file():
        fail("missing Audit Pack release public key")
    pack_key = pack_key_path.read_bytes()

    pack_schema = json.loads((ROOT / "audit-packs/schemas/audit_pack.schema.json").read_text())
    pack_dirs = sorted(
        path for path in (ROOT / "audit-packs").iterdir() if (path / "pack.yaml").is_file()
    )
    if not pack_dirs:
        fail("no Audit Pack carries a pack.yaml")
    packs = []
    for pack_dir in pack_dirs:
        pack = yaml.safe_load((pack_dir / "pack.yaml").read_text(encoding="utf-8"))
        errors = sorted(
            Draft202012Validator(pack_schema).iter_errors(pack), key=lambda e: list(e.path)
        )
        if errors:
            fail(f"{pack_dir.name}: audit pack invalid: {errors[0].message}")
        if pack.get("status") != "released" or pack.get("signed") is not True:
            fail(f"{pack_dir.name}: Audit Pack is not released and signed")
        try:
            release = verify_audit_pack_release(pack_dir, pack_key)
        except ValueError as exc:
            fail(str(exc))
        if release.get("pack_id") != pack.get("pack_id") or release.get(
            "version"
        ) != pack.get("version"):
            fail(f"{pack_dir.name}: Audit Pack release identity does not match pack.yaml")
        packs.append(f"{pack['pack_id']}@{pack['version']}")

    # Loading through the registry proves the packs satisfy the typed manifest as
    # well as the schema: that the procedure graph resolves, that every declared
    # human gate is enforced by a procedure, that cited criteria exist.
    try:
        AuditPackRegistry(ROOT / "audit-packs", trusted_public_key=pack_key).load()
    except Exception as exc:
        fail(f"Audit Pack registry rejected a released pack: {exc}")

    control_test_key_path = ROOT / "security/release-keys/control-test-release-public.pem"
    if not control_test_key_path.is_file():
        fail("missing control-test release public key")
    try:
        control_tests = ControlTestRegistry(
            ROOT / "tests-library",
            trusted_public_key=control_test_key_path.read_bytes(),
        ).load().list()
    except Exception as exc:
        fail(str(exc))
    # Two properties, neither of them a fixed inventory. An exact set fails on
    # the next procedure the library gains, which says nothing about whether the
    # library is sound, and the two things actually worth failing on are a
    # required procedure going missing and a package on disk that did not
    # produce a verified release.
    released = {(item.manifest.test_id, item.manifest.version) for item in control_tests}
    required = {("SCM-01", "2.0.0"), ("IAM-01", "1.0.0"), ("SLA-01", "1.0.0"), ("SCM-02", "1.0.0")}
    if missing := sorted(required - released):
        fail(f"required control-test releases are missing: {missing}")
    packaged = sum(
        1
        for path in (ROOT / "tests-library").rglob("manifest.yaml")
        if "__pycache__" not in path.parts
    )
    if len(control_tests) != packaged:
        fail(
            f"{packaged} control-test package(s) on disk produced {len(control_tests)} "
            "verified release(s); one did not load"
        )

    print(
        f"Validated {len(agent_dirs)} signed agent packages, common schemas, "
        f"{len(packs)} signed Audit Packs ({', '.join(packs)}), "
        f"{len(control_tests)} signed control-test releases, "
        "and execution-envelope trust material."
    )


if __name__ == "__main__":
    main()
