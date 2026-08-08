# Local backend demonstrations

Every demonstration below is offline and deterministic unless it is given a
model endpoint. Steps 1–4 are prerequisites: several demonstrations write to the
canonical database and expect the schema and the signed test registry to exist.

1. Install: `pip install -e '.[dev]'`.
2. Migrate: `python scripts/migrate.py`.
3. Sync the signed control-test registry: `python scripts/sync_control_test_registry.py`.
4. Validate release material: `python scripts/validate_repo.py`, then `pytest -q`.

## One tenant, the whole lifecycle

Each demonstration below proves one component, and each owns its own tenant so a
repeat run starts clean. That is right for a component proof and wrong for the
product: the cockpit reads one tenant, so an evaluator who runs them
individually finds the plan in one tenant, the report in another, and the
reasoning trace in a third, and no single screen shows the lifecycle.

    make seed-demo          # or: python scripts/seed_demo_tenant.py

runs every demonstration below, in dependency order, into the tenant the product
routes read (`tnt_asteria_demo`). Only the first stage clears it; the rest
compose. Add `--model-mode local --base-url ... --model ...` to drive the two
model-backed stages with a real model instead of the scripted client.

Run this before showing the interface to anybody.

## The demonstrations

| Command | What it shows |
| --- | --- |
| `python scripts/build_demo_corpus.py` | regenerates the 56-file Asteria corpus; seeded, so hashes are stable |
| `python scripts/run_golden_demo.py` | collects the whole corpus as evidence and runs the SCM-01 population test |
| `python scripts/run_control_test_demo.py` | all three signed control tests over the real populations, plus the access-review observation |
| `python scripts/run_orchestrator_demo.py` | the durable engagement graph, replayed and compared to canonical state |
| `python scripts/run_scheduler_demo.py` | a recurring audit occurrence launching the same durable workflow |
| `python scripts/run_evidence_vault_demo.py` | content-addressed storage, custody, and signed export |
| `python scripts/run_connector_demo.py` | read-only collection grants against stubbed provider APIs |
| `python scripts/run_pack_compiler_demo.py` | compiling a signed Audit Pack, and the six ways it refuses |
| `python scripts/run_portfolio_demo.py` | risk assessment and the capacity-bounded plan |
| `python scripts/run_assurance_loop_demo.py` | the full loop: finding, human gate, remediation, independent retest |
| `SLA-01`, via `seed_demo_tenant.py` | the cross-system finding: a contract amendment the procedure and the ticketing configuration never caught up with |
| `python scripts/run_reporting_demo.py` | evidence-grounded rendering and tamper detection |
| `python scripts/run_governance_demo.py --render-chain` | identity, gateway, Model Armor, and the correlated reasoning chain |
| `python scripts/run_agent_evaluations.py --mode contract` | the release qualification gate across the signed fleet |

## Against a real model

The governance and loop demonstrations accept a model endpoint. Everything else
stays deterministic.

```bash
python scripts/run_governance_demo.py \
  --model-mode local \
  --base-url http://127.0.0.1:5000/v1 \
  --model <your-model> \
  --render-chain
```

Use `--model-mode gemini` for Gemini 3.6 Flash through the Google GenAI SDK.

## The product surfaces

```bash
uvicorn assuranceos.api:app --reload --port 8080
```

`/` is the operator cockpit, `/judge` is the evaluator surface, and `/health`
reports the release and model configuration.

## On Windows

Deterministic control tests refuse to run where the sandbox cannot enforce
resource limits, which is every Windows host — `resource.setrlimit` is POSIX
only. To run them locally anyway, set
`ASSURANCEOS_CONTROL_TEST_ALLOW_DEGRADED_SANDBOX=true`. The degraded run is
recorded as degraded in its own output, and production configuration rejects the
flag.

All demonstration data is synthetic. See `demo/asteria/CORPUS.md`.
