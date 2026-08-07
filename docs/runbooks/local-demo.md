# Local backend demonstrations

1. Install the project with `pip install -e '.[dev]'`.
2. Run `python scripts/migrate.py`.
3. Run `python scripts/validate_repo.py`.
4. Run `pytest -q`.
5. Run `python scripts/run_golden_demo.py`.
6. Run `python scripts/run_orchestrator_demo.py`.
7. Run `python scripts/run_scheduler_demo.py`.
8. Start `uvicorn assuranceos.api:app --reload --port 8080`.
9. Inspect `/health`, the backend APIs, and the existing `/judge` route.

The three demonstrations use synthetic Asteria data. The scheduler demo creates a recurring audit
occurrence and launches the same durable workflow used by the orchestration demo.
