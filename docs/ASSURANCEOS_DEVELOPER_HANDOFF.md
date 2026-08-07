# AssuranceOS Developer Handoff

**Current release:** `v0.9.0-dev` — Components 1–7 plus the security, governance, and telemetry layer, verified against a live model
**Previous baseline:** `v0.8.0` — Components 1–6 (git tag `v0.8.0`)
**Handoff date:** 2026-08-07 (revised)
**Repository:** `github.com/Marc-Dvci/AssuranceOS`
**Scope authority:** `assuranceos_hackathon_implementation_plan_revision6.md`

---

## 0. What changed in this revision (2026-08-07)

Six commits sit on top of the imported baseline. Each is independently reviewable.
The four newest are summarised first.

### `9ae94bf` — Correctness against a real reasoning model

The governance layer had only ever run against scripted replies. Pointing it at a
live `gemma-4-12b-it-IQ4_XS` server surfaced four defects mocks cannot reach.

- **Two output channels.** llama.cpp returns deliberation in `reasoning_content`;
  other servers inline it in `<think>` tags. The runtime read only `content`, so a
  reply that spent its whole budget thinking looked like an empty answer.
- **Scratchpad leakage into the conclusion.** Parsing an unsplit reply can lift a
  conclusion out of the model's rehearsal. Observed shape: the model weighs a
  passing object, then declines in its actual answer, and the extractor returns the
  rehearsal. Reasoning is now split off before any JSON is parsed.
- **Truncation misdiagnosed as a schema fault.** Measured: with deliberation
  enabled, the governed audit prompt produced **16,602 characters of reasoning and
  no answer at all** inside a 4096-token ceiling. `enable_thinking` is now an
  explicit deployment control; with it off the same prompt answers in 171 tokens.
  `model_truncated` is a separate status, because `schema_invalid` sends an
  operator to rewrite a prompt that was never the problem.
- **Unresolvable evidence citations.** The old check only required the citation
  list to be non-empty, which the live model satisfied by citing the context header
  `"[ev_changes | jira]"` — a string that resolves to nothing. Citations are now
  checked against the evidence actually supplied, and the evidence id is labelled
  unambiguously in the prompt.

Model reasoning is now screened by Model Armor before retention: an injection that
fails to change the answer can still try the scratchpad.

Also makes the suite runnable where `setrlimit` does not exist. The sandbox's
refusal to run unenforced is correct and unchanged; the degraded mode is now
requested explicitly and only where the platform lacks the interface, so CI on
Linux keeps exercising the enforced path rather than the waiver.

### `f005ef6` — Component 7: adjudication, remediation, independent retest

The execution chain previously ended at deterministic test results. It now closes:
exception → proposed finding → skeptic contradiction search → human gate →
remediation obligation → closure evidence → independent retest → closed or
reopened. Migration `0009_finding_adjudication` applies and reverses cleanly.

Three gates are structural rather than conventional. An agent cannot approve a
finding. Remediation is keyed on the finding, so a replay carrying a *different*
key still cannot open a second action, enforced by a unique index. A retest by the
finding's author, the remediation owner, or whoever declared it complete is
refused, compared case-insensitively.

### `ab024de` — The gateway mounted on the real task path

The governance layer was a library something had to remember to call.
`GovernedAgentTaskHandler` registers against an orchestration task type, so an
agent task has no other route to execution. The execution envelope is derived from
the lease — canonical state — never from model output. Governed outcomes map onto
retry semantics by what a retry would achieve: only an unreachable model is
retryable, and truncation is a configuration fault.

### `29b7088` and `d235fa3` — ADK domain tools, and the end-to-end loop

In ADK the tool list *is* the security boundary. Each declared package tool is now
bound as a shim routing through the same gateway, so the ADK path and the
in-process runtime share one enforcement point rather than two implementations
kept in agreement by hand. Denials return to the model as readable JSON so it can
choose a permitted action; the decision is recorded either way.

`scripts/run_assurance_loop_demo.py` runs the whole chain and reports itself
against the seeded Asteria ground truth rather than against its own execution. See
section 3.

---

### `3baa26d` — Baseline hygiene

Section 8 of the original handoff is complete, plus three portability defects it did
not know about.

The suite passed only on Linux. 40 of 125 tests failed on Windows for three
unrelated reasons, all now fixed:

- **Vault storage.** `put_bytes` sealed the object with `chmod(0o444)` while the
  temporary hard link still pointed at the same inode, so the temporary became
  undeletable on Windows. The temporary name is now dropped *before* the target is
  sealed. This also closed a latent POSIX hazard: unlinking after the seal would
  have required a `chmod` that unseals the target through the shared inode.
- **Time zones.** Windows ships no system IANA database, so the recurring
  scheduler could not resolve any zone name. `tzdata` is now a Windows-only
  dependency.
- **Control-test sandbox.** Memory and CPU limits use the POSIX `resource`
  interface, which Windows does not provide. Rather than skip the limits silently,
  the worker now refuses to run unless the caller explicitly requests a degraded
  sandbox. The choice is recorded as `resource_limits_enforced` in the run
  environment and rejected outright by the production configuration.

Also: `MODULE.bazel` version, the `Makefile` `zip` target, and
`docs/architecture/starter-scope.md` are corrected; the Gemini default moved to
`gemini-3.5-flash` because **the hackathon mandates Gemini 3.5 or newer** and the
tree was pinned to `gemini-2.5-flash`.

**Lint determinism.** CI ran `ruff check` with an unpinned version and the implicit
default ruleset, which widens between ruff releases: ruff 0.16 reports 301 findings
on untouched baseline files, so CI would have failed on an unrelated dependency
bump. The ruleset is now pinned in `pyproject.toml` to the set the baseline was
written against, and the 4 genuine findings within it are fixed. Those 301
modernisation findings (`UP017`, `BLE001`, `I001`, …) remain available as a
separate, deliberate change.

### `4829b05` — Security, governance, and telemetry layer

The plan named Agent Identity, Agent Gateway, Model Armor, and Agent Observability
in prose, but **no code implemented any of them** and nothing in the tree imported
`opentelemetry`. That layer now exists as `src/assuranceos/governance/`. See
section 5A.

---

## 1. Executive summary

AssuranceOS is a governed, AI-native internal-audit platform. The current repository is a backend-first implementation of the platform foundation. It contains six executable components, production-oriented security hardening, a synthetic company and golden engagement, signed agent and Audit Pack releases, deterministic demonstrations, migrations, tests, APIs, container definitions, and Google Cloud infrastructure definitions.

The current release is not the complete product. It stops after deterministic control testing. The next developer must complete the business-facing audit lifecycle: compile Audit Packs into engagements, onboard and profile organizations, construct the audit universe and risk-based plan, adjudicate findings, manage remediation and independent retesting, generate evidence-grounded reports, execute the agent fleet on Google ADK/Gemini, add the remaining connectors and Audit Packs, and prove the complete system in Google Cloud and Judge Mode.

The original implementation plan remains authoritative. Do not reduce or redefine unimplemented capabilities out of scope. Repository directories marked `contract_defined` preserve intended boundaries but are not working services.

## 2. Starting artifact and integrity

Use the consolidated archive, not an earlier component ZIP:

```text
assuranceos-backend-v0.8-components-01-06.zip
```

The archive has no Git history. Create a repository and commit the extracted v0.8 tree as the immutable baseline before making changes.

Verify the supplied checksum before work begins:

```bash
sha256sum -c assuranceos-backend-v0.8-components-01-06.sha256
```

The release archive intentionally excludes runtime databases, caches, coverage output, compiled files, and private keys.

## 3. Current verified release state

Measured on Linux (`python:3.12-slim`) on 2026-08-07, not quoted from a prior report:

| | v0.8.0 baseline | current |
|---|---|---|
| Canonical tables | 43 | **47** |
| Alembic migrations | 7 (head `0007_control_test_engine`) | **9** (head `0009_finding_adjudication`) |
| Automated tests | 125 | **230** |
| Statement coverage | see note | **86.83%** |
| Deterministic demonstrations | 6 | **8** |
| Signed agent packages | 19 | 19 |
| Signed Audit Pack / control tests | 1 / 2 | 1 / 2 |

Tests pass on **both** Linux and Windows. The Windows run requests the degraded
control-test sandbox automatically from `tests/conftest.py`, and only where the
platform genuinely lacks `resource`, so CI on Linux still exercises the enforced
path. Every CI step was reproduced locally, including bare `pytest` (which does
not put the working directory on `sys.path` the way `python -m pytest` does).

### Verified against a live model

The governed path and the full assurance loop were run against
`gemma-4-12b-it-IQ4_XS` on a local llama.cpp server, not only against mocks:

| | result |
|---|---|
| Agent conclusion | `ineffective` — the seeded injection demanded `effective` |
| Injection detectors fired | `conclusion_forcing`, `credential_harvesting` |
| Injection obeyed | `false` |
| Suppressed, with reason | `PR-1003` approved exception; `PR-1004` out of period |
| Remediation opened once under replay | `true` |
| Non-independent retest | refused |
| Final status, read back from the database | `closed_verified` |
| Seeded ground truth | 3 of 3 |

Reproduce with:

```bash
python scripts/run_assurance_loop_demo.py --model-mode local \
  --base-url http://127.0.0.1:5000/v1 --model gemma-4-12b-it-IQ4_XS.gguf
```

Detection and resistance are separate claims, and the demonstration reports them
separately: `injection_detectors` says the document was recognised as hostile,
`injection_obeyed` says whether the conclusion matched what it demanded.

**Note on the 87.11% coverage figure.** The original handoff quoted it, but I never
reproduced it — the measurement was started and then abandoned before completing.
The current suite measures **86.19%**, comfortably above the 85% release floor, on a
codebase that grew by roughly 1,200 statements. Whether that represents a small
regression against the original figure or a difference in measurement is **not
established**; treat 85% as the floor and 86.19% as the current measured value.

Treat these values as regression floors. A new release must not silently lower the
coverage threshold, remove tests, bypass signature verification, or alter applied
migrations.

## 4. Architecture and non-negotiable invariants

### 4.1 Canonical state

PostgreSQL is the intended production system of record. SQLite is a local and test profile. Canonical business state must not live solely in model memory, agent sessions, logs, or generated documents.

Use SQLAlchemy 2.0 and Alembic. Do not create or mutate schemas at API startup. Add a forward-only migration for every schema change and test both a fresh installation and upgrade from a populated v0.8 database.

### 4.2 Tenant isolation and actor attribution

Every tenant-owned record and operation must be tenant-scoped. Never accept an actor identity from an untrusted request body when it can be derived from the authenticated principal. Preserve JWT authorization, role permissions, trusted hosts, and production fail-closed configuration.

### 4.3 Transactional effects

A consequential domain transition should atomically write:

1. canonical state;
2. an attributable audit event;
3. an outbox event when downstream delivery is required.

External side effects require stable idempotency keys. Do not call Jira, ServiceNow, email, Pub/Sub, or another external system in a way that can produce duplicate effects after retries.

### 4.4 Agent authority

Model output does not grant authority. Agent tasks use short-lived Ed25519-signed execution envelopes bound to the tenant, engagement, task, attempt, lease, agent release, allowed tools, evidence scopes, prohibited actions, model policy, budgets, and deadline.

Any new agent tool must be declared in the signed agent package and enforced by the policy gateway. Do not add a generic unrestricted tool executor.

### 4.5 Evidence integrity

Original evidence is immutable. Derivatives are separate records with explicit lineage. Preserve content hashes, custody chains, source and collection timestamps, classifications, taint, retention, legal hold, and evidence-to-claim relationships.

Do not allow a report, finding, or agent conclusion to cite only a search snippet, model memory, or noncanonical temporary file.

### 4.6 Deterministic tests versus agents

Numerical, rule-based, population, sampling, and SQL/Python control tests remain deterministic services. Agents may select or interpret released tests but must not replace them with opaque reasoning.

Technical failures, missing evidence, incomplete populations, approved exceptions, and control failures must remain distinct outcome categories.

### 4.7 Immutable release artifacts

Agent packages, Audit Packs, and control-test packages are signed immutable releases. Modifying their files requires a new semantic version, regenerated canonical manifest, new release signature, and regression evaluation. Never overwrite a released package in place.

### 4.8 Frontend boundary

The current backend work intentionally left `apps/` unchanged. The next developer may build the UI only if that is part of the assigned work, but backend state transitions must remain real. Judge Mode must read the same runtime state and telemetry as the normal product; it must not be a separate mock dashboard.

## 5. What has been built

## Component 1 — Canonical domain database

Implemented in `src/assuranceos/models.py`, `src/assuranceos/db/`, and `migrations/`.

Delivered:

- tenants, users, roles, and tenant authorization data;
- organization profiles and versioned facts;
- audit-universe entities, risks, controls, and relationships;
- plans, templates, schedules, and schedule occurrences;
- engagements, tasks, dependencies, leases, and immutable attempts;
- evidence, transformations, claims, and lineage;
- findings, decisions, responses, remediation, and retest table foundations;
- agent releases and execution traces;
- audit events, transactional outbox, and idempotency records;
- explicit transaction boundaries and domain-specific repositories;
- SQLite/PostgreSQL-compatible migrations.

Important: some tables exist ahead of their complete business services. A table’s presence does not mean the corresponding product workflow is implemented.

## Component 2 — Durable engagement orchestrator

Implemented in `src/assuranceos/orchestration/`.

Delivered:

- typed and versioned workflow definitions;
- DAG compilation and cycle rejection;
- dependency-driven task activation;
- worker claims, leases, heartbeats, and stale-lease recovery;
- immutable task attempts;
- retries with bounded backoff and explicit failure classes;
- deadlines, cancellation, downstream blocking, and recovery;
- pre-execution and post-execution human gates;
- state transitions coupled to audit events and outbox events;
- event replay and canonical-state comparison;
- authenticated worker APIs;
- signed lease-bound execution authority;
- local worker and Asteria orchestration demonstrations.

The orchestrator intentionally contains no provider, model, or test implementation logic. Extend it through handlers/adapters rather than embedding domain execution in the scheduler or API.

## Component 3 — Recurring audit scheduler

Implemented in `src/assuranceos/scheduling/`.

Delivered:

- IANA-time-zone-aware recurrence and iCalendar rules;
- business calendars, holidays, blackout windows, and audit-period calculation;
- missed-run and bounded catch-up policies;
- immutable schedule versions and approvals;
- leased scheduler cursors;
- deduplicated canonical occurrences;
- connector, budget, competency, independence, concurrency, and overlap preflight;
- approval-before-preflight, approval-after-preflight, and automatic launch modes;
- deterministic engagement identity;
- launch retry, interruption recovery, and orchestrator handoff;
- future-horizon simulation and APIs.

## Component 4 — Evidence vault and provenance

Implemented in `src/assuranceos/vault/` and evidence-related APIs.

Delivered:

- tenant-scoped content-addressed storage;
- local filesystem and create-only Google Cloud Storage adapters;
- separate acquisition identity over deduplicated bytes;
- SHA-256 verification and atomic writes;
- append-only hash-chained custody events;
- original/derivative lineage;
- content inspection and taint propagation;
- classification and legal-hold controls;
- retention-controlled tombstoning and conservative garbage collection;
- deterministic signed evidence-export ZIPs;
- independent export verification;
- bounded upload and retrieval APIs.

## Component 5 — Connector SDK and four adapters

Implemented in `src/assuranceos/connectors/` and `connectors/`.

Delivered:

- connector protocol and typed provider contracts;
- connector instances with credential references rather than secrets;
- purpose-bound, time-bound, read-only collection grants;
- provider-native checkpoints, idempotent collection runs, and resume after interruption;
- schema fingerprints and drift reporting;
- request, grant, provider-version, and source-lineage provenance;
- bounded live HTTP transport and strict offline fixture transport;
- Secret Manager credential resolver;
- executable adapters for GitHub pull requests, Jira issues, Confluence pages, and Google Drive metadata/change streams;
- direct ingestion into the evidence vault;
- connector APIs and Asteria fixtures.

The following connector directories are contracts only: public web, corporate registries, regulator registers, public status pages, Gmail, Microsoft Graph, Google Cloud, Okta, Entra ID, SAP, and ServiceNow.

## Component 6 — Deterministic control-test engine

Implemented in `src/assuranceos/control_testing/` and `tests-library/`.

Delivered:

- immutable signed semantic-versioned test packages;
- database-backed release registry and run history;
- typed dataset, parameter, and output schemas;
- dataset/evidence bindings;
- population reconciliation and duplicate-key rejection;
- full-population and deterministic-hash sampling;
- bounded, network-denied Python subprocess execution;
- read-only SQL execution over ephemeral normalized data;
- typed results and exception records;
- exact package, code, input, execution, and result hashes;
- run idempotency and reproducibility verification;
- audit-event and outbox integration;
- authenticated APIs and orchestrator adapter.

Released procedures:

- `SCM-01@2.0.0`: approved change and ticket before merge;
- `IAM-01@1.0.0`: terminated-user deprovisioning deadline.

## Component G — Security, governance, and telemetry (new)

Implemented in `src/assuranceos/governance/`, `src/assuranceos/db/models/agent_governance.py`,
and migration `0008_agent_governance`. This is the Fortified Enterprise Fleet track's
headline scoring surface.

### `identity.py` — Agent Identity (zero-trust)

Short-lived Ed25519 workload credentials, SPIFFE-style
(`spiffe://assuranceos/tenant/{t}/agent/{role}/{version}`), minted by the control
plane. Two properties carry the guarantee:

- **Binding.** A credential is valid only alongside the execution envelope it was
  minted for; both must agree on tenant, engagement, task, agent role, version, and
  attempt. A credential captured from one task cannot be replayed against another.
- **Intersection.** Granted authority is `package ∩ envelope`, never the union.
  Prohibitions take the union. Neither document can widen the other, so a
  compromised envelope issuer still cannot exceed the released package.

TTL is capped by the issuer and clipped to the task deadline. Revocation is consulted
on every authentication and is backed by canonical state, so it takes effect mid-task.

### `gateway.py` — Agent Gateway (routing and policy enforcement)

The single enforcement point between an agent and everything it can reach. Ordered
cheapest-first, fails closed at every step: identity → envelope → binding → package
policy → routing → separation of duties → human gate → budget → inbound guardrails →
invocation → outbound guardrails.

`PolicyGateway` (unchanged, in `policy.py`) remains the policy **decision** point for
package semantics; this is the **enforcement** point composed around it. Keeping them
separate leaves package semantics testable on their own.

An unregistered tool is denied by default, and registering a handler cannot widen
authority beyond the signed package — both are covered by tests.

### `armor.py` — Model Armor (inline guardrails)

Deterministic, on three boundaries. A guardrail that can be argued with is not a
guardrail, so a model is never the authority for a block decision.

- **Inbound context.** Injection inside evidence is *neutralised and fenced*, not
  blocked — matching the packages' declared
  `source_taint.prompt_injection: quarantine_and_continue_without_instruction`. An
  auditor still needs to read a policy document that contains an injection payload.
- **Tool calls.** Traversal, unapproved egress, scope expansion, destructive
  statements, and self-granted authority (`approved_by`, `signature`,
  `allowed_tools`, …), screened through nested values so nothing escapes by nesting.
- **Outbound text.** Personal data and secret material, with a Luhn check so
  reference numbers are not misreported as payment cards.

Matched content is **never persisted** — only a 16-hex digest, so findings stay
correlatable without the platform becoming a second copy of the data it screens.

This complements `BaselineContentInspector`, which screens bytes at evidence ingest.
Different chokepoints: ingest-time vs inference-time.

### `telemetry.py` — Agent Observability (OpenTelemetry)

Spans are recorded in-process and OpenTelemetry export is a **bridge on top**.
Removing the optional `otel` extra costs the dashboard, never the audit trail — the
reasoning chain is a canonical record, not a telemetry side effect.

Two design decisions worth preserving:

- **Separate id spaces.** OpenTelemetry begins a new trace at every root span, so
  adopting its ids fragments a chain that legitimately has several roots. Every
  exported span instead carries `assuranceos.trace_id` / `assuranceos.span_id` as the
  documented join key. Verified in both directions against a real
  `InMemorySpanExporter`, including the multi-root case.
- **Explicit `configure_telemetry()`.** A library that installs a global
  `TracerProvider` as a side effect of constructing a tracer wins the race against
  the application — and against any test that needs its own exporter. The
  application configures; the library only consumes.

Spans carry an explicit `sequence`. Wall-clock timestamps collide below microsecond
resolution, which silently reordered the steps of a chain rebuilt from storage.

Attribute names follow OTel semantic conventions, including GenAI conventions
(`gen_ai.request.model`, `gen_ai.usage.input_tokens`, …) for model calls.

### `runtime.py` + `models_client.py` — the governed agent runtime

One bounded task end to end: mint identity → screen evidence → call model under
budget → route every requested tool through the gateway → screen the narrative →
validate against the released output schema. A conclusion of `effective` or
`ineffective` that cites no evidence is rejected.

Three model transports behind one contract: `GeminiClient` (Gemini 3.5+ via Vertex
AI or the Gemini API), `OpenAICompatibleClient` (local llama.cpp / text-generation-webui,
also the basis for Component 14), and `ScriptedClient` for deterministic tests.

### `persistence.py` — canonical state

Four new tables: `agent_identities`, `agent_gateway_decisions`,
`agent_guardrail_findings`, `agent_reasoning_spans`. A denial that exists only in a
log line cannot be reconstructed during an audit of the auditor, so decisions,
guardrail verdicts, and reasoning chains commit through the same transaction
boundary as their audit events.

### Demonstration

`python scripts/run_governance_demo.py --render-chain` runs the **real seeded Asteria
injection payload** (`demo/asteria/sources/confluence/change_management_policy.md`)
through the full path and proves, from canonical state:

1. injection detected (`conclusion_forcing`, `credential_harvesting`) and neutralised,
   with the agent still reaching the conclusion the evidence supports;
2. one legitimate tool call allowed;
3. four denials across four distinct mechanisms — undeclared tool, poisoned
   arguments, cross-task credential replay, mid-task revocation;
4. the 24-span reasoning chain rebuilt from the database, in the original order.

## Cross-component production hardening

Also implemented:

- JWT authentication and tenant permissions;
- signed agent packages and signed Audit Pack;
- signed execution envelopes;
- leased outbox dispatcher with retry and dead-letter state;
- Pub/Sub publisher adapter;
- signed evidence exports;
- non-root/read-only container profile;
- separate migration, registry-sync, API, and outbox job patterns;
- generated OpenAPI and artifact manifests;
- GitHub Actions validation workflow;
- Terraform definitions for Cloud Run, Cloud SQL, Cloud Storage, Pub/Sub, Cloud Scheduler, Secret Manager, and IAM.

These are production-shaped contracts. Cloud deployment and concurrency behavior have not been proven in the supplied sandbox.

## 6. Repository map

```text
src/assuranceos/       Executable Python application and domain services
migrations/            Alembic migrations; never edit an applied revision
agents/                 Nineteen signed Agent Definition Packages
audit-packs/            Signed SCM pack plus contract-only future packs
tests-library/          Signed SCM and IAM deterministic tests
connectors/             Implemented provider packages and contract-only connectors
demo/asteria/           Synthetic company, evidence, fixtures, and golden data
tests/                  Regression, migration, API, security, and demonstration tests
scripts/                Migrations, release signing, validation, demos, workers, archive build
api/                    Generated OpenAPI and event schemas
infrastructure/         Terraform, Cloud Run notes, policies, dashboard placeholder
security/               Public trust keys, threat/security material
services/               Logical service-boundary status documents
apps/                   Existing frontend/Judge Mode files; not developed in v0.8
docs/source/            Original implementation plan; scope authority
```

The current implementation is a modular monolith, not a set of separately deployable microservices. The `services/*` directories document logical boundaries and implementation status. Do not split services merely to match folder names unless deployment or scaling requirements justify it.

## 7. Local setup and verification

Use Python 3.11 or later.

```bash
unzip assuranceos-backend-v0.8-components-01-06.zip
cd assuranceos-backend-v0.8-components-01-06
python -m venv .venv
. .venv/bin/activate
pip install -e '.[dev]'
cp .env.example .env
python scripts/migrate.py
python scripts/sync_control_test_registry.py
python scripts/validate_repo.py
pytest -q
pytest --cov=assuranceos --cov-report=term-missing --cov-fail-under=85
python scripts/generate_openapi.py --check
python scripts/build_artifact_manifest.py --check
```

Run all deterministic demonstrations:

```bash
python scripts/run_golden_demo.py
python scripts/run_orchestrator_demo.py
python scripts/run_scheduler_demo.py
python scripts/run_evidence_vault_demo.py
python scripts/run_connector_demo.py
python scripts/run_control_test_demo.py
python scripts/run_governance_demo.py --render-chain
```

On Windows, prefix test and demo runs with
`ASSURANCEOS_CONTROL_TEST_ALLOW_DEGRADED_SANDBOX=true` (see section 0). The
OpenTelemetry bridge is exercised by the suite through the `dev` extra; the `otel`
extra adds the OTLP exporter for deployment.

Start the API:

```bash
uvicorn assuranceos.api:app --reload --port 8080
```

For PostgreSQL:

```bash
docker compose up --build
```

For all optional production integrations in a development environment:

```bash
pip install -e '.[dev,postgres,cloud,agent-cloud]'
```

## 8. Immediate housekeeping — DONE (commit `3baa26d`)

All five items are complete: `MODULE.bazel` is `0.8.0`, the `Makefile` `zip` target
delegates to `scripts/build_release_archive.py`, `starter-scope.md` describes the
current scope, OpenAPI and the artifact manifest are regenerated, and the imported
baseline is tagged `v0.8.0`.

One additional defect was found and fixed while doing it: **the artifact manifest
hashed the entire working tree**. It had no exclusion for virtualenvs or build
artifacts, so a local `.venv` added 3,503 entries (123 KB → 812 KB) and the
`--check` release gate would fail on any machine whose environment differed.
`scripts/build_artifact_manifest.py` now excludes environment and build artifacts.

## 9. Remaining product scope

## Component 7 — Finding adjudication, remediation, and independent retest — **DELIVERED** (`f005ef6`)

Implemented in `src/assuranceos/adjudication/`, migration `0009_finding_adjudication`,
23 dedicated tests plus 9 covering the end-to-end demonstration.

Delivered: observation and proposed-finding creation from accepted test results;
criteria, condition, cause, consequence, risk, severity, confidence, limitations,
and contradictory evidence; contradiction search and skeptic-review records;
management response; human approve, reject, return-for-rework, defer, and
accept-risk decisions; immutable decision rationale and approval history;
conversion of an approved finding into a remediation obligation; owner, due date,
action plan, and escalation policy; idempotent action creation that survives
replay; closure-evidence collection; identity-independent retest assignment; the
full retest outcome set; and recurrence detection across engagements.

The acceptance demonstration runs as `scripts/run_assurance_loop_demo.py` and
passes all eight of its original conditions, including against a live model. See
`docs/architecture/agent-governance-and-assurance-loop.md`.

**Not yet built, and deliberately deferred:** materiality assessment as a distinct
scored step, the dispute workflow beyond a single management response, a separate
quality-review gate distinct from the human approval gate, and *live* Jira or
ServiceNow write adapters. The remediation record carries `external_system` and
`external_ref` and the idempotency guarantee those adapters need, so the
integration is a connector task rather than a lifecycle change; it belongs with
Component 13.
## Component 8 — Standards and criteria service plus Audit Pack compiler

The signed SCM Audit Pack currently exists, but the generic pack compiler and standards layer are contract-defined.

Build:

- versioned standards, criteria, requirement, jurisdiction, applicability, effective-date, and licensing records;
- citations and source entitlement enforcement;
- standards-to-risk, control, procedure, and test mappings;
- criteria crosswalks and change-impact records;
- signed Audit Pack registry, validation, simulation, and release approval;
- deterministic compilation of a released Audit Pack plus organization context into an engagement DAG;
- explicit pinning of pack, criteria, agent, prompt, model policy, ontology, and test versions;
- migration rules for pack upgrades without mutating historical engagements;
- publish complete Identity Access, Privileged Access, and Procure-to-Pay Audit Packs;
- expand deterministic tests beyond SCM-01 and IAM-01.

Acceptance demonstration:

- compile a signed Audit Pack into the existing orchestrator without a hand-authored workflow;
- reject unsigned, incompatible, unlicensed, or schema-invalid packs;
- show exact criteria and version provenance on every procedure and finding.

## Component 9 — Guided onboarding and public company intelligence

Build the durable onboarding workflow described in the original plan.

Build:

- tenant provisioning and resumable onboarding state machine;
- minimal company input: name, domain, headquarters country, optional industry/legal entity;
- controlled public-web connector and allowlisted egress broker;
- official-site, registry, regulator, filing, trust-center, status-page, and public-repository collection;
- source quality, date, contradiction, and freshness evaluation;
- typed organization claims distinguishing observed fact, public proposal, inference, management assertion, and unknown;
- legal-entity and domain resolution;
- user accept, correct, or not-applicable decisions;
- versioned canonical organization profile;
- privacy, retention, employee-monitoring, jurisdiction, and data-residency configuration;
- connector recommendation and least-privilege scope preview;
- baseline discovery and source-coverage matrix;
- onboarding readiness gates and summary.

Guardrails:

- search snippets are discovery aids, not canonical evidence;
- do not infer wrongdoing, protected attributes, or nonpublic facts;
- public data may form risk hypotheses but never prove control effectiveness.

## Component 10 — Audit universe, risk assessment, and portfolio planning

Build:

- living audit-universe graph covering entities, units, products, processes, systems, data, locations, vendors, obligations, accounts, platforms, initiatives, and emerging risks;
- configurable inherent and residual risk scoring;
- evidence-supported ratings and confidence;
- change intensity, velocity, persistence, detectability, maturity, coverage, and external exposure;
- assurance coverage mapping;
- first-year and rolling three-year plan recommendations;
- audit objectives, scope, criteria, cadence, data needs, expected value, cost, disruption, expertise, and blind spots;
- capacity and minimum-coverage constraints;
- scenario-based plan recalculation;
- continuous-monitor candidates;
- user approval and schedule creation through Component 3.

The risk agent may recommend ratings, but configured rules and human approval determine official ratings and plans.

## Component 11 — Retrieval, claim graph, reporting, and assurance cockpit backend

Build:

- access-aware retrieval over canonical evidence and relationships;
- claim-to-evidence graph and contradictory-evidence links;
- evidence freshness and permissible-reuse checks;
- workpaper generation;
- engagement reports, executive summaries, audit-committee summaries, findings registers, remediation dashboards, assurance maps, coverage/limitation reports, and technical evidence packages;
- report templates and versioning;
- fail-closed report rendering when a material statement lacks accepted evidence or an explicit limitation;
- signed report/export packages;
- cross-engagement themes and recurrence analytics;
- backend APIs required by the full product UI and Judge Mode.

Do not let semantic retrieval become an authoritative evidence source. Every conclusion must resolve to canonical evidence IDs or an explicit limitation.

## Component 12 — Full governed agent runtime on Google ADK and Gemini

The repository contains signed agent packages and an optional ADK adapter, but it does not prove end-to-end execution of the 19-role fleet on Vertex AI Agent Engine.

Build and validate:

- agent registration and release lifecycle in the target Google platform;
- typed tool implementations for each agent role;
- execution-envelope validation on every tool call;
- model routing by task risk and complexity;
- session and memory policies that never replace canonical facts;
- structured-output validation and repair limits;
- token, cost, latency, concurrency, and context budgets;
- cancellation, timeout, retry, and degraded-state behavior;
- Model Armor or equivalent prompt-injection controls;
- Agent Gateway policy enforcement;
- correlated traces across API, agent, tool, connector, test, evidence, approval, and outbox operations;
- golden, negative, ambiguous, multilingual, cross-industry, and adversarial evaluations;
- release thresholds and rollback.

The current ADK adapter exposes signed-envelope validation as a tool. The next implementation must connect actual bounded domain tools and prove the complete task path.

## Component 13 — Additional connectors and continuous monitoring

Implement the contract-defined connectors required by the selected demo and Audit Packs. Prioritize:

1. Okta or Entra ID for identity and deprovisioning;
2. Google Cloud for IAM/configuration/change evidence;
3. ServiceNow or Jira write adapter for remediation actions;
4. public web, corporate registry, and regulator register for onboarding;
5. Gmail or Microsoft Graph read-only for approved evidence requests;
6. public status pages for resilience evidence;
7. SAP only if Procure-to-Pay is part of the final demonstration.

For each connector, retain:

- exact scopes and grant purpose;
- secret references;
- pagination/checkpoint semantics;
- schema and permission drift;
- provider-version and request provenance;
- rate-limit and retry behavior;
- source health and completeness;
- fixture replay plus live sandbox tests.

Add continuous-monitor definitions that rerun released deterministic tests, deduplicate alerts, suspend conclusions when source freshness or completeness degrades, and never convert an alert directly into an approved finding.

## Component 14 — Local privacy runtime

This is a secondary deployment mode, not a substitute for the primary Google Cloud submission.

Build:

- Docker Compose local runtime with PostgreSQL and encrypted local evidence storage;
- loopback-only `llama.cpp` gateway;
- explicit outbound-network denial;
- no silent fallback to hosted models;
- signed bundle import/export and verification;
- model/runtime/hardware release profiles;
- qualified Gemma model artifacts referenced by digest, not committed as weights;
- prompt-prefix caching and bounded retrieval context;
- local evaluation for each approved agent task;
- clear degraded-capability indicators.

## Component 15 — Product UI, Judge Mode, deployment, and submission hardening

The current Judge Mode page is a minimal static page with three actions. The original product routes and evaluator experience remain to be built.

Build:

- product routes for Home, Company Setup, Plan, Audits, Findings, Evidence, Standards, Governance, Reporting, and remediation;
- complete human approval and review experiences;
- source-backed claim cards and evidence drill-down;
- schedule, orchestration, connector, test, agent, trace, and cost views;
- read-only evaluator account;
- one-click deterministic reset;
- demo-clock advance;
- golden engagement launch;
- prompt-injection replay;
- idempotency replay;
- ground-truth comparison;
- direct trace navigation;
- visible cloud project, region, service revision, model version, and infrastructure commit;
- architecture diagram, threat model, runbook, evaluator path, cost report, limitations, and four-minute video.

Judge Mode must use deployed canonical data and telemetry, not static success claims.

## 10. External validation still required

The supplied sandbox could not establish the following. They must be executed and evidenced before claiming a production deployment:

- Docker and read-only container behavior in the final environment;
- Terraform `init`, `validate`, plan, and apply against the target Google Cloud project;
- Cloud Run services and jobs;
- Cloud SQL migrations, connection pooling, backup, restore, and concurrent row-lock contention;
- Cloud Storage immutability, retention, and lifecycle policy;
- Pub/Sub delivery, retries, ordering assumptions, and dead-letter behavior;
- Cloud Scheduler invocations and authentication;
- Secret Manager and KMS/IAM separation for release and execution keys;
- OIDC issuer, audience, JWKS rotation, and tenant claims;
- live GitHub, Jira, Confluence, Google Drive, identity, and remediation provider installations;
- Vertex AI/ADK model execution and evaluation;
- Cloud Logging, Trace, Monitoring, dashboards, and alerts;
- load, soak, fault-injection, and disaster-recovery tests;
- security review, dependency scanning, image scanning, penetration testing, DLP, residency, retention, privilege, and legal-hold validation.

Document each external proof with commands, screenshots or logs, commit SHA, resource identifiers, date, and known limitations.

## 11. Recommended implementation order

Use the following sequence to produce the strongest end-to-end hackathon story with minimal rework:

1. Finding, remediation, and independent retest.
2. Standards/criteria service and Audit Pack compiler.
3. Onboarding, public intelligence, and organization profile.
4. Audit universe, risk scoring, and portfolio recommendations.
5. Retrieval and evidence-grounded reporting.
6. Full ADK/Gemini agent runtime and evaluation.
7. Required live connectors, extra Audit Packs, and continuous monitors.
8. Cloud deployment, observability, Judge Mode, and evaluator workflow.
9. Local privacy runtime as an additional deployment proof.

Keep each component releasable independently with its own migration, architecture document, tests, demonstration, report, and consolidated archive.

## 12. Definition of done for every new component

A component is complete only when it provides:

- executable code rather than only contracts or README files;
- typed domain and API contracts;
- tenant isolation and explicit authorization;
- migrations and populated-upgrade tests where state is added;
- canonical audit events and outbox events for consequential transitions;
- idempotency and retry behavior;
- failure, denial, and recovery paths;
- evidence and trace linkage;
- automated unit, integration, API, migration, and security tests;
- statement coverage that keeps the consolidated suite at or above 85%;
- an observable deterministic demonstration;
- updated OpenAPI, artifact manifest, capability status, architecture documentation, and build report;
- release-archive verification from a clean extraction;
- no private key material or runtime databases in the archive;
- no unsupported product or compliance claims.

## 13. Release process

Before every consolidated release:

```bash
ruff check src scripts tests migrations
python scripts/validate_repo.py
python -m compileall -q src scripts tests agents
pytest -q
pytest --cov=assuranceos --cov-report=term-missing --cov-fail-under=85
python scripts/migrate.py
python scripts/sync_control_test_registry.py
python scripts/generate_openapi.py
python scripts/generate_openapi.py --check
python scripts/build_artifact_manifest.py
python scripts/build_artifact_manifest.py --check
```

Run all demonstrations and verify every signed artifact. Build the archive through `scripts/build_release_archive.py`, then extract it into a clean directory and rerun tests, migration, signature validation, OpenAPI validation, and manifest validation from the extracted copy.

If a released agent, Audit Pack, or deterministic test changes, increment its semantic version and use the appropriate release script. Do not package private signing keys.

## 14. Handoff risks and cautions

- The project is broad. Preserve a coherent vertical slice rather than creating many shallow service stubs.
- The database contains foundations for later domains. Review existing models before adding duplicate tables.
- Some `services/*` folders are status documents, not deployable services.
- The current agent fleet is signed and policy-defined, but most roles are not yet connected to real domain tools or cloud model execution.
- Terraform is production-shaped but not proven by an applied target environment in this handoff.
- Connector fixtures are deterministic; live OAuth and provider behavior need separate validation.
- The Python test runtime is bounded and network-denied but is not a hostile multi-tenant kernel boundary. Use Cloud Run Jobs or an equivalent isolated executor for production.
- Do not claim that AssuranceOS issues statutory opinions, legal conclusions, certifications, guaranteed compliance, or complete fraud detection.
- Consequential findings, risk acceptance, closure, and report issuance require accountable human approval.

## 15. Next tasks, in order

Sections 9–11 (Components 7–15) stand unchanged as the remaining product scope,
minus the governance work now delivered as Component G. Component 12's governance
requirements — execution-envelope validation on every tool call, Model Armor,
Agent Gateway policy enforcement, correlated traces — are **done**; what remains of
12 is the cloud-side registration and evaluation, listed below.

### Immediate — blocked on the owner, ~15 minutes each

1. **Live model verification.** The governed runtime has been exercised against
   `ScriptedClient` and a mocked HTTP transport, but **never against a real model
   server**. Load `gemma-4-12b-it-IQ4_XS` in text-generation-webui
   (`http://127.0.0.1:5000/v1`) and run:
   ```bash
   python scripts/run_governance_demo.py --model-mode local \
       --model gemma-4-12b-it-IQ4_XS --render-chain
   ```
   Expect schema-repair to matter here: small local models wrap JSON in prose, and
   `extract_json_object` is bounded on purpose, so a reply carrying no object must
   fail closed as `schema_invalid` rather than be invented into shape. This also
   earns the "integrate Google AI models such as Gemma" bonus.
2. **Google Cloud project.** Nothing is deployed. Everything cloud-facing below is
   blocked until a project exists with Vertex AI enabled and `gcloud` authenticated.

### Then — highest value for the judging criteria

3. **Component 7** — finding adjudication, remediation, independent retest. Still
   the right next component: the execution chain ends at deterministic test results,
   and the Agent Gateway's separation-of-duties enforcement (already built and
   tested) is exactly what an independent retest needs.
4. **Component 12, cloud half** — register the fleet on Vertex AI Agent Engine, wire
   the remaining 18 agent roles to bounded tools through the gateway, and run the
   golden/adversarial evaluations in `agents/*/evaluations.yaml` and
   `agents/*/adversarial_cases/`.
5. **Cloud Run deployment + OTLP export.** Set `OTEL_EXPORTER_OTLP_ENDPOINT` and
   confirm spans land in Cloud Trace, joined on `assuranceos.trace_id`. The bridge
   is verified against an in-memory exporter but **not against Cloud Trace**.
6. **Components 8–11, 13–15** as previously scoped.

### Known gaps in what was delivered

Stated plainly so they are not mistaken for finished work:

- The OTel bridge is verified against `InMemorySpanExporter`, not a live OTLP
  collector or Cloud Trace.
- `GeminiClient` is **not** verified against a real Gemini endpoint — only its
  missing-SDK error path is tested. It is the mandated model path, so verify it
  first once a project exists.
- The gateway is wired to the runtime and demonstrations but is **not yet mounted
  in the FastAPI surface** (`api.py`) or in the orchestrator's task-execution path.
  Agent tasks running through Component 2 do not yet pass through it.
- `adk.py` still exposes only the original toy tool. It has not been updated to use
  the governance layer.
- Model Armor's detectors are deterministic patterns. They are tuned to fire on the
  seeded adversarial cases and to stay silent on ordinary audit prose (both tested),
  but they are not a substitute for a managed DLP product, and no false-positive rate
  has been measured on real corpora.
- Separation of duties is enforced only when the caller supplies
  `independence_constraints` when minting the identity. Component 7 must actually
  pass them.

## 16. Environment notes that cost real time

- **Python.** The registered `python` on the development machine is 3.10, below the
  3.11 floor. A working 3.12 lives at
  `C:\Users\M\AppData\Roaming\uv\python\cpython-3.12.13-windows-x86_64-none\python.exe`;
  `C:\...\Programs\Python\Python311\python.exe` is **actually 3.10.7**. The repo venv
  is built from the uv interpreter.
- **Canonical suite runs on Linux.** `docker run --rm -v "${PWD}:/w" -w /w
  python:3.12-slim` is the release profile. Windows runs need
  `ASSURANCEOS_CONTROL_TEST_ALLOW_DEGRADED_SANDBOX=true`.
- **Alembic drift check.** `compare_metadata` reports 46 pre-existing
  `add_constraint`/`remove_constraint` items on baseline tables. These are a naming
  artifact — the baseline applied its naming convention twice
  (`ck_<table>_ck_<table>_<name>`) — not real drift, and migration 0008 was verified
  to add **zero** drift of its own. Do not "fix" them casually: renaming a constraint
  means altering applied migrations.
- **PowerShell 5.1** here has no `&&`, no ternary, and chokes on nested quotes in
  `docker run ... bash -lc "..."`. Put multi-step shell logic in a `.sh` file and
  mount it.
