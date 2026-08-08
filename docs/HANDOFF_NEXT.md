# AssuranceOS release operations

## Canonical verification

Run these commands before publishing:

    uv lock --check
    ruff check src scripts tests migrations
    python -m compileall -q src scripts tests agents
    python scripts/validate_repo.py
    python scripts/run_agent_evaluations.py --mode contract
    pytest -q --cov=assuranceos --cov-fail-under=85
    python scripts/generate_openapi.py --check
    python scripts/build_artifact_manifest.py --check
    python scripts/deploy_adk_agent.py --plan

## Managed deployment

Set the Google project, location, staging bucket, and Gemini model variables,
then run scripts/deploy_adk_agent.py. The command qualifies all selected
releases before mutation and writes the managed-fleet proof to
var/agent-engine-deployment-result.json.

Supply that result to the API through ASSURANCEOS_AGENT_ENGINE_PROOF or
ASSURANCEOS_AGENT_ENGINE_RESOURCE_MAP_JSON. Judge Mode verifies it against the
signed registry at request time.

## Release artifacts

- security/release-keys contains public trust roots only.
- var/release-keys contains local private release keys and remains gitignored.
- api/openapi/openapi.yaml is generated from the typed FastAPI application.
- artifact-manifest.json is regenerated after the final tracked-file set.
- uv.lock is the reproducible Python dependency graph.
