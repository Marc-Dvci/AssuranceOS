# AssuranceOS — Backend Components 1–6

AssuranceOS is a governed, AI-native internal-audit platform. Version 0.8 adds the signed deterministic control-test engine and versioned test registry to the hardened canonical database, durable orchestrator, recurring scheduler, evidence vault, and connector SDK. No frontend or user-interface files were changed.

## Implemented

### Agent and audit foundation

- 19 complete, signed Agent Definition Packages;
- manifest-driven agent registry and default-deny policy gateway;
- typed execution envelopes and structured results;
- Asteria Systems DemoCo synthetic golden engagement;
- governed deterministic control-test selection and execution contracts;
- FastAPI control plane and existing Judge Mode route;
- optional Google ADK and Vertex AI Agent Engine adapters.

### Component 1 — Canonical domain database

- SQLAlchemy 2.0 models organized by domain;
- 31 normalized tables for tenancy, company context, audit universe, planning, schedules,
  engagements, tasks, evidence, claims, findings, approvals, remediation, retests, agent releases,
  traces, audit events, outbox delivery, and idempotency;
- Alembic migrations with SQLite and PostgreSQL compatibility;
- explicit transaction boundaries and domain-specific repositories;
- tenant-scoped repository reads and state transitions;
- transactional outbox support;
- canonical persistence for the existing golden demo.

See [`docs/architecture/canonical-data-model.md`](docs/architecture/canonical-data-model.md).

### Component 2 — Durable engagement orchestrator

- typed, versioned workflow definitions;
- dependency graph validation and cycle rejection;
- canonical task and dependency compilation;
- dependency-driven activation;
- exclusive worker claims and bounded leases;
- heartbeats, lease recovery, deadlines, cancellation, and task blocking;
- retry policies with bounded exponential backoff and explicit failure classes;
- pre-execution and post-execution human gates;
- atomic state, audit-event, and outbox transitions;
- per-stream event ordering and replay-to-canonical verification;
- queue-neutral local worker adapter;
- complete five-task Asteria SCM orchestration demonstration.

See
[`docs/architecture/durable-engagement-orchestration.md`](docs/architecture/durable-engagement-orchestration.md).


### Component 3 — Recurring audit scheduler and automatic launcher

- IANA-time-zone-aware iCalendar recurrence calculation;
- deterministic audit-period, business-calendar, holiday, and blackout handling;
- missed-occurrence policies and bounded catch-up;
- leased schedule cursors and one canonical occurrence per nominal due time;
- versioned schedule and template snapshots on each occurrence;
- fail-closed connector, budget, competency, independence, concurrency, and overlap preflight;
- approval-before-preflight, approval-after-preflight, and automatic launch modes;
- deterministic engagement identity and Component 2 orchestrator handoff;
- launch retry, attempt tracking, and interrupted-launch recovery;
- future-horizon simulation and backend scheduler APIs.

See
[`docs/architecture/recurring-audit-scheduler.md`](docs/architecture/recurring-audit-scheduler.md).

### Component 4 — Content-addressed evidence vault and provenance

- tenant-scoped immutable content-addressed object storage;
- distinct acquisition identities over deduplicated bytes;
- idempotent acquisition keys and source provenance;
- append-only, hash-chained custody events;
- explicit original-to-derivative transformation lineage;
- taint and legal-hold propagation with mixed-classification safeguards;
- fail-closed digest and size verification;
- retention-controlled tombstoning and conservative garbage collection;
- deterministic evidence ZIP exports containing objects, complete custody chains, and lineage;
- independent export verification and bounded backend upload APIs;
- complete Asteria evidence-vault demonstration.

See
[`docs/architecture/evidence-vault-and-provenance.md`](docs/architecture/evidence-vault-and-provenance.md).


### Component 5 — Connector SDK and local fixture connectors

- tenant-scoped connector instances containing credential references, never credential values;
- purpose-bound, time-bound, read-only collection grants with exact stream and resource selectors;
- idempotent collection runs, provider-native checkpoints, failure recovery, and source-version conflict detection;
- normalized source objects persisted into the evidence vault with request, grant, and provider provenance;
- schema fingerprinting and drift detection between successful runs;
- bounded live HTTP transport with distinct authentication, permission, rate-limit, protocol, and availability failures;
- deterministic fixture transport that rejects unregistered network calls;
- GitHub pull-request, Jira issue, Confluence page, Google Drive snapshot, and Google Drive incremental-change adapters;
- complete Asteria connector demonstration covering four source systems.

See
[`docs/architecture/connector-sdk-and-collection-grants.md`](docs/architecture/connector-sdk-and-collection-grants.md).

### Component 6 — Deterministic control-test engine and versioned registry

- Ed25519-signed, immutable semantic-versioned test packages;
- canonical release, run, dataset-binding, and exception history;
- exact dataset and parameter schemas with evidence-binding requirements;
- population reconciliation and duplicate-primary-key detection;
- full-population and deterministic hash sampling;
- bounded network-denied Python subprocess execution;
- read-only SQL execution over ephemeral normalized datasets;
- typed result taxonomy, exception records, audit events, and outbox delivery;
- input, execution, and result manifests with SHA-256 identities;
- reproducibility verification against exact release and input hashes;
- released SCM-01 and IAM-01 procedures and an orchestrator task adapter.

See [`docs/architecture/deterministic-control-test-engine.md`](docs/architecture/deterministic-control-test-engine.md).

## Local SQLite workflow

```bash
cp .env.example .env
python -m venv .venv
. .venv/bin/activate
pip install -e '.[dev]'
python scripts/migrate.py
python scripts/sync_control_test_registry.py
python scripts/validate_repo.py
pytest -q
python scripts/run_golden_demo.py
python scripts/run_orchestrator_demo.py
python scripts/run_scheduler_demo.py
python scripts/run_evidence_vault_demo.py
python scripts/run_connector_demo.py
python scripts/run_control_test_demo.py
uvicorn assuranceos.api:app --reload --port 8080
```

The SQLite fallback is controlled by `ASSURANCEOS_DATABASE_PATH`. Set
`ASSURANCEOS_DATABASE_URL` to use PostgreSQL or Cloud SQL. Evidence objects and temporary exports
are controlled by `ASSURANCEOS_EVIDENCE_ROOT` and `ASSURANCEOS_EVIDENCE_EXPORT_ROOT`; raw API
uploads are bounded by `ASSURANCEOS_MAX_EVIDENCE_UPLOAD_BYTES`.

## Local PostgreSQL workflow

```bash
docker compose up --build
```

Docker Compose runs Alembic and synchronizes signed control-test releases in a one-shot migration service before the API starts. The Google Cloud deployment uses the same sequence in a dedicated Cloud Run Job rather than the serving identity.

## Orchestrator example

```python
from assuranceos.orchestration import (
    DependencyDefinition,
    Orchestrator,
    TaskDefinition,
    WorkflowDefinition,
)

workflow = WorkflowDefinition(
    workflow_version="1.0.0",
    tasks=[
        TaskDefinition(key="collect", task_type="evidence_collection"),
        TaskDefinition(
            key="test",
            task_type="deterministic_test",
            dependencies=[DependencyDefinition(task_key="collect")],
        ),
    ],
)

orchestrator = Orchestrator(database)
orchestrator.compile_workflow(
    tenant_id="tnt_example",
    engagement_id="eng_example",
    workflow=workflow,
)
orchestrator.start_engagement(
    tenant_id="tnt_example",
    engagement_id="eng_example",
)
```

Workers use the same service contract locally or behind a queue adapter. The orchestrator itself
contains no model or connector execution code.

## Endpoints

Existing endpoints:

- health: `GET /health`
- agent registry: `GET /api/v1/agents`
- golden engagement: `POST /api/v1/demo/run`
- demo events: `GET /api/v1/demo/events`
- deterministic reset: `POST /api/v1/demo/reset`
- existing Judge Mode route: `GET /judge`

Orchestration endpoints:

- compile graph: `POST /api/v1/tenants/{tenant_id}/engagements/{engagement_id}/workflow`
- start engagement: `POST /api/v1/tenants/{tenant_id}/engagements/{engagement_id}/start`
- inspect state: `GET /api/v1/tenants/{tenant_id}/engagements/{engagement_id}/orchestration`
- approve gate: `POST /api/v1/tenants/{tenant_id}/tasks/{task_id}/gate/approve`
- reject gate: `POST /api/v1/tenants/{tenant_id}/tasks/{task_id}/gate/reject`
- cancel engagement: `POST /api/v1/tenants/{tenant_id}/engagements/{engagement_id}/cancel`
- run local orchestrator demo: `POST /api/v1/demo/orchestration/run`


Scheduler endpoints:

- simulate horizon: `POST /api/v1/tenants/{tenant_id}/schedules/{schedule_id}/simulate`
- evaluate due work: `POST /api/v1/tenants/{tenant_id}/schedules/{schedule_id}/evaluate`
- list occurrences: `GET /api/v1/tenants/{tenant_id}/schedules/{schedule_id}/occurrences`
- inspect occurrence: `GET /api/v1/tenants/{tenant_id}/occurrences/{occurrence_id}`
- approve launch: `POST /api/v1/tenants/{tenant_id}/occurrences/{occurrence_id}/approve`
- cancel occurrence: `POST /api/v1/tenants/{tenant_id}/occurrences/{occurrence_id}/cancel`

Evidence-vault endpoints:

- acquire original: `POST /api/v1/tenants/{tenant_id}/evidence`
- create derivative: `POST /api/v1/tenants/{tenant_id}/evidence/derived`
- list metadata: `GET /api/v1/tenants/{tenant_id}/evidence`
- inspect record: `GET /api/v1/tenants/{tenant_id}/evidence/{evidence_id}`
- retrieve content: `GET /api/v1/tenants/{tenant_id}/evidence/{evidence_id}/content`
- verify bytes: `POST /api/v1/tenants/{tenant_id}/evidence/{evidence_id}/verify`
- inspect custody: `GET /api/v1/tenants/{tenant_id}/evidence/{evidence_id}/custody`
- inspect lineage: `GET /api/v1/tenants/{tenant_id}/evidence/{evidence_id}/lineage`
- update retention: `PUT /api/v1/tenants/{tenant_id}/evidence/{evidence_id}/retention`
- tombstone record: `POST /api/v1/tenants/{tenant_id}/evidence/{evidence_id}/purge`
- create export: `POST /api/v1/tenants/{tenant_id}/evidence-exports`


Connector endpoints:

- register instance: `POST /api/v1/tenants/{tenant_id}/connectors`
- list instances: `GET /api/v1/tenants/{tenant_id}/connectors`
- approve collection grant: `POST /api/v1/tenants/{tenant_id}/connectors/{connector_instance_id}/grants`
- list grants: `GET /api/v1/tenants/{tenant_id}/collection-grants`
- revoke grant: `POST /api/v1/tenants/{tenant_id}/collection-grants/{grant_id}/revoke`
- inspect collection run: `GET /api/v1/tenants/{tenant_id}/connector-runs/{run_id}`

Control-test endpoints:

- list released tests: `GET /api/v1/control-tests`
- inspect exact release: `GET /api/v1/control-tests/{test_id}/versions/{version}`
- execute test: `POST /api/v1/tenants/{tenant_id}/control-test-runs`
- inspect run: `GET /api/v1/tenants/{tenant_id}/control-test-runs/{run_id}`
- verify reproducibility: `POST /api/v1/tenants/{tenant_id}/control-test-runs/{run_id}/verify-reproducibility`

Collection execution is a worker/service contract rather than a public unauthenticated HTTP route.
The worker must resolve the registered credential reference through an approved secret provider and
instantiate the matching adapter.

All management and worker routes use the release JWT, role-permission, tenant-isolation, and verified-actor controls. These are backend contracts and do not add or modify UI behavior.

## Google Cloud / ADK

The core application runs without cloud credentials. The optional cloud extra contains the Google
ADK and Vertex AI Agent Engine adapter:

```bash
pip install -e '.[cloud]'
export GOOGLE_CLOUD_PROJECT=your-project
export GOOGLE_CLOUD_LOCATION=us-central1
python scripts/deploy_adk_agent.py --agent engagement-director --dry-run
```

A future Pub/Sub subscriber can call the same worker/orchestrator interfaces. Queue
acknowledgements must occur only after the state transaction commits.

## Repository boundaries

- `src/assuranceos/db/`: canonical database models, sessions, and repositories;
- `src/assuranceos/orchestration/`: workflow compiler, runtime state machine, worker, and replay;
- `src/assuranceos/scheduling/`: recurrence, periods, preflight, occurrence state, and launch;
- `src/assuranceos/vault/`: immutable storage, custody, lineage, retention, and exports;
- `src/assuranceos/connectors/`: grants, runs, checkpoints, transports, and provider adapters;
- `src/assuranceos/control_testing/`: signed registry, reconciliation, sampling, runtimes, manifests, and worker adapter;
- `migrations/`: Alembic database history;
- `agents/`: signed agent contracts and ADK entrypoints;
- `audit-packs/`: executable methodology packages;
- `tests-library/`: deterministic analytics, separate from language-model agents;
- `examples/workflows/`: executable workflow definitions;
- `demo/asteria/`: synthetic source systems and ground truth;
- `infrastructure/terraform/`: compact Google Cloud-first foundation;
- `apps/`: unchanged through Component 5.

This remains a hackathon backend, not a production audit system. It deliberately fails closed when
evidence or approvals are missing, and it contains synthetic data only.
