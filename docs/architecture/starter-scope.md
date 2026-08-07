# Consolidated backend scope — Components 1–6 (v0.8.0)

This document records the release state of the consolidated v0.8.0 backend. It supersedes every
earlier starter-scope snapshot. Per-capability detail lives in
`docs/implementation/capability-status.yaml`, which remains the machine-readable authority.

## Release baseline

- 43 canonical database tables.
- 7 Alembic migrations; head `0007_control_test_engine`.
- 19 released and Ed25519-signed Agent Definition Packages.
- 1 signed software-change-management Audit Pack.
- 2 signed deterministic control-test packages (`SCM-01@2.0.0`, `IAM-01@1.0.0`).
- 54 generated OpenAPI paths.
- 125 automated tests at 87.11% statement coverage, above the 85% release floor.
- 6 deterministic local demonstrations.

These values are regression floors. A release must not silently lower the coverage threshold,
remove tests, bypass signature verification, or alter an applied migration.

## Implemented

- Canonical SQLAlchemy/Alembic domain database with explicit transaction boundaries and
  domain-specific repositories.
- JWT tenant authorization and attributable actor binding.
- Durable task-DAG orchestration with leases, retries, immutable attempts, human gates,
  cancellation, recovery, event replay, and administrative remediation paths.
- Versioned schedule authoring, IANA recurrence, audit-period calculation, preflight,
  deduplication, automatic launch, and stale-launch recovery.
- Local and GCS content-addressed evidence storage, custody, lineage, content inspection,
  retention, legal holds, signed export, and independent package verification.
- Connector SDK with collection grants, checkpoints, source lineage, Secret Manager credentials,
  GitHub, Jira, Confluence, and Google Drive adapters, deterministic fixtures, and a live worker CLI.
- Deterministic control-test engine: immutable signed packages, release registry and run history,
  population reconciliation, deterministic-hash sampling, bounded network-denied execution,
  run idempotency, and reproducibility verification.
- Leased transactional outbox dispatcher with Pub/Sub publisher and dead-letter state.
- Non-root container profile, dedicated migrations/outbox jobs, generated OpenAPI, CI release
  gates, and Google Cloud Terraform.

## Retained contracts outside Components 1–6

The original product scope is not reduced. Later workstreams remain represented by schemas,
acceptance criteria, and repository boundaries, including the finding/remediation/retest service,
the Audit Pack compiler and standards layer, onboarding and company intelligence, the risk
portfolio, evidence-grounded reporting, the governed agent runtime, and the local privacy runtime.
Their exact state is recorded in `docs/implementation/capability-status.yaml`.

A directory marked `contract_defined` preserves an intended boundary. It is not a working service,
and a table's presence does not mean the corresponding business workflow is implemented.

## Execution-environment boundaries

The deterministic control-test sandbox enforces hard memory and CPU limits through the POSIX
`resource` interface. Platforms without it cannot enforce those limits: such a run must be
explicitly enabled with `ASSURANCEOS_CONTROL_TEST_ALLOW_DEGRADED_SANDBOX`, is recorded as
`resource_limits_enforced: false` in the run's execution environment, and is rejected outright by
the production configuration. The canonical release profile is Linux.

## External validation boundary

Cloud deployment, live OIDC, provider OAuth tenants, PostgreSQL contention tests, and Vertex
AI/ADK execution require external projects and credentials. They are environment-validation items,
not application placeholders, and are tracked in the developer handoff.
