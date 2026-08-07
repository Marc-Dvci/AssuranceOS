from __future__ import annotations

import ast
import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml
from jsonschema import Draft202012Validator
from sqlalchemy import select

from assuranceos.db.models import ControlTestRelease
from assuranceos.db.session import Database

from .definitions import ControlTestManifest
from .exceptions import TestPackageError, TestReleaseConflictError, TestReleaseNotFoundError
from .release import canonical_json, verify_control_test_release


@dataclass(frozen=True)
class LoadedControlTest:
    package_dir: Path
    relative_path: str
    manifest: ControlTestManifest
    input_schema: dict[str, Any]
    output_schema: dict[str, Any]
    parameter_schema: dict[str, Any]
    release_document: dict[str, Any]
    signature_document: dict[str, Any]

    @property
    def release_id(self) -> str:
        digest = hashlib.sha256(f"{self.manifest.test_id}@{self.manifest.version}".encode()).hexdigest()
        return f"ctr_{digest[:24]}"

    @property
    def manifest_hash(self) -> str:
        return hashlib.sha256(canonical_json(self.manifest.model_dump(mode="json"))).hexdigest()

    @property
    def code_path(self) -> Path:
        relative = self.manifest.entrypoint.split(":", 1)[0]
        return self.package_dir / relative

    @property
    def code_hash(self) -> str:
        return hashlib.sha256(self.code_path.read_bytes()).hexdigest()


class ControlTestRegistry:
    """Loads immutable, signed deterministic-test packages and mirrors releases to SQL."""

    def __init__(self, root: Path, *, trusted_public_key: bytes):
        self.root = root.resolve()
        self.trusted_public_key = trusted_public_key
        self._releases: dict[tuple[str, str], LoadedControlTest] = {}

    def load(self) -> "ControlTestRegistry":
        releases: dict[tuple[str, str], LoadedControlTest] = {}
        for manifest_path in sorted(self.root.rglob("manifest.yaml")):
            package_dir = manifest_path.parent.resolve()
            try:
                raw_manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
                manifest = ControlTestManifest.model_validate(raw_manifest)
                input_schema = self._load_schema(package_dir / manifest.input_schema)
                output_schema = self._load_schema(package_dir / manifest.output_schema)
                parameter_schema = self._load_schema(package_dir / manifest.parameter_schema)
                release_document = verify_control_test_release(
                    package_dir, self.trusted_public_key
                )
                if manifest.engine == "python":
                    self._validate_python_source(
                        package_dir / manifest.entrypoint.split(":", 1)[0],
                        allowed_libraries=set(manifest.allowed_libraries),
                    )
                signature_document = json.loads(
                    (package_dir / "release.signature.json").read_text(encoding="utf-8")
                )
            except Exception as exc:
                raise TestPackageError(f"invalid control-test package {package_dir}: {exc}") from exc
            if release_document["test_id"] != manifest.test_id or release_document["version"] != manifest.version:
                raise TestPackageError(f"release identity mismatch in {package_dir}")
            loaded = LoadedControlTest(
                package_dir=package_dir,
                relative_path=package_dir.relative_to(self.root).as_posix(),
                manifest=manifest,
                input_schema=input_schema,
                output_schema=output_schema,
                parameter_schema=parameter_schema,
                release_document=release_document,
                signature_document=signature_document,
            )
            key = (manifest.test_id, manifest.version)
            if key in releases:
                raise TestPackageError(f"duplicate control-test release {manifest.test_id}@{manifest.version}")
            releases[key] = loaded
        self._releases = releases
        return self

    @staticmethod
    def _validate_python_source(path: Path, *, allowed_libraries: set[str]) -> None:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=path.as_posix())
        prohibited_calls = {"open", "exec", "eval", "compile", "__import__", "input"}
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = {item.name.split(".", 1)[0] for item in node.names}
                denied = sorted(names - allowed_libraries)
                if denied:
                    raise ValueError(f"unapproved Python imports: {denied}")
            elif isinstance(node, ast.ImportFrom):
                module = (node.module or "").split(".", 1)[0]
                if module != "__future__" and module not in allowed_libraries:
                    raise ValueError(f"unapproved Python import: {module or 'relative import'}")
            elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                if node.func.id in prohibited_calls:
                    raise ValueError(f"prohibited Python call: {node.func.id}")

    @staticmethod
    def _load_schema(path: Path) -> dict[str, Any]:
        schema = json.loads(path.read_text(encoding="utf-8"))
        Draft202012Validator.check_schema(schema)
        return schema

    def list(self, *, domain: str | None = None) -> list[LoadedControlTest]:
        releases = self._releases.values()
        if domain is not None:
            releases = (item for item in releases if item.manifest.domain == domain)
        return sorted(releases, key=lambda item: (item.manifest.test_id, item.manifest.version))

    def get(self, test_id: str, version: str) -> LoadedControlTest:
        try:
            return self._releases[(test_id, version)]
        except KeyError as exc:
            raise TestReleaseNotFoundError(f"control-test release not found: {test_id}@{version}") from exc

    def sync(self, database: Database, *, released_by: str = "release-pipeline") -> int:
        inserted = 0
        with database.transaction() as session:
            for loaded in self.list():
                existing = session.scalar(
                    select(ControlTestRelease).where(
                        ControlTestRelease.test_id == loaded.manifest.test_id,
                        ControlTestRelease.version == loaded.manifest.version,
                    )
                )
                values = self._model_values(loaded, released_by=released_by)
                if existing is None:
                    session.add(ControlTestRelease(**values))
                    inserted += 1
                    continue
                immutable = {
                    "package_hash": existing.package_hash,
                    "code_hash": existing.code_hash,
                    "manifest_hash": existing.manifest_hash,
                }
                expected = {key: values[key] for key in immutable}
                if immutable != expected:
                    raise TestReleaseConflictError(
                        f"registered release changed on disk: {loaded.manifest.test_id}@{loaded.manifest.version}"
                    )
        return inserted

    @staticmethod
    def _model_values(loaded: LoadedControlTest, *, released_by: str) -> dict[str, Any]:
        manifest = loaded.manifest
        released_at = datetime.fromisoformat(loaded.release_document["released_at"])
        return {
            "release_id": loaded.release_id,
            "test_id": manifest.test_id,
            "version": manifest.version,
            "domain": manifest.domain,
            "title": manifest.title,
            "description": manifest.description,
            "engine": manifest.engine,
            "entrypoint": manifest.entrypoint,
            "package_path": loaded.relative_path,
            "package_hash": loaded.release_document["package_sha256"],
            "code_hash": loaded.code_hash,
            "manifest_hash": loaded.manifest_hash,
            "input_schema_json": loaded.input_schema,
            "output_schema_json": loaded.output_schema,
            "parameter_schema_json": loaded.parameter_schema,
            "dataset_contracts_json": [item.model_dump(mode="json") for item in manifest.datasets],
            "reconciliation_policy_json": manifest.reconciliation.model_dump(mode="json"),
            "sampling_policy_json": manifest.sampling.model_dump(mode="json"),
            "resource_limits_json": manifest.resources.model_dump(mode="json"),
            "allowed_libraries_json": manifest.allowed_libraries,
            "release_status": manifest.release_status,
            "released_at": released_at,
            "released_by": released_by,
            "signature_key_id": loaded.signature_document.get("key_id"),
            "metadata_json": {"known_limitations": manifest.known_limitations},
        }
