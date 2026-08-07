from __future__ import annotations

import argparse
import sys
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from assuranceos.adk import build_agent_engine_app  # noqa: E402

def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--agent", default="engagement-director")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    agent_dir = ROOT / "agents" / args.agent
    model = os.getenv("ASSURANCEOS_GEMINI_MODEL", "gemini-2.5-flash")
    app = build_agent_engine_app(agent_dir, model)
    if args.dry_run:
        print(f"Built ADK app for {args.agent} with model {model}: {type(app).__name__}")
        return

    import vertexai

    project = os.environ["GOOGLE_CLOUD_PROJECT"]
    location = os.getenv("GOOGLE_CLOUD_LOCATION", "us-central1")
    staging_bucket = os.environ["GOOGLE_CLOUD_STAGING_BUCKET"]
    client = vertexai.Client(project=project, location=location)
    remote = client.agent_engines.create(
        agent=app,
        config={
            "staging_bucket": staging_bucket,
            "requirements": ["google-cloud-aiplatform[agent_engines,adk]>=1.153,<2", "PyYAML>=6,<7"],
            "extra_packages": [str(agent_dir), str(ROOT / "src/assuranceos")],
            "env_vars": {"ASSURANCEOS_GEMINI_MODEL": model},
        },
    )
    print(remote.resource_name)


if __name__ == "__main__":
    main()
