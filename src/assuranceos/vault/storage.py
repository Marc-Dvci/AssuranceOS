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
#: The only shape a stored object key takes, produced by `key_for_digest` and by
#: nothing else. Pinning it here means the path built from a key cannot leave the
#: object tree even before the containment check below runs.
_OBJECT_KEY = re.compile(r"^objects/[0-9a-f]{2}/[0-9a-f]{2}/(?P<digest>[0-9a-f]{64})$")


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
        match = _SEGMENT.fullmatch(tenant_id)
        if match is None:
            raise ValueError("tenant_id is not a safe storage segment")
        # Built from the matched text rather than from the argument. The two are
        # equal strings; the difference is that the path is now assembled from
        # something a pattern produced, which is what makes the validation
        # visible to a reader and to static analysis instead of being a check
        # standing next to an unrelated join.
        path = (self.root / match.group(0)).resolve()
        if self.root not in path.parents:
            raise ValueError("tenant storage path escapes vault root")
        return path

    def _path(self, tenant_id: str, key: str) -> Path:
        # Both components are matched against a fixed shape before either is
        # joined to a path, rather than only screened for the traversal spellings
        # somebody thought of. Every key this store has ever held comes from
        # `key_for_digest`, so accepting any string without `..` was a degree of
        # freedom nothing needed -- and a rule expressed as "not the bad ones" is
        # the kind that a new encoding walks through.
        match = _OBJECT_KEY.fullmatch(key)
        if match is None:
            raise ValueError("storage key is not a content-addressed object key")
        tenant_root = self._tenant_root(tenant_id)
        # Reassembled from the digest the pattern captured, so the path is a
        # function of sixty-four validated hex characters and nothing else.
        digest = match.group("digest")
        path = (tenant_root / "objects" / digest[:2] / digest[2:4] / digest).resolve()
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
