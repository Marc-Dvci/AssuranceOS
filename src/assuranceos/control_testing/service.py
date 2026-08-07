from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any

from jsonschema import Draft202012Validator
from sqlalchemy import select

from assuranceos.db.models import (
    ControlTestDatasetBinding,
    ControlTestException,
    ControlTestRun,
    Engagement,
    EngagementTask,
    EvidenceRecord,
)
from assuranceos.db.repositories import AuditEventRepository, OutboxRepository, new_id
from assuranceos.db.session import Database
from assuranceos.models import AuditEvent

from .definitions import (
    ControlTestRunRequest,
    ControlTestRunResult,
    DatasetContract,
    TestConclusion,
    TestExceptionResult,
)
from .exceptions import (
    ReproducibilityMismatchError,
    TestInputValidationError,
    TestReleaseNotFoundError,
    TestRunNotFoundError,
)
from .registry import ControlTestRegistry, LoadedControlTest
from .repository import ControlTestRepository
from .runtime import DeterministicRuntime


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def digest(value: Any) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


class ControlTestService:
    def __init__(
        self,
        database: Database,
        registry: ControlTestRegistry,
        *,
        runtime: DeterministicRuntime | None = None,
        require_canonical_evidence: bool = False,
    ):
        self.database = database
        self.registry = registry
        self.runtime = runtime or DeterministicRuntime()
        self.require_canonical_evidence = require_canonical_evidence

    def synchronize_registry(self, *, released_by: str = "release-pipeline") -> int:
        return self.registry.sync(self.database, released_by=released_by)

    def list_releases(self, *, domain: str | None = None) -> list[dict[str, Any]]:
        with self.database.read_session() as session:
            rows = ControlTestRepository(session).list_releases(domain)
            return [self._release_view(row) for row in rows]

    def get_release(self, test_id: str, version: str) -> dict[str, Any]:
        with self.database.read_session() as session:
            row = ControlTestRepository(session).get_release(test_id, version)
            if row is None:
                raise TestReleaseNotFoundError(f"control-test release not found: {test_id}@{version}")
            return self._release_view(row)

    def run(self, tenant_id: str, request: ControlTestRunRequest) -> ControlTestRunResult:
        release = self.registry.get(request.test_id, request.version)
        self._validate_request(release, request)
        self._validate_canonical_references(tenant_id, request)
        prepared, input_manifest, reconciliation = self._prepare_datasets(release, request)
        input_hash = digest(input_manifest)

        with self.database.transaction() as session:
            repository = ControlTestRepository(session)
            existing = repository.get_run_by_idempotency(tenant_id, request.idempotency_key)
            if existing is not None:
                if existing.input_manifest_hash != input_hash:
                    raise TestInputValidationError(
                        "idempotency key was already used with different test inputs"
                    )
                return self._result_from_row(repository, existing)
            db_release = repository.get_release(request.test_id, request.version)
            if db_release is None:
                raise TestReleaseNotFoundError(
                    f"release is not synchronized: {request.test_id}@{request.version}"
                )
            run = ControlTestRun(
                run_id=new_id("tst"),
                tenant_id=tenant_id,
                engagement_id=request.engagement_id,
                task_id=request.task_id,
                release_id=db_release.release_id,
                test_id=request.test_id,
                test_version=request.version,
                status="running",
                purpose=request.purpose,
                period_start=request.period_start,
                period_end=request.period_end,
                requested_by=request.requested_by,
                idempotency_key=request.idempotency_key,
                parameters_json=request.parameters,
                started_at=datetime.now(timezone.utc),
                population_count=reconciliation["population_count"],
                sampled_count=reconciliation["sampled_count"],
                reconciled_count=reconciliation["reconciled_count"],
                population_complete=reconciliation["population_complete"],
                input_manifest_hash=input_hash,
            )
            repository.add_run(run)
            for item in input_manifest["datasets"]:
                repository.add_binding(
                    ControlTestDatasetBinding(
                        binding_id=new_id("tds"),
                        tenant_id=tenant_id,
                        run_id=run.run_id,
                        dataset_name=item["name"],
                        dataset_role=item["role"],
                        row_count=item["row_count"],
                        sampled_row_count=item["sampled_row_count"],
                        content_hash=item["content_hash"],
                        schema_hash=item["schema_hash"],
                        evidence_ids_json=item["evidence_ids"],
                        authoritative=item["authoritative"],
                        metadata_json=item["metadata"],
                    )
                )
            self._emit_started(session, run)

        if not reconciliation["population_complete"] and release.manifest.reconciliation.require_complete:
            result_value = {
                "conclusion": TestConclusion.POPULATION_INCOMPLETE.value,
                "rows": [],
                "exceptions": [],
                "limitations": reconciliation["limitations"],
            }
            return self._finalize(
                tenant_id,
                run.run_id,
                release,
                request,
                result_value,
                environment={"runtime": "not_executed", "reason": "population_incomplete"},
                status="blocked",
                input_hash=input_hash,
                reconciliation=reconciliation,
            )

        try:
            execution = self.runtime.execute(
                release,
                datasets=prepared,
                parameters=request.parameters,
                context={
                    "tenant_id": tenant_id,
                    "run_id": run.run_id,
                    "period_start": request.period_start.isoformat(),
                    "period_end": request.period_end.isoformat(),
                    "purpose": request.purpose,
                },
            )
            Draft202012Validator(release.output_schema).validate(execution.value)
        except Exception as exc:
            with self.database.transaction() as session:
                row = ControlTestRepository(session).get_run(tenant_id, run.run_id)
                if row is None:
                    raise TestRunNotFoundError(run.run_id) from exc
                row.status = "failed"
                row.conclusion = TestConclusion.TEST_FAILED_TECHNICALLY.value
                row.failure_class = type(exc).__name__
                row.error_message = str(exc)[:8000]
                row.completed_at = datetime.now(timezone.utc)
                AuditEventRepository(session).append(
                    AuditEvent(
                        event_type="control_test.run_failed",
                        tenant_id=tenant_id,
                        engagement_id=request.engagement_id,
                        task_id=request.task_id,
                        payload={"run_id": row.run_id, "failure_class": row.failure_class},
                    )
                )
            raise

        return self._finalize(
            tenant_id,
            run.run_id,
            release,
            request,
            execution.value,
            environment=execution.environment,
            status="succeeded",
            input_hash=input_hash,
            reconciliation=reconciliation,
        )

    def get_run(self, tenant_id: str, run_id: str) -> ControlTestRunResult:
        with self.database.read_session() as session:
            repository = ControlTestRepository(session)
            row = repository.get_run(tenant_id, run_id)
            if row is None:
                raise TestRunNotFoundError(f"control-test run not found: {run_id}")
            return self._result_from_row(repository, row)

    def verify_reproducibility(
        self,
        tenant_id: str,
        run_id: str,
        request: ControlTestRunRequest,
    ) -> ControlTestRunResult:
        original = self.get_run(tenant_id, run_id)
        release = self.registry.get(original.test_id, original.version)
        self._validate_request(release, request)
        self._validate_canonical_references(tenant_id, request)
        prepared, input_manifest, reconciliation = self._prepare_datasets(release, request)
        if digest(input_manifest) != original.input_manifest_hash:
            raise ReproducibilityMismatchError("provided inputs do not match the recorded run")
        execution = self.runtime.execute(
            release,
            datasets=prepared,
            parameters=request.parameters,
            context={
                "tenant_id": tenant_id,
                "run_id": run_id,
                "period_start": request.period_start.isoformat(),
                "period_end": request.period_end.isoformat(),
                "purpose": request.purpose,
            },
        )
        Draft202012Validator(release.output_schema).validate(execution.value)
        result_hash = self._result_hash(execution.value, reconciliation)
        if result_hash != original.result_manifest_hash:
            raise ReproducibilityMismatchError(
                f"result mismatch: expected {original.result_manifest_hash}, got {result_hash}"
            )
        return original.model_copy(update={"reproducible": True})

    def _validate_canonical_references(
        self, tenant_id: str, request: ControlTestRunRequest
    ) -> None:
        with self.database.read_session() as session:
            if request.engagement_id is not None:
                engagement = session.scalar(
                    select(Engagement).where(
                        Engagement.tenant_id == tenant_id,
                        Engagement.engagement_id == request.engagement_id,
                    )
                )
                if engagement is None:
                    raise TestInputValidationError(
                        "engagement does not exist in the requested tenant"
                    )
            if request.task_id is not None:
                task = session.scalar(
                    select(EngagementTask).where(
                        EngagementTask.tenant_id == tenant_id,
                        EngagementTask.task_id == request.task_id,
                    )
                )
                if task is None:
                    raise TestInputValidationError(
                        "task does not exist in the requested tenant"
                    )
                if request.engagement_id is not None and task.engagement_id != request.engagement_id:
                    raise TestInputValidationError("task does not belong to the requested engagement")
            if self.require_canonical_evidence:
                requested = {
                    evidence_id
                    for dataset in request.datasets
                    for evidence_id in dataset.evidence_ids
                }
                if requested:
                    found = set(
                        session.scalars(
                            select(EvidenceRecord.evidence_id).where(
                                EvidenceRecord.tenant_id == tenant_id,
                                EvidenceRecord.evidence_id.in_(requested),
                                EvidenceRecord.deleted_at.is_(None),
                            )
                        )
                    )
                    missing = sorted(requested - found)
                    if missing:
                        raise TestInputValidationError(
                            f"dataset evidence references are not canonical for this tenant: {missing}"
                        )

    def _validate_request(
        self, release: LoadedControlTest, request: ControlTestRunRequest
    ) -> None:
        try:
            Draft202012Validator(release.parameter_schema).validate(request.parameters)
            dataset_by_name = {item.name: item for item in request.datasets}
            contracts = {item.name: item for item in release.manifest.datasets}
            unknown = sorted(set(dataset_by_name) - set(contracts))
            missing = sorted(
                name for name, contract in contracts.items() if contract.required and name not in dataset_by_name
            )
            if unknown or missing:
                raise TestInputValidationError(
                    f"dataset contract mismatch; missing={missing}, unknown={unknown}"
                )
            for name, dataset in dataset_by_name.items():
                contract = contracts[name]
                validator = Draft202012Validator(contract.row_schema)
                for index, record in enumerate(dataset.records):
                    errors = list(validator.iter_errors(record))
                    if errors:
                        raise TestInputValidationError(
                            f"dataset {name} row {index}: {errors[0].message}"
                        )
                if contract.evidence_required and not dataset.evidence_ids:
                    raise TestInputValidationError(f"dataset {name} requires evidence references")
                if not dataset.authoritative and contract.role == "population":
                    raise TestInputValidationError("population dataset must be authoritative")
            Draft202012Validator(release.input_schema).validate(
                {
                    "parameters": request.parameters,
                    "datasets": {item.name: item.records for item in request.datasets},
                }
            )
        except TestInputValidationError:
            raise
        except Exception as exc:
            raise TestInputValidationError(str(exc)) from exc

    def _prepare_datasets(
        self, release: LoadedControlTest, request: ControlTestRunRequest
    ) -> tuple[dict[str, list[dict[str, Any]]], dict[str, Any], dict[str, Any]]:
        contracts = {item.name: item for item in release.manifest.datasets}
        provided = {item.name: item for item in request.datasets}
        population_contract = next(item for item in release.manifest.datasets if item.role == "population")
        population = provided[population_contract.name]
        limitations: list[str] = []
        expected = population.expected_count
        parameter_name = release.manifest.reconciliation.expected_count_parameter
        if parameter_name and parameter_name in request.parameters:
            expected = int(request.parameters[parameter_name])
        complete = expected is None or expected == len(population.records)
        if not complete:
            limitations.append(
                f"population count {len(population.records)} does not match expected count {expected}"
            )
        if release.manifest.reconciliation.reject_duplicate_primary_keys:
            duplicates = self._duplicate_keys(population.records, population_contract)
            if duplicates:
                complete = False
                limitations.append(f"duplicate population keys: {duplicates[:10]}")
        sampled_population = self._sample(release, population.records, request.parameters)
        prepared: dict[str, list[dict[str, Any]]] = {
            item.name: (sampled_population if item.name == population_contract.name else item.records)
            for item in request.datasets
        }
        dataset_manifest = []
        for name in sorted(provided):
            dataset = provided[name]
            contract = contracts[name]
            records = prepared[name]
            dataset_manifest.append(
                {
                    "name": name,
                    "role": contract.role,
                    "row_count": len(dataset.records),
                    "sampled_row_count": len(records),
                    "content_hash": digest(dataset.records),
                    "schema_hash": digest(contract.row_schema),
                    "evidence_ids": sorted(dataset.evidence_ids),
                    "authoritative": dataset.authoritative,
                    "expected_count": dataset.expected_count,
                    "metadata": dataset.metadata,
                }
            )
        input_manifest = {
            "schema": "assurance.control_test_input_manifest.v1",
            "test_id": request.test_id,
            "version": request.version,
            "period_start": request.period_start.isoformat(),
            "period_end": request.period_end.isoformat(),
            "parameters": request.parameters,
            "datasets": dataset_manifest,
        }
        reconciliation = {
            "population_count": len(population.records),
            "sampled_count": len(sampled_population),
            "reconciled_count": len(population.records) if complete else min(len(population.records), expected or len(population.records)),
            "population_complete": complete,
            "limitations": limitations,
        }
        return prepared, input_manifest, reconciliation

    @staticmethod
    def _duplicate_keys(records: list[dict[str, Any]], contract: DatasetContract) -> list[str]:
        if not contract.primary_key:
            return []
        seen: set[tuple[Any, ...]] = set()
        duplicates: list[str] = []
        for record in records:
            key = tuple(record.get(field) for field in contract.primary_key)
            if key in seen:
                duplicates.append("|".join(str(value) for value in key))
            seen.add(key)
        return duplicates

    @staticmethod
    def _sample(
        release: LoadedControlTest,
        records: list[dict[str, Any]],
        parameters: dict[str, Any],
    ) -> list[dict[str, Any]]:
        policy = release.manifest.sampling
        if policy.method == "full_population":
            return list(records)
        size = policy.default_size or len(records)
        if policy.size_parameter and policy.size_parameter in parameters:
            size = int(parameters[policy.size_parameter])
        seed = policy.default_seed
        if policy.seed_parameter and policy.seed_parameter in parameters:
            seed = str(parameters[policy.seed_parameter])
        ranked = sorted(records, key=lambda record: hashlib.sha256(seed.encode() + canonical_bytes(record)).digest())
        return ranked[: min(size, len(ranked))]

    def _finalize(
        self,
        tenant_id: str,
        run_id: str,
        release: LoadedControlTest,
        request: ControlTestRunRequest,
        result_value: dict[str, Any],
        *,
        environment: dict[str, Any],
        status: str,
        input_hash: str,
        reconciliation: dict[str, Any],
    ) -> ControlTestRunResult:
        exceptions = [TestExceptionResult.model_validate(item) for item in result_value.get("exceptions", [])]
        conclusion = TestConclusion(result_value["conclusion"])
        execution_manifest = {
            "schema": "assurance.control_test_execution_manifest.v1",
            "test_id": release.manifest.test_id,
            "version": release.manifest.version,
            "package_hash": release.release_document["package_sha256"],
            "manifest_hash": release.manifest_hash,
            "code_hash": release.code_hash,
            "input_manifest_hash": input_hash,
            "parameters": request.parameters,
            "sampling": release.manifest.sampling.model_dump(mode="json"),
            "resources": release.manifest.resources.model_dump(mode="json"),
            "environment": environment,
        }
        execution_hash = digest(execution_manifest)
        result_hash = self._result_hash(result_value, reconciliation)
        with self.database.transaction() as session:
            repository = ControlTestRepository(session)
            row = repository.get_run(tenant_id, run_id)
            if row is None:
                raise TestRunNotFoundError(run_id)
            row.status = status
            row.completed_at = datetime.now(timezone.utc)
            row.exception_count = len(exceptions)
            row.conclusion = conclusion.value
            row.execution_manifest_hash = execution_hash
            row.result_manifest_hash = result_hash
            row.result_json = result_value
            row.execution_environment_json = environment
            for exception in exceptions:
                repository.add_exception(
                    ControlTestException(
                        exception_id=new_id("tex"),
                        tenant_id=tenant_id,
                        run_id=run_id,
                        exception_key=exception.exception_key,
                        subject_ref=exception.subject_ref,
                        classification=exception.classification,
                        severity=exception.severity,
                        status=exception.status,
                        reason=exception.reason,
                        attributes_json=exception.attributes,
                        evidence_ids_json=exception.evidence_ids,
                    )
                )
            payload = {
                "run_id": run_id,
                "test_id": row.test_id,
                "version": row.test_version,
                "status": status,
                "conclusion": conclusion.value,
                "population_count": row.population_count,
                "exception_count": len(exceptions),
                "result_manifest_hash": result_hash,
            }
            AuditEventRepository(session).append(
                AuditEvent(
                    event_type="control_test.run_completed",
                    tenant_id=tenant_id,
                    engagement_id=row.engagement_id,
                    task_id=row.task_id,
                    payload=payload,
                )
            )
            OutboxRepository(session).add(
                tenant_id=tenant_id,
                aggregate_type="control_test_run",
                aggregate_id=run_id,
                event_type="control_test.run_completed",
                payload=payload,
                idempotency_key=f"control-test-completed:{run_id}",
            )
            return self._result_from_row(repository, row)

    @staticmethod
    def _result_hash(value: dict[str, Any], reconciliation: dict[str, Any]) -> str:
        return digest(
            {
                "schema": "assurance.control_test_result_manifest.v1",
                "output": value,
                "population_count": reconciliation["population_count"],
                "sampled_count": reconciliation["sampled_count"],
                "reconciled_count": reconciliation["reconciled_count"],
                "population_complete": reconciliation["population_complete"],
            }
        )

    @staticmethod
    def _emit_started(session: Any, run: ControlTestRun) -> None:
        payload = {
            "run_id": run.run_id,
            "test_id": run.test_id,
            "version": run.test_version,
            "input_manifest_hash": run.input_manifest_hash,
        }
        AuditEventRepository(session).append(
            AuditEvent(
                event_type="control_test.run_started",
                tenant_id=run.tenant_id,
                engagement_id=run.engagement_id,
                task_id=run.task_id,
                payload=payload,
            )
        )
        OutboxRepository(session).add(
            tenant_id=run.tenant_id,
            aggregate_type="control_test_run",
            aggregate_id=run.run_id,
            event_type="control_test.run_started",
            payload=payload,
            idempotency_key=f"control-test-started:{run.run_id}",
        )

    @staticmethod
    def _release_view(row: Any) -> dict[str, Any]:
        return {
            "release_id": row.release_id,
            "test_id": row.test_id,
            "version": row.version,
            "domain": row.domain,
            "title": row.title,
            "description": row.description,
            "engine": row.engine,
            "release_status": row.release_status,
            "package_hash": row.package_hash,
            "code_hash": row.code_hash,
            "released_at": row.released_at.isoformat() if row.released_at else None,
            "dataset_contracts": row.dataset_contracts_json,
            "reconciliation": row.reconciliation_policy_json,
            "sampling": row.sampling_policy_json,
            "resource_limits": row.resource_limits_json,
            "known_limitations": row.metadata_json.get("known_limitations", []),
        }

    @staticmethod
    def _result_from_row(
        repository: ControlTestRepository, row: ControlTestRun
    ) -> ControlTestRunResult:
        exceptions = [
            TestExceptionResult(
                exception_key=item.exception_key,
                subject_ref=item.subject_ref,
                classification=item.classification,
                severity=item.severity,
                status=item.status,
                reason=item.reason,
                attributes=item.attributes_json,
                evidence_ids=item.evidence_ids_json,
            )
            for item in repository.exceptions(row.tenant_id, row.run_id)
        ]
        result = row.result_json or {}
        return ControlTestRunResult(
            run_id=row.run_id,
            test_id=row.test_id,
            version=row.test_version,
            status=row.status if row.status in {"succeeded", "blocked", "failed"} else "failed",
            conclusion=TestConclusion(row.conclusion or TestConclusion.NOT_TESTED.value),
            population_count=row.population_count or 0,
            sampled_count=row.sampled_count or 0,
            reconciled_count=row.reconciled_count or 0,
            population_complete=bool(row.population_complete),
            exception_count=row.exception_count or 0,
            exceptions=exceptions,
            rows=result.get("rows", []),
            input_manifest_hash=row.input_manifest_hash or "",
            execution_manifest_hash=row.execution_manifest_hash or "",
            result_manifest_hash=row.result_manifest_hash or "",
            limitations=result.get("limitations", []),
        )
