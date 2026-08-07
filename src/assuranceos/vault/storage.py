from __future__ import annotations

import hashlib
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import BinaryIO, Protocol
from uuid import uuid4

from .exceptions import (
    ImmutableObjectConflictError,
    ObjectIntegrityError,
    ObjectNotFoundError,
)

_SEGMENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def sha256_file(path: Path, *, chunk_size: int = 1024 * 1024) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as handle:
        while chunk := handle.read(chunk_size):
            digest.update(chunk)
            size += len(chunk)
    return digest.hexdigest(), size


@dataclass(frozen=True)
class StoredObject:
    provider: str
    key: str
    uri: str
    sha256: str
    size_bytes: int
    created: bool
    modified_at: datetime


class ObjectStore(Protocol):
    provider_name: str

    def put_bytes(
        self, tenant_id: str, payload: bytes, *, expected_sha256: str
    ) -> StoredObject: ...

    def open(self, tenant_id: str, key: str) -> BinaryIO: ...

    def stat(self, tenant_id: str, key: str) -> StoredObject: ...

    def verify(
        self,
        tenant_id: str,
        key: str,
        *,
        expected_sha256: str,
        expected_size: int,
    ) -> StoredObject: ...

    def delete(self, tenant_id: str, key: str) -> bool: ...

    def iter_objects(self, tenant_id: str) -> list[StoredObject]: ...


class LocalObjectStore:
    """Tenant-scoped immutable content-addressed storage for local and Docker execution."""

    provider_name = "local"

    def __init__(self, root: Path):
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / ".tmp").mkdir(exist_ok=True)

    @staticmethod
    def key_for_digest(digest: str) -> str:
        if len(digest) != 64 or any(character not in "0123456789abcdef" for character in digest):
            raise ValueError("sha256 digest must contain 64 lowercase hexadecimal characters")
        return f"objects/{digest[:2]}/{digest[2:4]}/{digest}"

    def _tenant_root(self, tenant_id: str) -> Path:
        if not _SEGMENT.fullmatch(tenant_id):
            raise ValueError("tenant_id is not a safe storage segment")
        path = (self.root / tenant_id).resolve()
        if self.root not in path.parents:
            raise ValueError("tenant storage path escapes vault root")
        return path

    def _path(self, tenant_id: str, key: str) -> Path:
        if key.startswith("/") or ".." in Path(key).parts:
            raise ValueError("storage key is not safe")
        tenant_root = self._tenant_root(tenant_id)
        path = (tenant_root / key).resolve()
        if tenant_root not in path.parents:
            raise ValueError("storage key escapes tenant root")
        return path

    def put_bytes(self, tenant_id: str, payload: bytes, *, expected_sha256: str) -> StoredObject:
        actual_sha256 = sha256_bytes(payload)
        if actual_sha256 != expected_sha256:
            raise ObjectIntegrityError(
                f"payload digest {actual_sha256} does not match expected {expected_sha256}"
            )
        key = self.key_for_digest(expected_sha256)
        target = self._path(tenant_id, key)
        target.parent.mkdir(parents=True, exist_ok=True)

        if target.exists():
            existing = self.verify(
                tenant_id,
                key,
                expected_sha256=expected_sha256,
                expected_size=len(payload),
            )
            return StoredObject(**{**existing.__dict__, "created": False})

        temporary = self.root / ".tmp" / f"{uuid4().hex}.tmp"
        try:
            with temporary.open("xb") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            try:
                os.link(temporary, target)
                created = True
            except FileExistsError:
                created = False
                self.verify(
                    tenant_id,
                    key,
                    expected_sha256=expected_sha256,
                    expected_size=len(payload),
                )
        finally:
            # Drop the temporary name before sealing the target. os.link leaves both
            # names pointing at one inode, so sealing first would either make the
            # temporary undeletable (Windows refuses to unlink a read-only entry) or
            # force a chmod that would unseal the target through the shared inode.
            temporary.unlink(missing_ok=True)
        if created:
            target.chmod(0o444)

        stat = target.stat()
        return StoredObject(
            provider=self.provider_name,
            key=key,
            uri=f"vault+file://{tenant_id}/{key}",
            sha256=expected_sha256,
            size_bytes=len(payload),
            created=created,
            modified_at=datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc),
        )

    def open(self, tenant_id: str, key: str) -> BinaryIO:
        path = self._path(tenant_id, key)
        if not path.is_file():
            raise ObjectNotFoundError(f"stored object not found: {tenant_id}/{key}")
        return path.open("rb")

    def stat(self, tenant_id: str, key: str) -> StoredObject:
        path = self._path(tenant_id, key)
        if not path.is_file():
            raise ObjectNotFoundError(f"stored object not found: {tenant_id}/{key}")
        digest, size = sha256_file(path)
        stat = path.stat()
        return StoredObject(
            provider=self.provider_name,
            key=key,
            uri=f"vault+file://{tenant_id}/{key}",
            sha256=digest,
            size_bytes=size,
            created=False,
            modified_at=datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc),
        )

    def verify(
        self,
        tenant_id: str,
        key: str,
        *,
        expected_sha256: str,
        expected_size: int,
    ) -> StoredObject:
        stored = self.stat(tenant_id, key)
        if stored.sha256 != expected_sha256 or stored.size_bytes != expected_size:
            raise ImmutableObjectConflictError(
                "stored object no longer matches its immutable digest and size"
            )
        return stored

    def delete(self, tenant_id: str, key: str) -> bool:
        path = self._path(tenant_id, key)
        if not path.exists():
            return False
        path.chmod(0o600)
        path.unlink()
        for parent in (path.parent, path.parent.parent):
            try:
                parent.rmdir()
            except OSError:
                break
        return True

    def iter_objects(self, tenant_id: str) -> list[StoredObject]:
        tenant_root = self._tenant_root(tenant_id)
        objects_root = tenant_root / "objects"
        if not objects_root.exists():
            return []
        objects: list[StoredObject] = []
        for path in sorted(objects_root.rglob("*")):
            if not path.is_file():
                continue
            key = path.relative_to(tenant_root).as_posix()
            objects.append(self.stat(tenant_id, key))
        return objects
