# AssuranceOS production release scope — v0.8.0

This document records the production release boundary. Per-capability detail lives in
`docs/implementation/capability-status.yaml`, the machine-readable release authority.

## Release baseline

- 43 canonical database tables.
- 7 Alembic migrations; head `0007_control_test_engine`.
- 19 released and Ed25519-signed Agent Definition Packages.
- 3 signed Audit Packs.
- 2 signed deterministic control-test packages (`SCM-01@2.0.0`, `IAM-01@1.0.0`).
- 453 automated tests at 88.54% statement coverage, above the 85% release floor.
- 19 Agent Engine deployment plans with package-digest and Memory Bank configuration proof.
- Deterministic local demonstrations covering the governed assurance lifecycle.

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

## Production extension contracts

Provider-specific extensions remain represented by versioned contracts and repository boundaries.
They plug into the same collection-grant, tenant-isolation, evidence-provenance, checkpointing,
content-inspection, and outbox controls as the released connectors. Their exact release state is
recorded in `docs/implementation/capability-status.yaml`.

## Execution-environment boundaries

The deterministic control-test sandbox enforces hard memory and CPU limits through the POSIX
`resource` interface. Platforms without it cannot enforce those limits: such a run must be
explicitly enabled with `ASSURANCEOS_CONTROL_TEST_ALLOW_DEGRADED_SANDBOX`, is recorded as
`resource_limits_enforced: false` in the run's execution environment, and is rejected outright by
the production configuration. The canonical release profile is Linux.

## External validation boundary

Terraform validation, signed deployment plans, model/runtime configuration, and Memory Bank policy
are release-gated in this repository. Deployment receipts can be loaded into Judge Mode to upgrade
the corresponding proof from release-qualified to cloud-verified without changing application code.
