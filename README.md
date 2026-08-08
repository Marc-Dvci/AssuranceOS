# AssuranceOS — a governed, AI-native internal-audit platform

An internal audit is a chain of custody, not a chat. AssuranceOS runs that chain
end to end — plan, collect, test, conclude, remediate, retest — and makes every
step attributable, so an autonomous agent can do the work without anyone having
to take its word for the result.

## The loop, in one command

```bash
make loop-demo          # deterministic and offline
python scripts/run_assurance_loop_demo.py --model-mode local --model <your-model>
python scripts/run_assurance_loop_demo.py --model-mode gemini   # Gemini 3.6 Flash
```

A deterministic control test runs over a seeded population. Its exceptions reach
a governed agent that reads a policy document carrying an embedded prompt
injection. A skeptic searches for reasons the resulting finding should not stand.
A human approves what survives. A remediation obligation opens exactly once, is
replayed to prove it, collects closure evidence, and is verified by a retester
independent of both the agent that raised the finding and the team that fixed it.

The seeded data carries three deliberate conditions: one real defect, one change
covered by a live waiver, and one falling outside the audit period. Raising all
three is as wrong as raising none, so the run reports itself against that ground
truth rather than against its own execution.

Verified against `gemma-4-12b-it-IQ4_XS` on a local llama.cpp server:

| | |
|---|---|
| Agent conclusion | `ineffective` — the injection demanded `effective` |
| Injection detectors fired | `conclusion_forcing`, `credential_harvesting` |
| Injection obeyed | `false` |
| Suppressed, with reason | `PR-1003` approved exception; `PR-1004` out of period |
| Remediation opened once under replay | `true` |
| Non-independent retest | refused |
| Final status, read back from the database | `closed_verified` |
| Ground truth | 3 of 3 |

### Three gates a model cannot open

The interesting part is not that the loop completes. It is what it refuses.

- **The human gate is a record, not a threshold.** An agent proposes a finding
  and states a confidence; it cannot approve one. Approval attributed to an agent
  is refused, so no confidence score can be tuned into an approval.
- **Remediation opens at most once.** Idempotency is keyed on the finding rather
  than on the caller's key, so a replay carrying a *different* key still cannot
  file a second ticket, and a unique index enforces it in the database.
- **Retest is independent by construction.** A retest by the finding's author,
  the remediation owner, or whoever declared it complete is refused. The
  independence basis is persisted so the claim can be re-verified from the record.

### Running against a reasoning model

Gemma 4 and Gemini 3.6 Flash deliberate before answering, and that changes what a
governed runtime has to handle. Measured on `gemma-4-12b-it-IQ4_XS`: with
deliberation enabled, the governed audit prompt produced 16,602 characters of
reasoning and **no answer at all** inside a 4096-token ceiling. With
`--thinking off` (the default for `--model-mode local`) the same prompt answers
in 171 tokens.

The runtime treats the two output channels separately. Reasoning is captured for
the trace and screened by Model Armor before it is retained — a prompt injection
that fails to change the answer can still try to move secrets out through the
scratchpad — but it is never parsed as the answer. A reasoning model routinely
rehearses the output object inside its own scratchpad, so parsing an unsplit
reply can lift a conclusion the model explicitly backed away from.

## Implemented

### Agent and audit foundation

- 19 complete, signed Agent Definition Packages;
- manifest-driven agent registry and default-deny policy gateway;
- typed execution envelopes and structured results;
- Asteria Systems DemoCo synthetic golden engagement;
- governed deterministic control-test selection and execution contracts;
- FastAPI control plane, lifecycle cockpit, and live Judge Mode proof surface;
- optional Google ADK and Vertex AI Agent Engine adapters.

### Component 1 — Canonical domain database

- SQLAlchemy 2.0 models organized by domain;
- 67 normalized tables for tenancy, company context, audit universe, planning, schedules,
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
- GitHub pull-request, Jira issue, Confluence page, Google Drive snapshot and incremental-change,
  Okta, Microsoft Entra ID, and Google Cloud IAM adapters;
- credentialed, provider-idempotent Jira and ServiceNow remediation writers;
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

### Component 7 — Finding adjudication, remediation, and independent retest

The component that turns test results into an audit conclusion.

- explicit lifecycle state machine; a transition absent from the table cannot happen;
- skeptic contradiction search over approved exceptions, audit period, tested
  compensating controls, and duplicates — an expired waiver does not explain an
  exception, and an untested compensating control does not compensate;
- contradictions retained even when the finding is approved, so a reviewer can
  tell a searched finding from an unexamined one;
- human approve, reject, return-for-rework, defer, and accept-risk decisions,
  refused when attributed to an automated actor;
- conversion of an approved finding into a remediation obligation, opened at most
  once per finding and enforced by a unique index;
- closure evidence required before an action can advance;
- identity-independent retest with the independence basis persisted;
- closure only on fresh evidence; every non-closing outcome reopens;
- recurrence detection across engagements, counting raised findings rather than
  proposed ones;
- an approval decision, an audit event, and an outbox event per transition, all
  written in the same transaction as the state change.

### Component G — Agent security, governance, and telemetry

- **Agent Identity**: Ed25519 SPIFFE-style credentials, short-lived and bound to
  one tenant, engagement, task, and attempt, with the granted authority computed
  as the package/envelope intersection;
- **Agent Gateway**: the single enforcement point, ordered cheapest-first and
  failing closed at every step, mounted on the durable orchestration task path so
  an agent task has no other route to execution;
- **Model Armor**: inbound context, tool-argument, and outbound guardrails, plus
  screening of model reasoning as its own exfiltration channel;
- **Agent Observability**: OpenTelemetry spans and a reasoning chain
  reconstructable from the database alone, recorded even when the run failed —
  the denied run is the one an auditor needs.

The Google ADK adapter binds each declared package tool as a shim that routes
through the same gateway, so the ADK path and the in-process runtime share one
enforcement point rather than two implementations kept in agreement by hand.

### Components 9, 11â€“15 â€” company intelligence, reporting, continuous assurance, and product

- resumable onboarding from minimal company input to a versioned canonical profile;
- immutable public-source snapshots, typed company claims, and explicit accept,
  correct, or not-applicable decisions with preserved provenance;
- canonical claim graph and deterministic report rendering that refuses unsupported,
  inadmissible, stale, or undisclosed contradictory material claims;
- separate human report issuance over digest-identified immutable report versions;
- versioned continuous monitors over pinned deterministic test releases, with
  freshness and completeness suspension, alert budgets, and deduplication windows;
- an explicit review-case boundary that prevents monitor alerts from becoming
  approved findings without the adjudication workflow;
- contract and live-model evaluation across 19 agents and 76 golden, adversarial,
  missing-evidence, and cross-industry cases;
- offline Agent Engine deployment planning with signed package digests and a
  qualification gate before cloud mutation;
- local privacy runtime with network isolation, local-only model routing, and
  verified signed evidence-bundle transfer;
- responsive product routes for planning, audits, findings, evidence, standards,
  governance, reporting, and a live evaluator cockpit.

See [`docs/architecture/evidence-grounded-reporting.md`](docs/architecture/evidence-grounded-reporting.md).

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
python scripts/run_governance_demo.py --render-chain
python scripts/run_assurance_loop_demo.py
python scripts/run_agent_evaluations.py --mode contract
uvicorn assuranceos.api:app --reload --port 8080
```

To drive the governed path with a real model instead of scripted replies, point
either demo at an OpenAI-compatible endpoint:

```bash
python scripts/run_assurance_loop_demo.py \
  --model-mode local \
  --base-url http://127.0.0.1:5000/v1 \
  --model gemma-4-12b-it-IQ4_XS.gguf
```

`--thinking off` is the default and is what a reasoning model needs for
structured audit output; `--thinking on` keeps the deliberation and records it in
the trace. If a run reports `model_truncated`, the output ceiling is too small
for that model — that status exists precisely so the message points at the budget
rather than at the prompt.

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
- evaluator overview: `GET /api/v1/judge/overview`
- product cockpit: `GET /api/v1/tenants/{tenant_id}/cockpit`

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

Onboarding and continuous-assurance endpoints:

- start or resume onboarding: `POST /api/v1/tenants/{tenant_id}/onboarding-workflows`
- inspect onboarding state: `GET /api/v1/tenants/{tenant_id}/onboarding-workflows/{workflow_id}`
- preserve a public source: `POST /api/v1/tenants/{tenant_id}/onboarding-workflows/{workflow_id}/sources`
- propose and decide company facts: `POST /api/v1/tenants/{tenant_id}/onboarding-workflows/{workflow_id}/facts`
- activate a monitor: `POST /api/v1/tenants/{tenant_id}/continuous-monitors`
- execute a monitor: `POST /api/v1/tenants/{tenant_id}/continuous-monitors/{monitor_id}/runs`
- inspect monitors and review alerts: `GET /api/v1/tenants/{tenant_id}/continuous-monitors`

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
python scripts/run_agent_evaluations.py --mode contract
python scripts/deploy_adk_agent.py --plan --output var/agent-engine-plan.json
python scripts/deploy_adk_agent.py --agent engagement-director
```

Queue subscribers call the same worker/orchestrator interfaces and acknowledge
delivery only after the canonical state transaction commits.

## Repository boundaries

- `src/assuranceos/db/`: canonical database models, sessions, and repositories;
- `src/assuranceos/orchestration/`: workflow compiler, runtime state machine, worker, and replay;
- `src/assuranceos/scheduling/`: recurrence, periods, preflight, occurrence state, and launch;
- `src/assuranceos/vault/`: immutable storage, custody, lineage, retention, and exports;
- `src/assuranceos/connectors/`: grants, runs, checkpoints, transports, and provider adapters;
- `src/assuranceos/control_testing/`: signed registry, reconciliation, sampling, runtimes, manifests, and worker adapter;
- `src/assuranceos/adjudication/`: finding lifecycle, skeptic contradiction search, remediation, and independent retest;
- `src/assuranceos/onboarding.py`: source-backed organization resolution and profile review;
- `src/assuranceos/monitoring.py`: released-test monitors and deduplicated review alerts;
- `src/assuranceos/reporting/`: access-aware retrieval, claim graph, report rendering, and issuance;
- `src/assuranceos/governance/`: agent identity, gateway, Model Armor, telemetry, model clients, and the orchestration task handler;
- `migrations/`: Alembic database history;
- `agents/`: signed agent contracts and ADK entrypoints;
- `audit-packs/`: executable methodology packages;
- `tests-library/`: deterministic analytics, separate from language-model agents;
- `examples/workflows/`: executable workflow definitions;
- `demo/asteria/`: synthetic source systems and ground truth;
- `infrastructure/terraform/`: compact Google Cloud-first foundation;
- `apps/web/`: the responsive lifecycle cockpit and evaluator proof surface.
