# Component 6 report — Deterministic control-test engine

## Delivered

- Ed25519-signed, semantic-versioned control-test packages and trust key.
- Canonical release, run, dataset-binding, and exception tables.
- Alembic migration `0007_control_test_engine`.
- Exact-release registry synchronization with immutable hash conflict detection.
- Typed dataset, parameter, and output validation through JSON Schema and Pydantic.
- Complete-population reconciliation, duplicate-key detection, and fail-closed blocking.
- Full-population and deterministic hash sampling policies.
- Bounded, network-denied Python subprocess runtime with static import policy.
- Read-only SQL execution over ephemeral normalized datasets.
- Run idempotency, tenant scoping, engagement/task validation, and optional canonical evidence enforcement.
- Input, execution, and result manifests with SHA-256 identities.
- Canonical exception records, audit events, and transactional outbox delivery.
- Reproducibility verification against the exact recorded release and inputs.
- Durable-orchestrator task-handler adapter.
- Tenant-authorized REST contracts and generated OpenAPI definitions.
- Executable Asteria demonstration for SCM and IAM.

## Released procedures

### SCM-01@2.0.0

Tests the in-period pull-request population for the required approval count and an approved change
ticket. Active governed exceptions are excluded without hiding them from the row-level result.

### IAM-01@1.0.0

Tests terminated workforce identities against directory status and the approved disabling deadline.
Missing, active, and late-disabled accounts are classified separately; active approved exceptions are
retained as non-findings.

## Deliberate boundaries

- The local Python process provides practical isolation but is not claimed as a hostile multi-tenant
  kernel boundary. Production execution should use a network-denied Cloud Run Job.
- Dataset records are not duplicated into the canonical database. Their hashes and evidence bindings
  are stored; reproduction requires the same immutable inputs to be supplied again.
- Model agents may select a released procedure and interpret accepted results, but cannot alter test
  code, population policy, release identity, or deterministic output.

## Verification

- 125 automated tests passed.
- Statement coverage reached 87.11%, above the 85% release floor.
- Fresh and populated-database migration reached `0007_control_test_engine` with no Alembic drift.
- Both signed procedure packages verified and synchronized into the canonical registry.
- Python and SQL runtime demonstrations passed.
- HTTP authorization, tenant isolation, idempotency, failure persistence, sampling, strict evidence mode, orchestrator adaptation, package tamper rejection, and reproducibility are covered by executable tests.
- Generated OpenAPI is current and the complete repository compiles.
