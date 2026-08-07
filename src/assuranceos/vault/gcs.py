from __future__ import annotations

import hashlib
from io import BytesIO
from datetime import timezone

from .exceptions import ImmutableObjectConflictError, ObjectNotFoundError
from .storage import LocalObjectStore, StoredObject


class GoogleCloudStorageObjectStore:
    """Immutable, tenant-prefixed Cloud Storage adapter.

    Uploads use ``if_generation_match=0`` so an existing content-addressed object cannot be
    overwritten. SHA-256 is persisted as object metadata and recomputed during verification.
    """

    provider_name = "gcs"

    def __init__(self, bucket_name: str, *, client: object | None = None, prefix: str = "evidence"):
        if client is None:
            try:
                from google.cloud import storage
            except ImportError as exc:  # pragma: no cover - optional cloud dependency
                raise RuntimeError("install the cloud extra to use Cloud Storage") from exc
            client = storage.Client()
        self._client = client
        self.bucket = self._client.bucket(bucket_name)
        self.prefix = prefix.strip("/")

    def _name(self, tenant_id: str, key: str) -> str:
        if not tenant_id or any(ch not in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_.-" for ch in tenant_id):
            raise ValueError("tenant_id is not a safe storage segment")
        if key.startswith("/") or ".." in key.split("/"):
            raise ValueError("storage key is not safe")
        return f"{self.prefix}/{tenant_id}/{key}"

    @staticmethod
    def _key(expected_sha256: str) -> str:
        return LocalObjectStore.key_for_digest(expected_sha256)

    def put_bytes(self, tenant_id: str, payload: bytes, *, expected_sha256: str) -> StoredObject:
        actual = hashlib.sha256(payload).hexdigest()
        if actual != expected_sha256:
            raise ImmutableObjectConflictError("payload digest does not match expected SHA-256")
        key = self._key(expected_sha256)
        blob = self.bucket.blob(self._name(tenant_id, key))
        blob.metadata = {"sha256": expected_sha256, "tenant_id": tenant_id}
        created = True
        try:
            blob.upload_from_file(
                BytesIO(payload),
                size=len(payload),
                content_type="application/octet-stream",
                if_generation_match=0,
                checksum="auto",
                timeout=60,
            )
        except Exception as exc:
            # A failed create precondition means the immutable object already exists. Verify it
            # before treating the operation as deduplicated success.
            if exc.__class__.__name__ not in {"PreconditionFailed", "Conflict"}:
                raise
            created = False
            return self.verify(
                tenant_id, key, expected_sha256=expected_sha256, expected_size=len(payload)
            )
        blob.reload()
        modified = blob.updated or blob.time_created
        assert modified is not None
        return StoredObject(
            provider=self.provider_name,
            key=key,
            uri=f"gs://{self.bucket.name}/{blob.name}",
            sha256=expected_sha256,
            size_bytes=len(payload),
            created=created,
            modified_at=modified.astimezone(timezone.utc),
        )

    def open(self, tenant_id: str, key: str) -> BytesIO:
        blob = self.bucket.blob(self._name(tenant_id, key))
        try:
            payload = blob.download_as_bytes(checksum="auto", timeout=60)
        except Exception as exc:
            if exc.__class__.__name__ == "NotFound":
                raise ObjectNotFoundError(f"stored object not found: {tenant_id}/{key}") from exc
            raise
        return BytesIO(payload)

    def stat(self, tenant_id: str, key: str) -> StoredObject:
        blob = self.bucket.blob(self._name(tenant_id, key))
        try:
            blob.reload()
        except Exception as exc:
            if exc.__class__.__name__ == "NotFound":
                raise ObjectNotFoundError(f"stored object not found: {tenant_id}/{key}") from exc
            raise
        metadata = blob.metadata or {}
        digest = metadata.get("sha256", "")
        modified = blob.updated or blob.time_created
        assert modified is not None
        return StoredObject(
            provider=self.provider_name,
            key=key,
            uri=f"gs://{self.bucket.name}/{blob.name}",
            sha256=digest,
            size_bytes=int(blob.size or 0),
            created=False,
            modified_at=modified.astimezone(timezone.utc),
        )

    def verify(
        self, tenant_id: str, key: str, *, expected_sha256: str, expected_size: int
    ) -> StoredObject:
        with self.open(tenant_id, key) as handle:
            payload = handle.read()
        actual = hashlib.sha256(payload).hexdigest()
        if actual != expected_sha256 or len(payload) != expected_size:
            raise ImmutableObjectConflictError(
                "Cloud Storage object no longer matches its immutable digest and size"
            )
        stored = self.stat(tenant_id, key)
        return StoredObject(**{**stored.__dict__, "sha256": actual})

    def delete(self, tenant_id: str, key: str) -> bool:
        blob = self.bucket.blob(self._name(tenant_id, key))
        try:
            blob.reload()
            blob.delete(if_generation_match=blob.generation, timeout=60)
            return True
        except Exception as exc:
            if exc.__class__.__name__ == "NotFound":
                return False
            raise

    def iter_objects(self, tenant_id: str) -> list[StoredObject]:
        prefix = f"{self.prefix}/{tenant_id}/objects/"
        objects: list[StoredObject] = []
        for blob in self._client.list_blobs(self.bucket, prefix=prefix):
            key = blob.name.removeprefix(f"{self.prefix}/{tenant_id}/")
            metadata = blob.metadata or {}
            modified = blob.updated or blob.time_created
            if modified is None:
                continue
            objects.append(
                StoredObject(
                    provider=self.provider_name,
                    key=key,
                    uri=f"gs://{self.bucket.name}/{blob.name}",
                    sha256=metadata.get("sha256", ""),
                    size_bytes=int(blob.size or 0),
                    created=False,
                    modified_at=modified.astimezone(timezone.utc),
                )
            )
        return objects
