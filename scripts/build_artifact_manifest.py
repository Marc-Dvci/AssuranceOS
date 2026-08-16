from __future__ import annotations

import argparse
import functools
import hashlib
import json
# git is invoked below with a fixed argument list and no shell.
import subprocess  # nosec B404
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MANIFEST_PATH = ROOT / "artifact-manifest.json"
# The manifest is a release gate, so it must describe the source tree and nothing
# else. Without these exclusions it hashes whatever happens to sit in the working
# directory -- a local virtualenv alone adds thousands of entries -- and the
# --check gate then fails on any machine whose environment differs.
EXCLUDED_PARTS = {
    ".git",
    ".pytest_cache",
    "__pycache__",
    ".ruff_cache",
    ".mypy_cache",
    ".terraform",
    "var",
    ".venv",
    "venv",
    "node_modules",
    "htmlcov",
    "build",
    "dist",
    ".idea",
    ".vscode",
}
EXCLUDED_NAMES = {".coverage", ".DS_Store"}
EXCLUDED_SUFFIXES = {".db", ".sqlite3", ".pyc", ".pyo"}


@functools.lru_cache(maxsize=1)
def _git_ignored() -> frozenset[str]:
    """Every path in the tree that git is already ignoring.

    A hardcoded exclusion list only excludes what someone thought of. Following
    this repository's *own* cloud runbook writes `terraform.tfstate` next to the
    module — gitignored, holding the generated database password, and hashed
    straight into the release manifest, which then fails the gate that asserts
    the manifest equals `git ls-files`. The exclusions above stay as a fallback
    for a checkout without git; git's own answer is the authority when it is
    available.

    This deliberately does not switch to listing `git ls-files` instead: the
    manifest is built by walking the tree so that an untracked file which is
    *not* ignored still breaks the gate. That is the check working, not failing.
    """
    try:
        # Fixed argument list, no shell, no interpolation.
        result = subprocess.run(  # nosec B603 B607
            ["git", "ls-files", "--others", "--ignored", "--exclude-standard"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=True,
            timeout=60,
        )
    except (OSError, subprocess.SubprocessError):
        return frozenset()
    return frozenset(line.strip() for line in result.stdout.splitlines() if line.strip())


def _is_local_environment_file(name: str) -> bool:
    """A populated .env is developer-local and may hold secrets.

    It is gitignored, so it exists on a developer machine and not in CI. Hashing it
    into a release manifest both breaks the --check gate across environments and
    puts credential material into a published artifact. .env.example is committed
    and stays in the manifest.
    """
    return name == ".env" or (name.startswith(".env.") and name != ".env.example")


def _is_build_artifact(relative: Path) -> bool:
    return any(
        part.endswith((".egg-info", ".egg-link")) or part.startswith(".coverage.")
        for part in relative.parts
    )


def included_files() -> list[Path]:
    files: list[Path] = []
    for path in ROOT.rglob("*"):
        if not path.is_file() or path == MANIFEST_PATH or path.name in EXCLUDED_NAMES:
            continue
        if _is_local_environment_file(path.name):
            continue
        relative = path.relative_to(ROOT)
        if any(part in EXCLUDED_PARTS for part in relative.parts):
            continue
        if relative.as_posix() in _git_ignored():
            continue
        if _is_build_artifact(relative):
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
        json.dumps(build_manifest(), indent=2) + "\n", encoding="utf-8", newline="\n"
    )
    print(f"Wrote {MANIFEST_PATH.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
