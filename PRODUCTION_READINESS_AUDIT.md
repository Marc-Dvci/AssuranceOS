# Components 1–6 production-readiness audit

## Decision

Version 0.8 retains the v0.7 production hardening for Components 1–5 and adds a release-shaped deterministic control-test subsystem. Component 6 is not a wrapper around a demo function: released tests are signed, immutable, schema-validated, persisted, independently reproducible, tenant-scoped, and integrated with orchestration and evidence contracts.

“Production ready” here means maintainable application code, explicit security and operational controls, repeatable migrations, signed release artifacts, bounded execution, tests, and deployable infrastructure. It does not claim external cloud/runtime proofs that cannot be performed without credentials and infrastructure.

## Component 6 readiness controls

| Area | Control implemented |
|---|---|
| Test integrity | Ed25519 signatures, canonical package manifest, package hash, code hash, semantic version, and immutable release conflict detection. |
| Registry | Exact-release SQL registry synchronized by deployment job; serving reads do not mutate registry state. |
| Input contracts | JSON Schema and typed validation for datasets, rows, parameters, and outputs. |
| Population integrity | Expected-versus-observed reconciliation, complete-population blocking, and duplicate primary-key rejection. |
| Sampling | Explicit full-population or deterministic hash policy; no model-selected opaque sample. |
| Python safety | Isolated interpreter, cleared environment, denied network socket, static import/call policy, deadlines, and resource/output bounds. |
| SQL safety | Ephemeral database, query-only mode, one read-only `SELECT`/`WITH` statement, and deterministic normalization. |
| Result taxonomy | Technical failure, missing/incomplete population, approved exception, and control exception remain distinct. |
| Evidence lineage | Dataset bindings store evidence references and stable hashes; production can require canonical evidence IDs. |
| Reproducibility | Recorded release, parameters, datasets, execution identity, and result hash can be rerun and compared. |
| Governance | Tenant, engagement, task, authentication, role permission, audit-event, and outbox boundaries are enforced. |
| Orchestration | A released control test can execute as a durable task handler without embedding test logic in the orchestrator. |

## Preserved v0.7 hardening

- JWT authentication, tenant authorization, actor binding, and production fail-closed configuration.
- Immutable task attempts and signed lease-bound execution envelopes.
- Leased transactional outbox with retry and dead-letter behavior.
- Governed schedule versioning and approval.
- Immutable local/GCS evidence objects, inspection, custody, retention, and signed exports.
- Managed credential resolution and executable connector factory.
- Nineteen signed agent packages and a signed SCM Audit Pack.
- Non-root/read-only containers, separate migration/outbox jobs, and production-shaped Google Cloud Terraform.

## Retained scope

No original capability was deleted or redefined out of scope. Components 1–6 are implemented. Finding/remediation, onboarding/company intelligence, risk portfolio, reporting cockpit, and local privacy runtime remain explicit subsequent components in the capability inventory.

## External validation still required

- Cloud Run Job isolation and resource limits under production traffic;
- PostgreSQL locking and contention with concurrent test workers;
- deployment of signing and trust keys through Secret Manager/KMS policy;
- OIDC integration and live provider OAuth installations;
- Terraform provider validation and actual Google Cloud rollout;
- Vertex AI/ADK execution and model evaluation;
- organization-specific data, retention, privacy, and residency policy validation.

## Release gate

A release is accepted only when migrations reach exact head, Alembic reports no drift, all tests pass above the 85% coverage floor, all agent/Audit Pack/control-test signatures verify, generated OpenAPI and artifact manifest are current, all six deterministic demonstrations pass, the extracted ZIP repeats verification, no private keys are packaged, and every file under `apps/` matches the v0.7 source release byte for byte.
