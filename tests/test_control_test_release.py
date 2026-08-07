from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from assuranceos.control_testing.release import (
    build_release_document,
    sign_release_document,
    verify_control_test_release,
)


def test_control_test_release_round_trip_and_signature_tampering(tmp_path: Path):
    package = tmp_path / "package"
    package.mkdir()
    (package / "manifest.yaml").write_text("test_id: DEMO\nversion: 1.0.0\n")
    (package / "test.sql").write_text("SELECT 1")
    key = Ed25519PrivateKey.generate()
    document = build_release_document(
        package_dir=package,
        test_id="DEMO",
        version="1.0.0",
        released_at=datetime.now(timezone.utc),
    )
    signature = sign_release_document(document, private_key=key, key_id="test-key")
    (package / "release.json").write_text(json.dumps(document))
    (package / "release.signature.json").write_text(json.dumps(signature))
    public_pem = key.public_key().public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    verified = verify_control_test_release(package, public_pem)
    assert verified["package_sha256"] == document["package_sha256"]

    signature["signature_base64"] = "AAAA"
    (package / "release.signature.json").write_text(json.dumps(signature))
    with pytest.raises(ValueError, match="signature is invalid"):
        verify_control_test_release(package, public_pem)
