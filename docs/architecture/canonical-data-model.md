# Canonical data model

Component 1 replaces the preliminary single-table event ledger with a normalized system of
record. The implementation uses SQLAlchemy 2.0 and Alembic so the same domain model runs on
SQLite during tests and PostgreSQL locally or on Cloud SQL.

## Design decisions

- **Explicit transaction ownership.** `Database.transaction()` owns commit and rollback.
  Repositories receive an existing session and never commit independently.
- **No generic repository base class.** Domain repositories expose only operations that are
  meaningful for their aggregate. This keeps the persistence API small and discoverable.
- **Tenant scope is explicit.** Read and transition methods require `tenant_id`; callers cannot
  accidentally perform unscoped object lookups through the repository API.
- **Versioned governance records.** Organization profiles, plans, templates, schedules,
  findings, management responses, agent releases, and scope references preserve versions.
- **Immutable history by addition.** Audit events and approval decisions are append-only through
  their repositories. Past schedule occurrences and findings are not overwritten by new versions.
- **Transactional outbox.** Domain changes and integration events can be written in one database
  transaction. Publication occurs separately and is idempotent.
- **Portable JSON.** Flexible policy, scope, and evidence attributes use SQLAlchemy JSON while
  identifiers, statuses, dates, hashes, and relationships remain first-class columns.

## Domain groups

| Group | Principal tables |
|---|---|
| Tenancy and authorization | `tenants`, `users`, `role_assignments` |
| Company context | `organization_profiles`, `organization_facts` |
| Audit universe | `audit_universe_entities`, `entity_relationships`, `risks`, `controls`, `risk_control_links` |
| Planning and scheduling | `audit_plans`, `engagement_templates`, `audit_schedules`, `schedule_occurrences` |
| Engagement execution | `engagements`, `engagement_tasks`, `task_dependencies` |
| Evidence and claims | `evidence_records`, `evidence_transformations`, `claims`, `claim_evidence_links` |
| Findings and follow-up | `findings`, `approval_decisions`, `management_responses`, `remediation_actions`, `retests` |
| Agent governance | `agent_releases`, `execution_traces` |
| Reliability and traceability | `audit_events`, `outbox_events`, `idempotency_records` |

## Migrations

```bash
# SQLite, using ASSURANCEOS_DATABASE_PATH fallback
python scripts/migrate.py

# PostgreSQL / Cloud SQL
export ASSURANCEOS_DATABASE_URL='postgresql+psycopg://user:password@host/db'
python scripts/migrate.py
```

Application containers run migrations before starting the API. Production deployment should run
that command as a dedicated release job rather than granting DDL permissions to the serving
identity.

## Repository usage

```python
with database.transaction() as session:
    engagements = EngagementRepository(session)
    outbox = OutboxRepository(session)

    engagements.add(engagement)
    outbox.add(
        tenant_id=engagement.tenant_id,
        aggregate_type="engagement",
        aggregate_id=engagement.engagement_id,
        event_type="engagement.created",
        payload={"engagement_id": engagement.engagement_id},
        idempotency_key=f"engagement.created:{engagement.engagement_id}",
    )
```

If either write fails, both are rolled back.

## Subsequent components

Component 2 now implements the durable engagement-task state machine on these records. Schedule
occurrence calculation, evidence-vault behavior, finding adjudication, remediation, and retest
state machines remain separate components so the persistence layer does not absorb service logic.
