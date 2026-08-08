"""Admission of signed evidence bundles into a private deployment.

An imported export remains sealed as one canonical evidence object. Its inner
records and custody chains are independently verified and retained byte-for-byte,
without rewriting their original tenant identities into the receiving database.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .definitions import EvidenceItem, ExportVerification
from .exceptions import ExportPackageError
from .export import verify_export_package
from .service import EvidenceVault


def import_signed_bundle(
    vault: EvidenceVault,
    *,
    package: Path,
    tenant_id: str,
    actor_id: str,
    trusted_public_keys: dict[str, bytes],
    engagement_id: str | None = None,
    classification: str = "confidential",
) -> tuple[EvidenceItem, ExportVerification]:
    """Verify a signed export completely, then admit its sealed bytes once."""

    verification = verify_export_package(
        package, trusted_public_keys=trusted_public_keys
    )
    if not verification.valid or verification.signature_valid is not True:
        detail = "; ".join(verification.errors) or "signature validation failed"
        raise ExportPackageError(f"evidence bundle admission refused: {detail}")
    item = vault.ingest_bytes(
        tenant_id=tenant_id,
        engagement_id=engagement_id,
        payload=package.read_bytes(),
        source_type="assuranceos-evidence-bundle",
        source_locator=package.resolve().as_uri(),
        actor_id=actor_id,
        actor_type="bundle-importer",
        acquisition_key=f"bundle:{verification.package_sha256}",
        original_filename=package.name,
        mime_type="application/vnd.assuranceos.evidence-export+zip",
        classification=classification,
        accepted=True,
        metadata={
            "sealed_bundle": True,
            "package_sha256": verification.package_sha256,
            "manifest_sha256": verification.manifest_sha256,
            "signing_key_id": verification.signing_key_id,
            "evidence_count": verification.evidence_count,
            "object_count": verification.object_count,
        },
    )
    return item, verification


def bundle_admission_result(
    item: EvidenceItem, verification: ExportVerification
) -> dict[str, Any]:
    return {
        "admitted": True,
        "evidence_id": item.evidence_id,
        "package_sha256": verification.package_sha256,
        "manifest_sha256": verification.manifest_sha256,
        "signing_key_id": verification.signing_key_id,
        "evidence_count": verification.evidence_count,
        "object_count": verification.object_count,
    }
