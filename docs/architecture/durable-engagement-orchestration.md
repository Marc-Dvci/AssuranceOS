# Durable engagement orchestration

Component 2 turns an approved engagement into a durable task graph. The implementation is a
regular application service backed by the canonical database; it is not an agent prompt and does
not depend on a particular queue, model provider, or web framework.

## Design principles

- **Canonical state first.** Task and engagement state is stored in the relational system of
  record. Chat history and queue delivery are not workflow state.
- **Explicit task graph.** A `WorkflowDefinition` is validated for duplicate keys, missing
  dependencies, self-dependencies, and cycles before any task is persisted.
- **Queue neutrality.** Workers claim leases through the orchestrator. The same contract can be
  driven by a local loop, Pub/Sub subscriber, Cloud Run Job, or test harness.
- **Atomic transitions.** Canonical state, append-only audit events, and transactional outbox
  records commit together.
- **At-least-once safe execution.** Claims use compare-and-set state changes, bounded leases, task
  idempotency keys, attempt counters, and replayable events.
- **Human judgment is explicit.** Gates can occur before execution or after a task has produced a
  proposed result. A result behind a post-execution gate is not successful until approved.
- **Failure classes remain distinct.** Retryable infrastructure failures, policy violations,
  insufficient evidence, deterministic test failures, and human rejection are not collapsed into
  one generic error.
- **No workflow framework dependency.** The implementation uses focused domain objects and
  SQLAlchemy rather than introducing a general-purpose orchestration platform for the hackathon
  vertical slice.

## Task states

```text
pending
  -> ready
  -> running
  -> succeeded

running
  -> retry_wait -> ready
  -> waiting_approval -> succeeded | failed
  -> failed

pending
  -> waiting_approval -> ready | failed
  -> blocked
  -> cancelled
```

Terminal states are `succeeded`, `failed`, `blocked`, `cancelled`, and `skipped`.

## Engagement states

- `planned`: graph may be compiled but has not started;
- `running`: runnable, leased, or delayed retry work exists;
- `waiting_approval`: no runnable work exists and a human decision is required;
- `blocked`: dependency failure prevents remaining work;
- `failed`: at least one task failed and no active work remains;
- `cancelled`: an authorized actor cancelled the engagement with a reason;
- `completed`: every task succeeded or was explicitly skipped.

## Lease and retry semantics

A claim atomically changes a `ready` task to `running`, increments `attempt_count`, and records a
worker and lease expiry. A heartbeat extends only a valid, unexpired lease owned by that worker.
Expired leases are classified as `lease_expired` and pass through the same retry policy as other
failures.

Each task carries a persisted `RetryPolicy`:

- maximum attempts;
- initial delay;
- exponential multiplier;
- maximum delay;
- explicit retryable failure classes.

The orchestrator never retries after the attempt budget or task deadline is exhausted.

## Human gates

A gate has a stable identifier and one of two positions:

- **before**: dependencies complete, then the task waits for approval before a worker can claim it;
- **after**: a worker produces a typed result, then the task waits for approval before becoming
  successful.

Approval and rejection require an actor and rationale and are written to the event stream and
outbox with the task transition.

## Event replay

Audit events include a per-stream sequence number. Task events use the task identifier as their
stream; engagement events use the engagement identifier. Replay therefore does not rely on
wall-clock ordering across concurrent workers. `verify_replay()` compares the final replayed
projection with canonical task and engagement state.

## Local execution

```bash
python scripts/migrate.py
python scripts/run_orchestrator_demo.py
```

The demonstration compiles and executes:

```text
collect evidence
  -> deterministic SCM test
  -> skeptic review
  -> proposed finding [post-execution finding approval]
  -> report [pre-execution report issuance approval]
```

It uses the existing Asteria synthetic sources, completes both human gates, and verifies event
replay against canonical state.

## Cloud handoff

The transactional outbox dispatcher publishes committed events through the Google Pub/Sub adapter.
Consumers use the stable event and idempotency identifiers and acknowledge messages only after their
own state transition commits. Cloud Scheduler invokes the dedicated outbox Cloud Run job, while the
Component 3 scheduler creates schedule occurrences and hands approved launches to this orchestrator.

## Authenticated worker and agent authority

The worker API exposes claim, heartbeat, completion, and failure transitions under the dedicated
`tasks:execute` permission. The authenticated JWT subject is the lease owner; a non-admin caller
cannot submit another worker identity.

When a claimed task has an `assigned_agent_role`, `ExecutionAuthority` compiles the canonical
`TaskLease`, its explicit execution policy, and the verified Agent Definition Package into an
`ExecutionEnvelope`. Tools and evidence scopes must be explicitly persisted on the task. Package
budgets are ceilings, forbidden actions are copied from the signed package, and the envelope cannot
outlive the worker lease or task deadline. The control plane signs this object with Ed25519.

The ADK adapter accepts only the signed wrapper. It verifies the trusted issuer key, signature,
validity window, task deadline, agent identity/version, declared tools, and package prohibitions
before returning bounded authority. Heartbeats issue a fresh envelope for the extended lease.
Replay safety remains enforced by the canonical attempt number, lease ownership, task idempotency,
and downstream service transitions; the signature alone is not treated as an execution ledger.
