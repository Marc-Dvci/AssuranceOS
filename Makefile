.PHONY: install validate migrate test coverage run demo orchestrator-demo scheduler-demo evidence-demo connector-demo control-test-demo loop-demo pack-demo portfolio-demo governance-demo sync-control-tests openapi manifest outbox docker zip clean

install:
	python -m pip install -e '.[dev]'

validate:
	python scripts/validate_repo.py

migrate:
	python scripts/migrate.py

test:
	pytest -q

coverage:
	pytest --cov=assuranceos --cov-report=term-missing --cov-fail-under=85

run:
	uvicorn assuranceos.api:app --reload --port 8080

demo:
	python scripts/run_golden_demo.py

orchestrator-demo:
	python scripts/run_orchestrator_demo.py

scheduler-demo:
	python scripts/run_scheduler_demo.py

evidence-demo:
	python scripts/run_evidence_vault_demo.py

connector-demo:
	python scripts/run_connector_demo.py

loop-demo:
	python scripts/run_assurance_loop_demo.py

pack-demo:
	python scripts/run_pack_compiler_demo.py

portfolio-demo:
	python scripts/run_portfolio_demo.py

governance-demo:
	python scripts/run_governance_demo.py

control-test-demo:
	python scripts/run_control_test_demo.py

sync-control-tests:
	python scripts/sync_control_test_registry.py

outbox:
	python scripts/run_outbox_dispatcher.py --worker-id local-publisher

openapi:
	python scripts/generate_openapi.py
	python scripts/generate_openapi.py --check

manifest:
	python scripts/build_artifact_manifest.py
	python scripts/build_artifact_manifest.py --check

docker:
	docker compose up --build

clean:
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
	rm -rf .pytest_cache .coverage htmlcov
	find var -type f \( -name '*.db' -o -name '*.sqlite3' \) -delete 2>/dev/null || true

zip:
	python scripts/build_release_archive.py
