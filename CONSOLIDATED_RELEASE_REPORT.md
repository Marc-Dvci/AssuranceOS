# AssuranceOS v0.8 consolidated release report

## Scope

This release consolidates Components 1–5 from v0.7 and adds Component 6 without modifying the frontend:

1. canonical domain database;
2. durable engagement orchestrator;
3. recurring audit scheduler and automatic launcher;
4. content-addressed evidence vault and provenance layer;
5. connector SDK and GitHub, Jira, Confluence, and Google Drive adapters;
6. deterministic control-test engine and signed versioned test registry.

The original implementation plan remains authoritative. Later components remain represented by explicit contracts and are not falsely claimed as complete.

## Component 6 delivered

- Signed semantic-versioned control-test packages with immutable release hashes.
- Database-backed release registry and canonical run, dataset-binding, and exception history.
- Fail-closed JSON Schema validation for package manifests, parameters, input rows, and outputs.
- Required-dataset, evidence-binding, population-count, and duplicate-primary-key controls.
- Full-population and deterministic hash sampling.
- Bounded, network-denied Python subprocess execution with static import/call policy.
- Read-only SQL execution over ephemeral normalized SQLite datasets.
- Typed test outcomes and exception taxonomy; technical failures remain distinct from control failures.
- Input, execution, code, package, and result SHA-256 identities.
- Run idempotency, tenant/engagement/task boundaries, audit events, and transactional outbox emission.
- Exact-release reproducibility verification using the recorded immutable inputs.
- Durable-orchestrator handler and authenticated tenant-scoped HTTP contracts.
- Asteria demonstration covering both SCM and IAM procedures.

## Released procedures

- `SCM-01@2.0.0` — approved change before merge, Python, complete population.
- `IAM-01@1.0.0` — timely terminated-user deprovisioning, SQL, complete population.

Both packages include manifests, code/query, schemas, golden cases, file manifests, release metadata, and Ed25519 signatures. Test code cannot be changed without producing a different release identity.

## Release verification

- 125 automated tests passed.
- Statement coverage: 87.11%.
- Canonical schema: 43 tables and 7 Alembic migrations.
- Fresh and populated upgrade reached `0007_control_test_engine`.
- Alembic reported no model/migration drift.
- 19 agent release signatures, the Audit Pack signature, and 2 control-test signatures verified.
- Generated OpenAPI contains 54 paths.
- Golden, orchestration, scheduling, evidence, connector, and control-test demonstrations passed.
- Python compilation and repository validation passed.
- The source artifact manifest and extracted ZIP are independently verified before release.
- Every file under `apps/` is compared byte-for-byte with the v0.7 source release.

## External validation boundary

The implementation includes production-shaped execution and deployment contracts, but this sandbox cannot prove Docker runtime hardening, Cloud Run Job isolation, Terraform provider validation, real Google Cloud deployment, PostgreSQL locking under production concurrency, OIDC issuer integration, live provider OAuth/app installations, or Vertex AI Agent Engine execution. Those remain deployment-environment proofs, not hidden placeholders or reduced scope.
