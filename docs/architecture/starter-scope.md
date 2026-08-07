# Consolidated backend scope — Components 1–5

This document supersedes the original starter-scope snapshot. The complete release state is:

## Implemented

- 19 released and Ed25519-signed Agent Definition Packages.
- Signed software-change-management Audit Pack and deterministic Asteria golden engagement.
- Canonical SQLAlchemy/Alembic domain database with 39 tables.
- JWT tenant authorization and attributable actor binding.
- Durable task-DAG orchestration with leases, retries, immutable attempts, human gates, cancellation,
  recovery, event replay, and administrative remediation paths.
- Versioned schedule authoring, recurrence, audit-period calculation, preflight, deduplication,
  automatic launch, and stale-launch recovery.
- Local and GCS content-addressed evidence storage, custody, lineage, content inspection, retention,
  legal holds, signed export, and independent package verification.
- Connector SDK with collection grants, checkpoints, source lineage, Secret Manager credentials,
  GitHub, Jira, Confluence, and Google Drive adapters, deterministic fixtures, and live worker CLI.
- Leased transactional outbox dispatcher with Pub/Sub publisher and dead-letter state.
- Non-root container profile, dedicated migrations/outbox jobs, generated OpenAPI, CI release gates,
  and Google Cloud Terraform.

## Retained contracts outside Components 1–5

The original product scope is not reduced. Later workstreams remain represented by schemas,
acceptance criteria, and repository boundaries, including the deterministic control-test studio,
finding/remediation/retest service, onboarding/company intelligence, risk portfolio, reporting,
standards service, and local privacy runtime. Their exact state is recorded in
`docs/implementation/capability-status.yaml`.

## External validation boundary

Cloud deployment, live OIDC, provider OAuth tenants, PostgreSQL contention tests, and Vertex AI/ADK
execution require external projects and credentials. They are environment-validation items, not
application placeholders.
