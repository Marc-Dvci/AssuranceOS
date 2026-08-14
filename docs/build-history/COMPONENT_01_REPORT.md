# Component 1 report — Canonical domain database

## Outcome

The preliminary append-only SQLite event table has been replaced by a production-shaped relational
persistence layer while preserving the existing API and golden demonstration behavior.

## Main additions

- `src/assuranceos/db/base.py`: metadata naming convention and UTC clock.
- `src/assuranceos/db/session.py`: engine construction and explicit transaction boundaries.
- `src/assuranceos/db/models/`: domain-organized SQLAlchemy models.
- `src/assuranceos/db/repositories.py`: focused repositories with tenant-scoped reads.
- `migrations/`: initial Alembic migration for all canonical tables.
- `tests/test_database.py`: transaction, tenancy, planning, lifecycle, outbox, and idempotency tests.
- `tests/test_migrations.py`: fresh-database migration test.
- PostgreSQL-backed `docker-compose.yml` and migration-aware `Dockerfile`.

## Design characteristics

- The persistence layer does not contain business-process orchestration.
- Repositories do not commit; the caller owns the transaction.
- Domain writes and outbox records can commit atomically.
- No generic CRUD base class or service framework was introduced.
- JSON is limited to flexible policies, scope, metadata, and external references; core identities,
  dates, versions, states, and relationships remain relational columns.
- Existing frontend files under `apps/` were not modified.

## Validation

- 14 automated tests passed.
- Fresh Alembic upgrade passed.
- Alembic model-drift check reported no new operations.
- Python bytecode compilation passed.
- Repository validation passed for all 19 agent packages and the Audit Pack.
- Golden engagement passed with nine audit events and canonical domain records.
- FastAPI smoke tests passed for health, demo run, events, and reset.

## Environment limitations

The sandbox did not provide Docker, so the PostgreSQL Compose stack was authored but not executed.
The sandbox package index also did not provide Ruff, so lint execution was not available; syntax,
migration, repository, integration, and behavioral checks were run instead.
