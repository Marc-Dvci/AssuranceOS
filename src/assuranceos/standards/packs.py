"""Loading, verifying, and admitting signed Audit Packs.

A pack on disk is not a pack the platform will run. Between the two sit four
checks, in this order, and the order is the point:

1. **signature and digest** — is this the artefact somebody released, unmodified;
2. **schema** — does it satisfy the published Audit Pack schema;
3. **typed manifest** — does it satisfy its own coherence rules (the graph
   resolves, every declared gate is enforced, every cited criterion exists);
4. **release status** — was it actually released rather than left in draft.

Cheapest-first would put schema before signature. Signature goes first anyway,
because parsing an unverified artefact means the parser is the attack surface.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from jsonschema import Draft202012Validator
from pydantic import ValidationError

from assuranceos.audit_pack_release import verify_audit_pack_release

from .definitions import PackManifest
from .exceptions import (
    PackNotFoundError,
    PackNotReleasedError,
    PackSchemaError,
    PackSignatureError,
)


@dataclass(frozen=True)
class LoadedAuditPack:
    """A pack that has passed every admission check."""

    pack_dir: Path
    manifest: PackManifest
    release_document: dict[str, Any]

    @property
    def package_sha256(self) -> str:
        return str(self.release_document["package_sha256"])

    @property
    def reference(self) -> str:
        return self.manifest.reference


class AuditPackRegistry:
    """Loads signed Audit Packs from a directory tree.

    ``trusted_public_key`` is required rather than optional. A registry that can
    be constructed without one has a mode in which it admits unsigned packs, and
    that mode will be the one someone reaches for when a signature is inconvenient.
    """

    def __init__(self, root: Path, *, trusted_public_key: bytes, schema_path: Path | None = None):
        self.root = Path(root).resolve()
        self.trusted_public_key = trusted_public_key
        self.schema_path = schema_path or (self.root / "schemas" / "audit_pack.schema.json")
        self._packs: dict[str, LoadedAuditPack] = {}

    # -- loading ---------------------------------------------------------------

    def _schema(self) -> dict[str, Any]:
        if not self.schema_path.is_file():
            raise PackSchemaError(f"Audit Pack schema is missing at {self.schema_path}")
        return json.loads(self.schema_path.read_text(encoding="utf-8"))

    def load(self) -> "AuditPackRegistry":
        """Admit every signed pack under the root.

        A directory with no ``pack.yaml`` is skipped rather than refused — the
        tree also carries the schema directory and contract READMEs — but a
        directory that *has* one and fails any check aborts the load. Partial
        admission would leave the registry's contents dependent on iteration
        order.
        """
        self._packs = {}
        for pack_dir in sorted(item for item in self.root.iterdir() if item.is_dir()):
            if not (pack_dir / "pack.yaml").is_file():
                continue
            pack = self.load_one(pack_dir)
            self._packs[pack.reference] = pack
        return self

    def load_one(self, pack_dir: Path) -> LoadedAuditPack:
        pack_dir = Path(pack_dir)
        manifest_path = pack_dir / "pack.yaml"
        if not manifest_path.is_file():
            raise PackNotFoundError(f"no pack.yaml in {pack_dir}")

        # 1. Signature and digest, before anything parses the content.
        try:
            release = verify_audit_pack_release(pack_dir, self.trusted_public_key)
        except ValueError as exc:
            raise PackSignatureError(str(exc)) from exc

        raw = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise PackSchemaError(f"{pack_dir.name}: pack.yaml must be a mapping")

        # 2. The published schema, so a pack authored against it is portable.
        errors = sorted(
            Draft202012Validator(self._schema()).iter_errors(raw), key=lambda item: list(item.path)
        )
        if errors:
            location = "/".join(str(part) for part in errors[0].path) or "<root>"
            raise PackSchemaError(f"{pack_dir.name}: {location}: {errors[0].message}")

        # 3. The pack's own coherence rules.
        try:
            manifest = PackManifest.model_validate(raw)
        except ValidationError as exc:
            first = exc.errors()[0]
            location = ".".join(str(part) for part in first["loc"]) or "<root>"
            raise PackSchemaError(f"{pack_dir.name}: {location}: {first['msg']}") from exc

        if release.get("pack_id") != manifest.pack_id or release.get("version") != manifest.version:
            raise PackSignatureError(
                f"{pack_dir.name}: release identity {release.get('pack_id')}@"
                f"{release.get('version')} does not match pack.yaml {manifest.reference}"
            )

        # 4. Released, and honest about being signed.
        if manifest.status != "released":
            raise PackNotReleasedError(
                f"{manifest.reference} is {manifest.status!r}; only a released pack compiles"
            )
        if not manifest.signed:
            raise PackSignatureError(
                f"{manifest.reference} carries a valid release signature but declares "
                "signed: false; the manifest and the artefact disagree"
            )

        return LoadedAuditPack(
            pack_dir=pack_dir, manifest=manifest, release_document=release
        )

    # -- access ----------------------------------------------------------------

    def get(self, pack_id: str, version: str) -> LoadedAuditPack:
        try:
            return self._packs[f"{pack_id}@{version}"]
        except KeyError as exc:
            available = ", ".join(sorted(self._packs)) or "none"
            raise PackNotFoundError(
                f"Audit Pack {pack_id}@{version} is not registered; available: {available}"
            ) from exc

    def list(self) -> list[LoadedAuditPack]:
        return [self._packs[key] for key in sorted(self._packs)]

    def versions(self, pack_id: str) -> list[str]:
        return sorted(
            pack.manifest.version for pack in self._packs.values() if pack.manifest.pack_id == pack_id
        )
