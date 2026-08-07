from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MANIFEST_PATH = ROOT / "artifact-manifest.json"
EXCLUDED_PARTS = {
    ".git",
    ".pytest_cache",
    "__pycache__",
    ".ruff_cache",
    ".mypy_cache",
    ".terraform",
    "var",
}
EXCLUDED_NAMES = {".coverage"}
EXCLUDED_SUFFIXES = {".db", ".sqlite3", ".pyc"}


def included_files() -> list[Path]:
    files: list[Path] = []
    for path in ROOT.rglob("*"):
        if not path.is_file() or path == MANIFEST_PATH or path.name in EXCLUDED_NAMES:
            continue
        relative = path.relative_to(ROOT)
        if any(part in EXCLUDED_PARTS for part in relative.parts):
            continue
        if path.suffix in EXCLUDED_SUFFIXES:
            continue
        files.append(path)
    return sorted(files, key=lambda item: item.relative_to(ROOT).as_posix())


def digest(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def build_manifest() -> dict:
    entries = [
        {
            "path": path.relative_to(ROOT).as_posix(),
            "size_bytes": path.stat().st_size,
            "sha256": digest(path),
        }
        for path in included_files()
    ]
    return {
        "artifact": "assuranceos-backend",
        "version": "0.8.0",
        "component": "components-01-06-control-test-engine",
        "file_count": len(entries),
        "files": entries,
    }


def verify_manifest(manifest: dict) -> None:
    expected = {entry["path"]: entry for entry in manifest["files"]}
    current_paths = {
        path.relative_to(ROOT).as_posix(): path for path in included_files()
    }
    if set(expected) != set(current_paths):
        missing = sorted(set(expected) - set(current_paths))
        extra = sorted(set(current_paths) - set(expected))
        raise SystemExit(f"manifest file set mismatch; missing={missing}, extra={extra}")
    for relative, path in current_paths.items():
        entry = expected[relative]
        if path.stat().st_size != entry["size_bytes"] or digest(path) != entry["sha256"]:
            raise SystemExit(f"manifest mismatch: {relative}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--verify", "--check", dest="verify", action="store_true")
    args = parser.parse_args()
    if args.verify:
        verify_manifest(json.loads(MANIFEST_PATH.read_text(encoding="utf-8")))
        print("Artifact manifest verified.")
        return
    MANIFEST_PATH.write_text(
        json.dumps(build_manifest(), indent=2) + "\n", encoding="utf-8"
    )
    print(f"Wrote {MANIFEST_PATH.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
