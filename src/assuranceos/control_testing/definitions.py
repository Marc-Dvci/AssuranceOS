from __future__ import annotations

from datetime import date
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator


class TestConclusion(StrEnum):
    EFFECTIVE = "effective"
    PARTIALLY_EFFECTIVE = "partially_effective"
    INEFFECTIVE = "ineffective"
    NOT_APPLICABLE = "not_applicable"
    NOT_TESTED = "not_tested"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"
    POPULATION_INCOMPLETE = "population_incomplete"
    SOURCE_UNRELIABLE = "source_unreliable"
    TEST_FAILED_TECHNICALLY = "test_failed_technically"
    SCOPE_LIMITATION = "scope_limitation"


class DatasetContract(BaseModel):
    name: str = Field(pattern=r"^[a-z][a-z0-9_]{1,63}$")
    role: Literal["population", "reference", "exceptions"]
    row_schema: dict[str, Any]
    primary_key: list[str] = Field(default_factory=list)
    required: bool = True
    evidence_required: bool = True


class ReconciliationPolicy(BaseModel):
    require_complete: bool = True
    expected_count_parameter: str | None = None
    reject_duplicate_primary_keys: bool = True


class SamplingPolicy(BaseModel):
    method: Literal["full_population", "deterministic_hash"] = "full_population"
    size_parameter: str | None = None
    default_size: int | None = Field(default=None, ge=1)
    seed_parameter: str | None = None
    default_seed: str = "assuranceos"


class ResourceLimits(BaseModel):
    timeout_seconds: int = Field(default=60, ge=1, le=900)
    memory_mb: int = Field(default=512, ge=64, le=8192)
    cpu_seconds: int = Field(default=30, ge=1, le=900)
    max_output_bytes: int = Field(default=5_000_000, ge=1024, le=100_000_000)


class ControlTestManifest(BaseModel):
    schema_version: Literal["assurance.control_test_manifest.v1"]
    test_id: str = Field(pattern=r"^[A-Z][A-Z0-9_-]{2,63}$")
    version: str = Field(pattern=r"^\d+\.\d+\.\d+$")
    domain: str = Field(pattern=r"^[a-z][a-z0-9_-]{1,63}$")
    title: str = Field(min_length=3, max_length=255)
    description: str = Field(min_length=10, max_length=4000)
    engine: Literal["python", "sql"]
    entrypoint: str = Field(min_length=3, max_length=255)
    input_schema: str = "input.schema.json"
    output_schema: str = "output.schema.json"
    parameter_schema: str = "parameters.schema.json"
    datasets: list[DatasetContract]
    reconciliation: ReconciliationPolicy = Field(default_factory=ReconciliationPolicy)
    sampling: SamplingPolicy = Field(default_factory=SamplingPolicy)
    resources: ResourceLimits = Field(default_factory=ResourceLimits)
    allowed_libraries: list[str] = Field(default_factory=list)
    release_status: Literal["released"] = "released"
    known_limitations: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def exactly_one_population(self) -> "ControlTestManifest":
        if sum(item.role == "population" for item in self.datasets) != 1:
            raise ValueError("a control test must declare exactly one population dataset")
        names = [item.name for item in self.datasets]
        if len(names) != len(set(names)):
            raise ValueError("dataset names must be unique")
        return self


class ControlTestDataset(BaseModel):
    name: str
    records: list[dict[str, Any]]
    evidence_ids: list[str] = Field(default_factory=list)
    authoritative: bool = True
    expected_count: int | None = Field(default=None, ge=0)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ControlTestRunRequest(BaseModel):
    test_id: str
    version: str
    purpose: str = Field(min_length=3, max_length=4000)
    period_start: date
    period_end: date
    requested_by: str = Field(min_length=1, max_length=128)
    idempotency_key: str = Field(min_length=3, max_length=255)
    engagement_id: str | None = None
    task_id: str | None = None
    parameters: dict[str, Any] = Field(default_factory=dict)
    datasets: list[ControlTestDataset]

    @model_validator(mode="after")
    def validate_period_and_names(self) -> "ControlTestRunRequest":
        if self.period_end < self.period_start:
            raise ValueError("period_end must not precede period_start")
        names = [item.name for item in self.datasets]
        if len(names) != len(set(names)):
            raise ValueError("dataset names must be unique")
        return self


class TestExceptionResult(BaseModel):
    exception_key: str
    subject_ref: str
    classification: str
    severity: str = "medium"
    status: Literal["open", "approved_exception", "false_positive", "resolved"] = "open"
    reason: str
    attributes: dict[str, Any] = Field(default_factory=dict)
    evidence_ids: list[str] = Field(default_factory=list)


class ControlTestRunResult(BaseModel):
    run_id: str
    test_id: str
    version: str
    status: Literal["succeeded", "blocked", "failed"]
    conclusion: TestConclusion
    population_count: int
    sampled_count: int
    reconciled_count: int
    population_complete: bool
    exception_count: int
    exceptions: list[TestExceptionResult] = Field(default_factory=list)
    rows: list[dict[str, Any]] = Field(default_factory=list)
    input_manifest_hash: str
    execution_manifest_hash: str
    result_manifest_hash: str
    reproducible: bool | None = None
    limitations: list[str] = Field(default_factory=list)
