from __future__ import annotations

import hashlib
import json
import zipfile
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Any

from .custody import custody_event_hash
from .definitions import ExportVerification
from .exceptions import ExportPackageError
from .signing import ManifestSigner, signature_document, verify_signature

_FIXED_ZIP_TIME = (1980, 1, 1, 0, 0, 0)
_REQUIRED_ENTRIES = {"manifest.json", "manifest.sha256"}
_SIGNATURE_ENTRIES = {"manifest.signature.json", "manifest.public.pem"}


def canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")


def sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _zip_info(name: str) -> zipfile.ZipInfo:
    info = zipfile.ZipInfo(filename=name, date_time=_FIXED_ZIP_TIME)
    info.compress_type = zipfile.ZIP_DEFLATED
    info.external_attr = 0o100444 << 16
    return info


def _parse_datetime(value: Any) -> datetime:
    if not isinstance(value, str):
        raise ValueError("custody timestamp is not a string")
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def write_export_package(
    destination: Path,
    *,
    manifest: dict[str, Any],
    objects: dict[str, bytes],
    signer: ManifestSigner | None = None,
) -> tuple[str, str]:
    destination.parent.mkdir(parents=True, exist_ok=True)
    manifest_bytes = canonical_json_bytes(manifest)
    manifest_sha256 = hashlib.sha256(manifest_bytes).hexdigest()
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    try:
        with zipfile.ZipFile(temporary, "w") as archive:
            archive.writestr(_zip_info("manifest.json"), manifest_bytes)
            archive.writestr(
                _zip_info("manifest.sha256"),
                f"{manifest_sha256}  manifest.json\n".encode("ascii"),
            )
            if signer is not None:
                archive.writestr(
                    _zip_info("manifest.signature.json"),
                    canonical_json_bytes(signature_document(signer=signer, payload=manifest_bytes)),
                )
                archive.writestr(_zip_info("manifest.public.pem"), signer.public_key_pem())
            for digest in sorted(objects):
                archive.writestr(_zip_info(f"objects/{digest}"), objects[digest])
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)
    return sha256_path(destination), manifest_sha256


def _verify_custody(
    *, tenant_id: str, evidence_id: str, events: Any, declared_head: Any
) -> list[str]:
    errors: list[str] = []
    if not isinstance(events, list):
        return [f"evidence {evidence_id} custody is not a list"]
    previous_hash: str | None = None
    for expected_sequence, event in enumerate(events, start=1):
        if not isinstance(event, dict):
            errors.append(f"evidence {evidence_id} custody event is not an object")
            continue
        if event.get("sequence_no") != expected_sequence:
            errors.append(
                f"evidence {evidence_id} custody sequence mismatch at {expected_sequence}"
            )
            continue
        try:
            calculated = custody_event_hash(
                tenant_id=tenant_id,
                evidence_id=evidence_id,
                sequence_no=expected_sequence,
                action=event["action"],
                actor_type=event["actor_type"],
                actor_id=event["actor_id"],
                occurred_at=_parse_datetime(event["occurred_at"]),
                details=event.get("details", {}),
                previous_event_hash=previous_hash,
            )
        except (KeyError, TypeError, ValueError) as exc:
            errors.append(f"evidence {evidence_id} custody event is malformed: {exc}")
            continue
        if event.get("previous_event_hash") != previous_hash:
            errors.append(
                f"evidence {evidence_id} custody previous hash mismatch at {expected_sequence}"
            )
        if event.get("event_hash") != calculated:
            errors.append(
                f"evidence {evidence_id} custody event hash mismatch at {expected_sequence}"
            )
        previous_hash = event.get("event_hash")
    if declared_head != previous_hash:
        errors.append(f"evidence {evidence_id} custody head mismatch")
    return errors


def verify_export_package(
    path: Path, *, trusted_public_keys: dict[str, bytes] | None = None
) -> ExportVerification:
    errors: list[str] = []
    manifest_sha256: str | None = None
    evidence_count = 0
    object_count = 0
    signature_valid: bool | None = None
    signing_key_id: str | None = None
    try:
        package_sha256 = sha256_path(path)
        with zipfile.ZipFile(path, "r") as archive:
            names = archive.namelist()
            if len(names) != len(set(names)):
                errors.append("package contains duplicate entry names")
            for name in names:
                pure = PurePosixPath(name)
                if pure.is_absolute() or ".." in pure.parts:
                    errors.append(f"unsafe archive path: {name}")
            missing_required = _REQUIRED_ENTRIES - set(names)
            if missing_required:
                raise ExportPackageError(
                    f"missing required package entries: {sorted(missing_required)}"
                )
            manifest_bytes = archive.read("manifest.json")
            checksum_line = archive.read("manifest.sha256").decode("ascii").strip()
            checksum_parts = checksum_line.split()
            manifest_sha256 = checksum_parts[0] if checksum_parts else None
            if len(checksum_parts) != 2 or checksum_parts[1] != "manifest.json":
                errors.append("manifest checksum file has an invalid format")
            actual_manifest_sha256 = hashlib.sha256(manifest_bytes).hexdigest()
            if manifest_sha256 != actual_manifest_sha256:
                errors.append("manifest checksum mismatch")
            signature_entries = _SIGNATURE_ENTRIES.intersection(names)
            if signature_entries and signature_entries != _SIGNATURE_ENTRIES:
                errors.append("evidence export has an incomplete signature bundle")
                signature_valid = False
            elif signature_entries == _SIGNATURE_ENTRIES:
                try:
                    signature = json.loads(archive.read("manifest.signature.json"))
                    if not isinstance(signature, dict):
                        raise ValueError("signature document is not an object")
                    signing_key_id = signature.get("key_id")
                    embedded_key = archive.read("manifest.public.pem")
                    trusted_key = (trusted_public_keys or {}).get(signing_key_id)
                    if trusted_public_keys is not None and trusted_key is None:
                        raise ValueError("manifest signing key is not trusted")
                    verify_signature(
                        payload=manifest_bytes,
                        signature=signature,
                        public_key_pem=trusted_key or embedded_key,
                    )
                    signature_valid = True
                except (KeyError, ValueError, json.JSONDecodeError) as exc:
                    errors.append(str(exc))
                    signature_valid = False
            try:
                manifest = json.loads(manifest_bytes)
            except json.JSONDecodeError as exc:
                raise ExportPackageError("manifest is not valid JSON") from exc
            if not isinstance(manifest, dict):
                raise ExportPackageError("manifest root is not an object")
            if manifest.get("schema") not in {
                "assurance.evidence_export.v1",
                "assurance.evidence_export.v2",
            }:
                errors.append("unsupported manifest schema")
            if manifest.get("schema") == "assurance.evidence_export.v2" and signature_valid is not True:
                errors.append("v2 evidence export requires a valid manifest signature")
            tenant_id = manifest.get("tenant_id")
            if not isinstance(tenant_id, str) or not tenant_id:
                errors.append("manifest tenant_id is missing")
                tenant_id = ""

            evidence = manifest.get("evidence", [])
            objects = manifest.get("objects", [])
            lineage = manifest.get("lineage", [])
            if not isinstance(evidence, list):
                evidence = []
                errors.append("manifest evidence is not a list")
            if not isinstance(objects, list):
                objects = []
                errors.append("manifest objects is not a list")
            if not isinstance(lineage, list):
                lineage = []
                errors.append("manifest lineage is not a list")
            evidence_count = len(evidence)
            object_count = len(objects)

            object_sizes: dict[str, int] = {}
            for item in objects:
                if not isinstance(item, dict):
                    errors.append("manifest contains a non-object object entry")
                    continue
                digest = item.get("sha256")
                size = item.get("size_bytes")
                if (
                    not isinstance(digest, str)
                    or len(digest) != 64
                    or any(character not in "0123456789abcdef" for character in digest)
                ):
                    errors.append("manifest contains an invalid object digest")
                    continue
                if digest in object_sizes:
                    errors.append(f"manifest declares object more than once: {digest}")
                    continue
                if not isinstance(size, int) or size < 0:
                    errors.append(f"manifest contains invalid object size: {digest}")
                    continue
                object_sizes[digest] = size
                name = f"objects/{digest}"
                try:
                    payload = archive.read(name)
                except KeyError:
                    errors.append(f"missing object: {digest}")
                    continue
                if hashlib.sha256(payload).hexdigest() != digest:
                    errors.append(f"object checksum mismatch: {digest}")
                if len(payload) != size:
                    errors.append(f"object size mismatch: {digest}")

            allowed_entries = _REQUIRED_ENTRIES | signature_entries | {
                f"objects/{digest}" for digest in object_sizes
            }
            unexpected_entries = set(names) - allowed_entries
            if unexpected_entries:
                errors.append(
                    f"package contains undeclared entries: {sorted(unexpected_entries)}"
                )

            evidence_ids: set[str] = set()
            for item in evidence:
                if not isinstance(item, dict):
                    errors.append("manifest contains a non-object evidence entry")
                    continue
                evidence_id = item.get("evidence_id")
                if not isinstance(evidence_id, str) or not evidence_id:
                    errors.append("manifest contains evidence without an identifier")
                    continue
                if evidence_id in evidence_ids:
                    errors.append(f"manifest declares evidence more than once: {evidence_id}")
                evidence_ids.add(evidence_id)
                digest = item.get("content_sha256")
                if digest not in object_sizes:
                    errors.append(
                        f"evidence {evidence_id} references an undeclared object"
                    )
                elif item.get("size_bytes") != object_sizes[digest]:
                    errors.append(f"evidence {evidence_id} object size does not match")
                errors.extend(
                    _verify_custody(
                        tenant_id=tenant_id,
                        evidence_id=evidence_id,
                        events=item.get("custody"),
                        declared_head=item.get("custody_head"),
                    )
                )

            requested = manifest.get("requested_evidence_ids", [])
            if not isinstance(requested, list) or not set(requested).issubset(evidence_ids):
                errors.append("requested evidence identifiers are not contained in the export")
            for edge in lineage:
                if not isinstance(edge, dict):
                    errors.append("manifest contains a non-object lineage edge")
                    continue
                if edge.get("source_evidence_id") not in evidence_ids:
                    errors.append("lineage edge references an absent source evidence record")
                if edge.get("derived_evidence_id") not in evidence_ids:
                    errors.append("lineage edge references an absent derived evidence record")
    except (OSError, zipfile.BadZipFile, ExportPackageError) as exc:
        package_sha256 = sha256_path(path) if path.is_file() else ""
        errors.append(str(exc))
    return ExportVerification(
        valid=not errors,
        package_sha256=package_sha256,
        manifest_sha256=manifest_sha256,
        evidence_count=evidence_count,
        object_count=object_count,
        signature_valid=signature_valid,
        signing_key_id=signing_key_id,
        errors=errors,
    )
