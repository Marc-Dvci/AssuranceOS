# Component 2 report — Durable engagement orchestrator

## Outcome

AssuranceOS can now compile and execute a durable engagement task graph without relying on agent
chat history, an in-memory workflow, or a cloud queue. The implementation uses the canonical
relational state introduced in Component 1 and preserves the frontend boundary.

## Main additions

- `src/assuranceos/orchestration/definitions.py`: typed workflow and runtime contracts.
- `src/assuranceos/orchestration/compiler.py`: DAG validation and canonical graph persistence.
- `src/assuranceos/orchestration/repository.py`: claims, leases, retry discovery, and dependency
  reads.
- `src/assuranceos/orchestration/service.py`: transactional engagement and task state machine.
- `src/assuranceos/orchestration/worker.py`: queue-neutral synchronous worker adapter.
- `src/assuranceos/orchestration/replay.py`: per-stream deterministic event replay.
- `src/assuranceos/orchestration/demo.py`: local SCM vertical-slice execution.
- `examples/workflows/software-change-management.json`: executable five-task workflow.
- `migrations/versions/0002_durable_orchestration.py`: task-runtime and event-order fields.
- backend API routes for workflow compilation, start, snapshots, gate decisions, and cancellation.

## Implemented behavior

- duplicate, missing-dependency, self-dependency, and cycle rejection;
- versioned task and dependency persistence;
- root-task activation and dependency-driven promotion;
- exclusive compare-and-set worker claims;
- bounded leases and heartbeats;
- retryable versus permanent failure handling;
- bounded exponential backoff;
- attempt and deadline enforcement;
- expired-lease recovery;
- pre-execution and post-execution human gates;
- attributable approval, rejection, and cancellation decisions;
- dependent-task blocking after terminal prerequisite failure;
- final engagement status reconciliation;
- canonical audit-event and outbox emission in the state transaction;
- per-task and per-engagement stream sequencing;
- replay-to-canonical verification;
- local worker execution without cloud services.

## Demonstrated workflow

The local demonstration executes the Asteria software-change-management workflow:

1. collect four synthetic evidence sources;
2. execute the deterministic SCM population test;
3. perform a skeptic-review task;
4. propose a finding and pause for finding approval;
5. pause for report-issuance approval and generate the report.

The final result contains one supported exception, rejects the approved-service-account and
out-of-period false positives, completes both human gates, and finishes with replay matching the
canonical database state.

## Design characteristics

- No general-purpose workflow framework was introduced.
- The orchestration service is independent of FastAPI, Pub/Sub, ADK, and model providers.
- Repositories remain transaction-neutral; the orchestrator owns transaction boundaries.
- Worker handlers return typed results and cannot directly mutate workflow state.
- Technical failure and evidence conclusions remain separate.
- Event replay does not depend on timestamps being unique across workers.
- All files under `apps/` remain unchanged.

## Deferred

- recurring schedule calculation and occurrence creation;
- connector-health and independence preflight;
- Pub/Sub transport adapter and Cloud Run worker deployment;
- task-attempt history as a separate table;
- administrative force-retry and force-skip operations;
- production authorization on orchestration API routes.

## Validation

- 31 automated tests passed.
- Fresh Alembic upgrade to head passed.
- Component 1 database upgrade with existing audit-event backfill passed.
- Alembic model-drift check reported no new operations.
- Python compilation passed.
- Repository and Audit Pack validation passed.
- Local orchestrator demonstration completed and replay matched canonical state.
- FastAPI health, orchestrator-demo, and orchestration-snapshot smoke checks passed.
- Frontend file hashes match Component 1 exactly.
