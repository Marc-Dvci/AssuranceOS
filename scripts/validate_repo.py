from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import yaml
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from jsonschema import Draft202012Validator

from assuranceos.agent_release import verify_agent_release
from assuranceos.audit_pack_release import verify_audit_pack_release
from assuranceos.control_testing import ControlTestRegistry

ROOT = Path(__file__).resolve().parents[1]
REQUIRED_PROMPT_SECTIONS = ["ROLE", "AUTHORITY", "NON_GOALS", "CANONICAL_CONTEXT", "OBJECTIVE", "REQUIRED_PROCEDURE", "TOOL_RULES", "EVIDENCE_RULES", "ABSTAIN_OR_ESCALATE_WHEN", "OUTPUT", "SELF_CHECK"]
REQUIRED_FILES = ["manifest.yaml", "system_prompt.md", "input.schema.json", "output.schema.json", "company_context.schema.json", "tools.yaml", "policy.yaml", "model_profiles.yaml", "evaluations.yaml", "known_limitations.md", "README.md", "release.json", "release.signature.json"]


def fail(message: str) -> None:
    print(f"ERROR: {message}", file=sys.stderr)
    raise SystemExit(1)


def main() -> None:
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
    for pem_path in ROOT.rglob("*.pem"):
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

    pack_schema = json.loads((ROOT / "audit-packs/schemas/audit_pack.schema.json").read_text())
    pack = yaml.safe_load((ROOT / "audit-packs/software-change-management/pack.yaml").read_text())
    errors = sorted(Draft202012Validator(pack_schema).iter_errors(pack), key=lambda e: e.path)
    if errors:
        fail(f"audit pack invalid: {errors[0].message}")
    if pack.get("status") != "released" or pack.get("signed") is not True:
        fail("software-change-management Audit Pack is not released and signed")
    try:
        release = verify_audit_pack_release(
            ROOT / "audit-packs/software-change-management", release_key
        )
    except ValueError as exc:
        fail(str(exc))
    if release.get("pack_id") != pack.get("pack_id") or release.get("version") != pack.get("version"):
        fail("Audit Pack release identity does not match pack.yaml")

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
    if {(item.manifest.test_id, item.manifest.version) for item in control_tests} != {
        ("SCM-01", "2.0.0"),
        ("IAM-01", "1.0.0"),
    }:
        fail("expected released SCM-01 and IAM-01 control tests")

    print(
        f"Validated {len(agent_dirs)} signed agent packages, common schemas, "
        f"the signed Audit Pack, {len(control_tests)} signed control-test releases, "
        "and execution-envelope trust material."
    )


if __name__ == "__main__":
    main()
