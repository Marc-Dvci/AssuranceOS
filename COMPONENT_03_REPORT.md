# Component 3 report — Recurring audit scheduler and automatic launcher

## Outcome

AssuranceOS can now turn an approved recurring schedule into durable, auditable occurrences and
launch the corresponding engagement through the Component 2 orchestrator. The implementation runs
locally without Cloud Scheduler, Pub/Sub, model credentials, or frontend changes.

## Main additions

- `src/assuranceos/scheduling/definitions.py`: typed schedule, calendar, period, preflight,
  occurrence, decision, simulation, and evaluation contracts.
- `src/assuranceos/scheduling/recurrence.py`: IANA-time-zone-aware iCalendar recurrence engine.
- `src/assuranceos/scheduling/calendar.py`: business-day and blackout resolution.
- `src/assuranceos/scheduling/periods.py`: deterministic calendar-month and rolling-day periods.
- `src/assuranceos/scheduling/preflight.py`: fail-closed launch policy evaluation.
- `src/assuranceos/scheduling/repository.py`: cursor leases, occurrence deduplication, and retry
  discovery.
- `src/assuranceos/scheduling/service.py`: materialization, decisions, preflight, launch, and
  recovery orchestration.
- `src/assuranceos/scheduling/demo.py`: local Asteria semiannual schedule demonstration.
- `migrations/versions/0003_recurring_audit_scheduler.py`: schedule configuration, cursor, and
  occurrence-provenance schema.
- backend API routes for simulation, evaluation, occurrence inspection, approval, and cancellation.

## Implemented behavior

- iCalendar `RRULE` calculation in the organization's IANA time zone;
- local wall-clock recurrence across daylight-saving transitions;
- schedule effective windows;
- deterministic calendar-month and rolling-day audit periods;
- business calendars, holidays, weekend rules, and blackout delay or skip;
- `launch_all`, `launch_latest`, and `skip` missed-occurrence policies;
- bounded catch-up behavior;
- one canonical occurrence per schedule and nominal due time;
- scheduler cursor leasing and idempotent reevaluation;
- version and configuration snapshots on each occurrence;
- fail-closed connector, budget, competency, independence, concurrency, and overlap preflight;
- approval-before-preflight, approval-after-preflight, and automatic launch modes;
- attributable approval and cancellation rationale;
- deterministic engagement identity and orchestrator handoff;
- launch-attempt tracking, delayed retry, and stale-`launching` recovery;
- transactional schedule events and outbox records;
- future-horizon simulation without canonical-state mutation.

## Demonstrated path

The local demonstration:

1. seeds an approved Asteria rolling audit plan;
2. registers a released software-change-management engagement template;
3. creates a semiannual Europe/Paris schedule;
4. simulates future nominal times and periods;
5. performs connector, budget, competency, and independence preflight;
6. creates one durable due occurrence;
7. creates one deterministic engagement;
8. compiles and starts the existing five-task SCM workflow.

## Design characteristics

- Recurrence and launch decisions are deterministic application services, not prompts.
- Schedule configuration is separate from mutable scheduler cursor state.
- Nominal due time is never rewritten by blackout or retry behavior.
- Missing preflight context blocks launch.
- A retry cannot create a second occurrence or engagement.
- Existing orchestration contracts remain queue neutral.
- SQLite supports deterministic behavioral tests; PostgreSQL paths add row locking for concurrent
  workers.
- All files under `apps/` remain unchanged.

## Deferred

- schedule-authoring and approval APIs;
- event-triggered and risk-threshold schedules;
- customer-specific fiscal calendar adapters;
- Cloud Scheduler/Pub/Sub transport and authenticated service identity;
- notification delivery;
- board-facing coverage and repeated-deferral reporting;
- production authorization on scheduler API routes.

## Validation

- 46 automated tests passed.
- Fresh Alembic upgrade to revision `0003_recurring_scheduler` passed.
- Upgrade from the populated Component 2 schema passed with foreign-key integrity retained.
- Alembic model-drift check reported no new operations.
- Python compilation passed.
- Repository and Audit Pack validation passed.
- Local scheduler demonstration launched the durable orchestrated engagement.
- Scheduler HTTP contract smoke checks passed.
- Artifact manifest and extracted ZIP verification passed.
- Frontend file hashes match Component 2 exactly.
