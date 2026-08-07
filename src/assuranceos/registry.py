from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from .agent_release import verify_agent_release


REQUIRED_FILES = {
    "manifest.yaml",
    "system_prompt.md",
    "input.schema.json",
    "output.schema.json",
    "company_context.schema.json",
    "tools.yaml",
    "policy.yaml",
    "model_profiles.yaml",
    "evaluations.yaml",
    "known_limitations.md",
    "README.md",
    "release.json",
    "release.signature.json",
}


@dataclass(frozen=True)
class AgentPackage:
    agent_id: str
    path: Path
    manifest: dict[str, Any]
    tools: dict[str, Any]
    policy: dict[str, Any]
    model_profiles: dict[str, Any]
    evaluations: dict[str, Any]
    release: dict[str, Any]


class AgentRegistry:
    def __init__(
        self,
        root: Path,
        *,
        verify_releases: bool = True,
        release_public_key: Path | None = None,
    ):
        self.root = root
        self.verify_releases = verify_releases
        self.release_public_key = release_public_key or (
            root.parent / "security/release-keys/agent-release-public.pem"
        )

    def load(self) -> dict[str, AgentPackage]:
        packages: dict[str, AgentPackage] = {}
        if not self.root.exists():
            return packages
        public_key = None
        if self.verify_releases:
            if not self.release_public_key.exists():
                raise ValueError(
                    f"agent release public key is missing: {self.release_public_key}"
                )
            public_key = self.release_public_key.read_bytes()
        for directory in sorted(p for p in self.root.iterdir() if p.is_dir()):
            present = {p.name for p in directory.iterdir() if p.is_file()}
            missing = REQUIRED_FILES - present
            if missing:
                raise ValueError(f"Agent package {directory.name} missing: {sorted(missing)}")
            manifest = self._yaml(directory / "manifest.yaml")
            agent_id = str(manifest["agent_id"])
            if agent_id != directory.name:
                raise ValueError(f"Agent id/path mismatch: {agent_id} != {directory.name}")
            release = (
                verify_agent_release(directory, public_key)
                if public_key is not None
                else self._json(directory / "release.json")
            )
            if release.get("agent_id") != agent_id:
                raise ValueError(f"Agent release id mismatch: {directory.name}")
            if release.get("version") != str(manifest.get("version")):
                raise ValueError(f"Agent release version mismatch: {directory.name}")
            packages[agent_id] = AgentPackage(
                agent_id=agent_id,
                path=directory,
                manifest=manifest,
                tools=self._yaml(directory / "tools.yaml"),
                policy=self._yaml(directory / "policy.yaml"),
                model_profiles=self._yaml(directory / "model_profiles.yaml"),
                evaluations=self._yaml(directory / "evaluations.yaml"),
                release=release,
            )
        return packages

    @staticmethod
    def _yaml(path: Path) -> dict[str, Any]:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError(f"Expected object in {path}")
        return data

    @staticmethod
    def _json(path: Path) -> dict[str, Any]:
        import json

        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError(f"Expected object in {path}")
        return data
