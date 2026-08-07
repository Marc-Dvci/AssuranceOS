# Build report

- Artifact: `assuranceos-backend-v0.8-components-01-06`
- Version: `0.8.0`
- Scope: consolidated and hardened Components 1–6
- Canonical domain tables: 43
- Alembic migrations: 7
- Agent packages: 19 signed releases
- Audit Packs: 1 signed executable release
- Control-test packages: 2 signed executable releases
- OpenAPI paths: 54
- Frontend/UI changes: none

## Consolidated components

1. Canonical domain database and transactional repositories.
2. Durable engagement DAG orchestrator with immutable task attempts and human gates.
3. Versioned recurring scheduler with preflight and automatic launch.
4. Content-addressed evidence vault with custody, lineage, retention, GCS, inspection, and signed exports.
5. Connector SDK with grants, checkpoints, recovery, Secret Manager credentials, and four executable provider adapters.
6. Deterministic control-test engine and signed versioned registry with Python/SQL execution, population reconciliation, sampling, exception records, and reproducibility manifests.

## Component 6 release contents

- `SCM-01@2.0.0`: full-population approved-change-before-merge test executed through the bounded Python runtime.
- `IAM-01@1.0.0`: full-population terminated-user-deprovisioning test executed through the read-only SQL runtime.
- immutable release, run, dataset-binding, and exception records;
- Ed25519 package signatures and exact package/code hashes;
- JSON Schema and Pydantic validation for datasets, parameters, and results;
- population completeness and duplicate-key blocking;
- full-population and deterministic hash sampling policies;
- evidence bindings, execution/input/result hashes, audit events, and outbox records;
- authenticated tenant-scoped APIs and durable-orchestrator integration;
- exact-input reproducibility verification.

## Validation

- 125 automated tests passed.
- Statement coverage: 87.11% (release floor: 85%).
- Fresh and populated-database Alembic upgrade reached `0007_control_test_engine`.
- Alembic reported no model/migration drift.
- Python compilation passed.
- 19 agent signatures, the Audit Pack signature, and 2 control-test signatures verified.
- Generated OpenAPI consistency passed with 54 paths.
- Golden, orchestration, scheduling, evidence, connector, and control-test demonstrations passed.
- Source artifact manifest and extracted-ZIP verification are release gates.
- Every file under `apps/` is compared byte-for-byte with the v0.7 source release.

## Environment-bound validation

The sandbox does not provide Docker, Terraform, Google Cloud credentials, OIDC configuration, or live provider tenants. Cloud Run Job isolation, PostgreSQL contention, Terraform provider execution, live OAuth, and production evidence volumes require deployment-environment validation. The corresponding code, contracts, and infrastructure remain included and are not substituted with placeholders.
