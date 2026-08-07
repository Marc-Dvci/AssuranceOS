# Recurring audit scheduler and automatic engagement launcher

Component 3 turns an approved audit schedule into durable schedule occurrences and, when policy
allows, orchestrated engagements. The scheduler is application code backed by the canonical
database. Cloud Scheduler may wake it later, but recurrence, deduplication, preflight, audit-period
calculation, and launch decisions do not live in a timer configuration or an agent prompt.

## Design principles

- **Nominal time is immutable.** Every occurrence is identified by the schedule and its nominal due
  time. Blackout delays change `eligible_at`; they never rewrite the nominal occurrence.
- **Configuration and execution state are separate.** Versioned schedules and templates contain the
  approved rules. `schedule_cursors` contain only mutable evaluation position and scheduler leases.
- **At most one canonical occurrence.** A database uniqueness constraint on `(schedule_id,
  nominal_due)` and deterministic occurrence identifiers make repeated evaluations idempotent.
- **Fail-closed preflight.** Missing connector, budget, competency, independence, plan, template, or
  tenant state blocks launch and is retained as a typed check result.
- **Human gates remain consequential.** The launch mode determines whether approval is required
  before preflight, after preflight, or not at all. Automatic launch cannot bypass later engagement
  gates.
- **Queue and cloud neutrality.** The scheduler invokes the existing orchestrator service. A local
  process, Cloud Scheduler request, Pub/Sub subscriber, or Cloud Run Job can call the same method.
- **Recoverable launch handoff.** `launching` is durable and has a bounded recovery time. A retry
  reuses the deterministic engagement identifier and existing task graph.

## Schedule configuration

An active schedule pins:

- tenant, plan, engagement template, and schedule version;
- iCalendar recurrence rule and IANA time zone;
- effective start and optional end;
- deterministic audit-period rule;
- business calendar and blackout windows;
- launch mode;
- missed-occurrence and catch-up policy;
- overlap and maximum-concurrency policy;
- connector, budget, competency, and independence preflight requirements.

The engagement template pins the Audit Pack reference, scope, preflight defaults, and executable
workflow definition. Each occurrence stores snapshots of both records so later edits cannot alter
why an engagement was launched.

## Evaluation algorithm

For each active schedule, one scheduler worker acquires a short database lease on the schedule
cursor and performs the following transactionally:

1. calculate due nominal times between the last completed evaluation and the current time;
2. apply the missed-occurrence policy;
3. calculate each audit period;
4. apply blackout delay or skip rules;
5. create missing occurrence records using deterministic identifiers;
6. advance the schedule cursor and calculate the next due time;
7. emit occurrence events through the transactional outbox.

After occurrence materialization, eligible occurrences pass through preflight and launch. A blocked
occurrence remains durable and can be reevaluated when source health or other context improves.

## Missed-occurrence policies

- `launch_all`: materialize up to `catch_up_limit` recent occurrences and record older coverage as
  skipped;
- `launch_latest`: record all older nominal occurrences as skipped and process only the latest;
- `skip`: record every missed nominal occurrence as skipped.

A skipped occurrence is never silently deleted. It remains visible as a coverage decision.

## Blackout behavior

A blackout window can either:

- `delay`: preserve `nominal_due`, move `eligible_at` to the next configured business day, and keep
  the occurrence retryable; or
- `skip`: preserve the occurrence as terminal `skipped` with the configured reason.

Holiday and weekend calculations use the schedule's local time zone. Recurrence calculations retain
local wall-clock intent across daylight-saving changes and store canonical UTC instants.

## Preflight checks

The built-in evaluator checks:

- active tenant;
- approved audit plan;
- active schedule;
- released engagement template;
- present workflow definition;
- required connector health;
- required competencies;
- available execution budget;
- configured independence conflicts;
- maximum concurrent engagements;
- overlapping active audit periods.

Every check is persisted with its code, observed details, result, and check time. Missing context is
not interpreted as a pass.

## Launch modes

- `approval_required`: wait for an attributable decision before running preflight;
- `preflight_then_approval`: run preflight, then wait for approval;
- `automatic`: launch immediately when preflight passes.

Approval and cancellation decisions require an actor and rationale. Repeating an approval after a
successful launch returns the existing occurrence and does not create another engagement.

## Launch recovery and idempotency

The engagement identifier is derived from the occurrence identifier. Launch first records the
occurrence as `launching`, associates the deterministic engagement, and sets a bounded recovery
eligibility time. It then compiles and starts the workflow through the Component 2 orchestrator.

If execution raises an error, the occurrence becomes `launch_failed`, retains the error, and is
eligible after the configured retry interval. If the process terminates without recording the
error, the durable `launching` occurrence becomes retryable after the same interval. Retrying:

- reuses an existing engagement;
- skips workflow compilation when tasks already exist;
- starts only a still-planned engagement;
- records attempt count and outcome events;
- finishes as one canonical `launched` occurrence.

## Local execution

```bash
python scripts/migrate.py
python scripts/run_scheduler_demo.py
```

The demonstration creates a semiannual Asteria software-change-management schedule, simulates its
future occurrences, performs preflight, creates the due occurrence, launches an engagement, and
hands the five-task workflow to the durable orchestrator.

## Cloud handoff

Cloud Scheduler should invoke an authenticated scheduler endpoint or publish a wake-up message. It
must not encode audit-period or deduplication logic. PostgreSQL row locks and cursor leases provide
multi-worker coordination; application-level occurrence uniqueness remains mandatory even when the
transport offers delivery guarantees.
