from __future__ import annotations

import argparse
import hashlib
import json
import stat
import zipfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MANIFEST_PATH = ROOT / "artifact-manifest.json"
DEFAULT_ROOT_NAME = "assuranceos-backend-v0.8-components-01-06"


def _digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def _zip_info(name: str, mode: int) -> zipfile.ZipInfo:
    # Fixed timestamp makes repeat builds byte-for-byte reproducible.
    info = zipfile.ZipInfo(name, date_time=(2026, 8, 6, 0, 0, 0))
    info.create_system = 3
    info.external_attr = (stat.S_IFREG | mode) << 16
    info.compress_type = zipfile.ZIP_DEFLATED
    return info


def main() -> None:
    parser = argparse.ArgumentParser(description="Build a deterministic AssuranceOS release ZIP")
    parser.add_argument("output", type=Path)
    parser.add_argument("--root-name", default=DEFAULT_ROOT_NAME)
    args = parser.parse_args()

    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    entries = list(manifest["files"])
    entries.append(
        {
            "path": MANIFEST_PATH.relative_to(ROOT).as_posix(),
            "size_bytes": MANIFEST_PATH.stat().st_size,
            "sha256": _digest(MANIFEST_PATH),
        }
    )
    for entry in entries:
        path = ROOT / entry["path"]
        if not path.is_file():
            raise SystemExit(f"release file is missing: {entry['path']}")
        if path.stat().st_size != entry["size_bytes"] or _digest(path) != entry["sha256"]:
            raise SystemExit(f"release file does not match manifest: {entry['path']}")
        if b"PRIVATE KEY" in path.read_bytes() and path.suffix == ".pem":
            raise SystemExit(f"refusing to package private key material: {entry['path']}")

    args.output.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.output.with_suffix(args.output.suffix + ".tmp")
    with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for entry in sorted(entries, key=lambda item: item["path"]):
            path = ROOT / entry["path"]
            mode = path.stat().st_mode & 0o777
            archive.writestr(
                _zip_info(f"{args.root_name}/{entry['path']}", mode),
                path.read_bytes(),
            )
    temporary.replace(args.output)
    digest = _digest(args.output)
    checksum_path = args.output.with_suffix(".sha256")
    checksum_path.write_text(f"{digest}  {args.output.name}\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "archive": str(args.output),
                "sha256": digest,
                "files": len(entries),
                "built_at": datetime.now(timezone.utc).isoformat(),
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
