from __future__ import annotations

from datetime import date, datetime, timedelta
from pathlib import Path
import os
import tempfile

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from pydantic import BaseModel, Field
from starlette.background import BackgroundTask
from starlette.middleware.trustedhost import TrustedHostMiddleware
from uuid import uuid4

from .config import settings
from .control_testing import (
    ControlTestDataset,
    ControlTestRegistry,
    ControlTestRunRequest,
    ControlTestService,
)
from .control_testing.exceptions import (
    ControlTestError,
    ReproducibilityMismatchError,
    TestInputValidationError,
    TestReleaseNotFoundError,
    TestRunNotFoundError,
)
from .connectors import (
    CollectionGrantInput,
    ConnectorInstanceInput,
    ConnectorService,
)
from .connectors.exceptions import (
    CollectionGrantError,
    CollectionGrantExpiredError,
    CollectionScopeError,
    ConnectorNotFoundError,
    ConnectorRunConflictError,
)
from .db.session import Database
from .execution_authority import ExecutionAuthority
from .execution_security import Ed25519ExecutionEnvelopeSigner
from .demo import ENGAGEMENT_ID, TENANT_ID, run_golden_engagement
from .ledger import AuditLedger
from .orchestration import (
    FailureClass,
    GateDecision,
    Orchestrator,
    TaskExecutionResult,
    WorkflowDefinition,
)
from .orchestration.demo import run_orchestrator_demo
from .orchestration.exceptions import (
    EngagementNotFoundError,
    InvalidStateTransitionError,
    LeaseConflictError,
    TaskNotFoundError,
    WorkflowAlreadyCompiledError,
    WorkflowValidationError,
)
from .registry import AgentRegistry
from .scheduling import (
    AuditScheduler,
    OccurrenceDecision,
    PreflightContext,
    ScheduleAuthoringService,
    ScheduleDecision,
    ScheduleDraftInput,
)
from .scheduling.exceptions import (
    OccurrenceNotFoundError,
    OccurrenceStateError,
    ScheduleConfigurationError,
    ScheduleNotFoundError,
)
from .security import JwtVerifier, Permission, Principal, effective_actor, require_permission
from .vault import BaselineContentInspector, Ed25519ManifestSigner, EvidenceVault, GoogleCloudStorageObjectStore
from .vault.exceptions import (
    AcquisitionConflictError,
    EvidenceDeletedError,
    EvidenceNotFoundError,
    ImmutableObjectConflictError,
    ObjectIntegrityError,
    ObjectNotFoundError,
    RetentionPolicyError,
)
from .vault.inspection import ContentInspectionRejected

app = FastAPI(title="AssuranceOS API", version="0.8.0")
app.state.settings = settings
if settings.auth_mode == "jwt":
    app.state.jwt_verifier = JwtVerifier(
        issuer=settings.auth_jwt_issuer or "",
        audience=settings.auth_jwt_audience or "",
        algorithms=settings.auth_jwt_algorithms,
        secret=settings.auth_jwt_secret,
        jwks_url=settings.auth_jwks_url,
        leeway_seconds=settings.auth_clock_leeway_seconds,
    )
else:
    app.state.jwt_verifier = None
if settings.trusted_hosts:
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=list(settings.trusted_hosts))

database = Database(settings.database_url)
ledger = AuditLedger(database)
orchestrator = Orchestrator(database)
scheduler = AuditScheduler(database, orchestrator=orchestrator)
schedule_authoring = ScheduleAuthoringService(database)
_execution_signer = (
    Ed25519ExecutionEnvelopeSigner.from_pem(
        settings.execution_signing_private_key,
        key_id=settings.execution_signing_key_id,
    )
    if settings.execution_signing_private_key
    else None
)
execution_authority = (
    ExecutionAuthority(
        AgentRegistry(settings.agent_root).load(),
        _execution_signer,
        default_ttl=timedelta(seconds=settings.execution_envelope_ttl_seconds),
    )
    if _execution_signer is not None
    else None
)
_export_signer = (
    Ed25519ManifestSigner.from_pem(
        settings.export_signing_private_key, key_id=settings.export_signing_key_id
    )
    if settings.export_signing_private_key
    else None
)
if settings.is_production and _export_signer is None:
    raise RuntimeError("production API requires an Ed25519 export-signing private key")
if settings.is_production and execution_authority is None:
    raise RuntimeError("production API requires an Ed25519 execution-envelope signing key")
_object_store = (
    GoogleCloudStorageObjectStore(settings.evidence_bucket or "")
    if settings.evidence_storage == "gcs"
    else None
)
vault = (
    EvidenceVault(
        database,
        _object_store,
        export_signer=_export_signer,
        inspector=BaselineContentInspector(),
    )
    if _object_store is not None
    else EvidenceVault.local(
        database,
        settings.evidence_root,
        export_signer=_export_signer,
        inspector=BaselineContentInspector(),
    )
)
connector_service = ConnectorService(database, vault)

control_test_registry = ControlTestRegistry(
    settings.control_test_root,
    trusted_public_key=settings.control_test_public_key.read_bytes(),
).load()


def _control_test_service() -> ControlTestService:
    return ControlTestService(
        database,
        control_test_registry,
        require_canonical_evidence=settings.is_production,
    )


@app.middleware("http")
async def request_context(request: Request, call_next):
    request_id = request.headers.get("x-request-id") or f"req_{uuid4().hex[:20]}"
    request.state.request_id = request_id
    try:
        response = await call_next(request)
    except Exception:
        raise
    response.headers["x-request-id"] = request_id
    response.headers["x-content-type-options"] = "nosniff"
    response.headers["cache-control"] = "no-store"
    return response


class CancellationRequest(BaseModel):
    actor_id: str = Field(min_length=1, max_length=128)
    reason: str = Field(min_length=1, max_length=4000)


class ScheduleSimulationRequest(BaseModel):
    window_start: datetime
    window_end: datetime
    limit: int = Field(default=500, ge=1, le=1000)


class ScheduleEvaluationRequest(BaseModel):
    context: PreflightContext = Field(default_factory=PreflightContext)
    worker_id: str = Field(default="api-scheduler", min_length=1, max_length=128)
    lease_seconds: int = Field(default=60, ge=1, le=3600)


class EvidenceRetentionRequest(BaseModel):
    actor_id: str = Field(min_length=1, max_length=255)
    retention_until: date | None = None
    legal_hold: bool = False
    reason: str = Field(min_length=1, max_length=4000)


class EvidencePurgeRequest(BaseModel):
    actor_id: str = Field(min_length=1, max_length=255)
    reason: str = Field(min_length=1, max_length=4000)
    as_of: date | None = None


class GrantRevocationRequest(BaseModel):
    actor_id: str = Field(min_length=1, max_length=255)
    reason: str = Field(min_length=1, max_length=4000)


class EvidenceExportRequest(BaseModel):
    evidence_ids: list[str] = Field(min_length=1, max_length=500)
    actor_id: str = Field(min_length=1, max_length=255)
    purpose: str = Field(min_length=1, max_length=4000)
    include_ancestors: bool = True


class TaskClaimRequest(BaseModel):
    worker_id: str = Field(min_length=1, max_length=128)
    lease_seconds: int = Field(default=60, ge=1, le=3600)
    engagement_id: str | None = Field(default=None, max_length=64)
    task_types: list[str] = Field(default_factory=list, max_length=100)
    agent_roles: list[str] = Field(default_factory=list, max_length=100)


class TaskHeartbeatRequest(BaseModel):
    worker_id: str = Field(min_length=1, max_length=128)
    lease_seconds: int = Field(default=60, ge=1, le=3600)


class TaskCompletionRequest(BaseModel):
    worker_id: str = Field(min_length=1, max_length=128)
    result: TaskExecutionResult


class TaskFailureRequest(BaseModel):
    worker_id: str = Field(min_length=1, max_length=128)
    failure_class: FailureClass
    message: str = Field(min_length=1, max_length=8000)
    force_retryable: bool | None = None


class ControlTestExecutionRequest(BaseModel):
    test_id: str
    version: str
    purpose: str = Field(min_length=3, max_length=4000)
    period_start: date
    period_end: date
    idempotency_key: str = Field(min_length=3, max_length=255)
    engagement_id: str | None = None
    task_id: str | None = None
    parameters: dict = Field(default_factory=dict)
    datasets: list[ControlTestDataset]


class ControlTestReproductionRequest(BaseModel):
    purpose: str = Field(min_length=3, max_length=4000)
    period_start: date
    period_end: date
    parameters: dict = Field(default_factory=dict)
    datasets: list[ControlTestDataset]


async def _bounded_request_body(request: Request) -> bytes:
    payload = bytearray()
    async for chunk in request.stream():
        payload.extend(chunk)
        if len(payload) > settings.max_evidence_upload_bytes:
            raise HTTPException(
                status_code=413,
                detail=f"evidence payload exceeds {settings.max_evidence_upload_bytes} bytes",
            )
    if not payload:
        raise HTTPException(status_code=422, detail="evidence payload is empty")
    return bytes(payload)


def _raise_http(exc: Exception) -> None:
    if isinstance(
        exc,
        (
            EngagementNotFoundError,
            TaskNotFoundError,
            ScheduleNotFoundError,
            OccurrenceNotFoundError,
            EvidenceNotFoundError,
            ObjectNotFoundError,
            ConnectorNotFoundError,
        ),
    ):
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if isinstance(
        exc,
        (
            InvalidStateTransitionError,
            LeaseConflictError,
            WorkflowAlreadyCompiledError,
            OccurrenceStateError,
            AcquisitionConflictError,
            ImmutableObjectConflictError,
            ObjectIntegrityError,
            RetentionPolicyError,
            CollectionGrantError,
            CollectionGrantExpiredError,
            CollectionScopeError,
            ConnectorRunConflictError,
            ContentInspectionRejected,
        ),
    ):
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if isinstance(exc, EvidenceDeletedError):
        raise HTTPException(status_code=410, detail=str(exc)) from exc
    if isinstance(exc, (WorkflowValidationError, ScheduleConfigurationError, ValueError)):
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if isinstance(exc, (TestReleaseNotFoundError, TestRunNotFoundError)):
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if isinstance(exc, (TestInputValidationError, ReproducibilityMismatchError)):
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if isinstance(exc, ControlTestError):
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    raise exc



def _actor(request: Request, requested: str | None = None) -> str:
    principal = getattr(request.state, "principal", Principal.local_system())
    return effective_actor(principal, requested)


def _registry():
    return AgentRegistry(settings.agent_root).load()


@app.get("/health")
def health() -> dict:
    return {
        "status": "ok",
        "environment": settings.environment,
        "model_mode": settings.model_mode,
        "agent_packages": len(_registry()),
        "control_test_packages": len(control_test_registry.list()),
        "database_dialect": database.engine.dialect.name,
    }


@app.get("/ready")
def readiness() -> JSONResponse:
    checks = {
        "database": database.ping(),
        "agent_registry": False,
        "control_test_registry": False,
        "control_test_registry_database": False,
    }
    try:
        checks["agent_registry"] = len(_registry()) == 19
        checks["control_test_registry"] = len(control_test_registry.list()) >= 2
        checks["control_test_registry_database"] = (
            len(_control_test_service().list_releases()) == len(control_test_registry.list())
        )
    except Exception:
        checks["agent_registry"] = False
    ready = all(checks.values())
    return JSONResponse(
        status_code=200 if ready else 503,
        content={"status": "ready" if ready else "not_ready", "checks": checks},
    )


@app.get("/api/v1/agents", dependencies=[Depends(require_permission(Permission.AGENTS_READ))])
def list_agents() -> list[dict]:
    return [
        {
            "agent_id": package.agent_id,
            "version": package.manifest["version"],
            "display_name": package.manifest["display_name"],
            "mandate": package.manifest["mandate"],
            "human_gates": package.manifest.get("human_gates", []),
            "tool_count": len(package.tools.get("tools", [])),
        }
        for package in _registry().values()
    ]


@app.get("/api/v1/agents/{agent_id}", dependencies=[Depends(require_permission(Permission.AGENTS_READ))])
def get_agent(agent_id: str) -> dict:
    package = _registry().get(agent_id)
    if not package:
        raise HTTPException(status_code=404, detail="agent not found")
    return {
        "manifest": package.manifest,
        "tools": package.tools,
        "policy": package.policy,
        "model_profiles": package.model_profiles,
        "evaluations": package.evaluations,
    }


@app.post("/api/v1/demo/reset", dependencies=[Depends(require_permission(Permission.DEMO_OPERATE))])
def reset_demo() -> dict:
    existing_events = len(ledger.list_events(TENANT_ID))
    deleted = ledger.reset_tenant(TENANT_ID)
    return {
        "tenant_id": TENANT_ID,
        "tenant_deleted": deleted,
        "events_deleted": existing_events if deleted else 0,
    }


@app.post("/api/v1/demo/run", dependencies=[Depends(require_permission(Permission.DEMO_OPERATE))])
def run_demo() -> dict:
    return run_golden_engagement(settings.demo_root, ledger)


@app.get("/api/v1/demo/events", dependencies=[Depends(require_permission(Permission.DEMO_OPERATE))])
def demo_events() -> list[dict]:
    return ledger.list_events(TENANT_ID, ENGAGEMENT_ID)


@app.post("/api/v1/tenants/{tenant_id}/engagements/{engagement_id}/workflow", dependencies=[Depends(require_permission(Permission.ENGAGEMENT_WRITE))])
def compile_engagement_workflow(
    tenant_id: str, engagement_id: str, workflow: WorkflowDefinition
) -> dict:
    try:
        return orchestrator.compile_workflow(
            tenant_id=tenant_id, engagement_id=engagement_id, workflow=workflow
        ).model_dump(mode="json")
    except Exception as exc:  # translated to stable API errors below
        _raise_http(exc)
        raise AssertionError("unreachable")


@app.post("/api/v1/tenants/{tenant_id}/engagements/{engagement_id}/start", dependencies=[Depends(require_permission(Permission.ENGAGEMENT_WRITE))])
def start_engagement_workflow(tenant_id: str, engagement_id: str) -> dict:
    try:
        return orchestrator.start_engagement(
            tenant_id=tenant_id, engagement_id=engagement_id
        ).model_dump(mode="json")
    except Exception as exc:
        _raise_http(exc)
        raise AssertionError("unreachable")


@app.get("/api/v1/tenants/{tenant_id}/engagements/{engagement_id}/orchestration", dependencies=[Depends(require_permission(Permission.ENGAGEMENT_READ))])
def get_engagement_orchestration(tenant_id: str, engagement_id: str) -> dict:
    try:
        return orchestrator.snapshot(
            tenant_id=tenant_id, engagement_id=engagement_id
        ).model_dump(mode="json")
    except Exception as exc:
        _raise_http(exc)
        raise AssertionError("unreachable")


@app.post(
    "/api/v1/tenants/{tenant_id}/tasks/claim",
    dependencies=[Depends(require_permission(Permission.TASK_EXECUTE))],
    response_model=None,
)
def claim_task(tenant_id: str, payload: TaskClaimRequest, http_request: Request) -> Response | dict:
    worker_id = _actor(http_request, payload.worker_id)
    try:
        lease = orchestrator.claim_next(
            tenant_id=tenant_id,
            worker_id=worker_id,
            lease_seconds=payload.lease_seconds,
            engagement_id=payload.engagement_id,
            task_types=payload.task_types or None,
            agent_roles=payload.agent_roles or None,
        )
        if lease is None:
            return Response(status_code=204)
        signed = None
        if lease.assigned_agent_role is not None:
            if execution_authority is None:
                raise HTTPException(
                    status_code=503,
                    detail="execution-envelope signer is not configured",
                )
            signed = execution_authority.issue(lease)
        return {
            "lease": lease.model_dump(mode="json"),
            "signed_execution_envelope": (
                signed.model_dump(mode="json", by_alias=True) if signed is not None else None
            ),
        }
    except HTTPException:
        raise
    except Exception as exc:
        _raise_http(exc)
        raise AssertionError("unreachable")


@app.post(
    "/api/v1/tenants/{tenant_id}/tasks/{task_id}/heartbeat",
    dependencies=[Depends(require_permission(Permission.TASK_EXECUTE))],
)
def heartbeat_task(
    tenant_id: str,
    task_id: str,
    payload: TaskHeartbeatRequest,
    http_request: Request,
) -> dict:
    worker_id = _actor(http_request, payload.worker_id)
    try:
        lease = orchestrator.heartbeat(
            tenant_id=tenant_id,
            task_id=task_id,
            worker_id=worker_id,
            lease_seconds=payload.lease_seconds,
        )
        signed = None
        if lease.assigned_agent_role is not None:
            if execution_authority is None:
                raise HTTPException(
                    status_code=503,
                    detail="execution-envelope signer is not configured",
                )
            signed = execution_authority.issue(lease)
        return {
            "lease": lease.model_dump(mode="json"),
            "signed_execution_envelope": (
                signed.model_dump(mode="json", by_alias=True) if signed is not None else None
            ),
        }
    except HTTPException:
        raise
    except Exception as exc:
        _raise_http(exc)
        raise AssertionError("unreachable")


@app.post(
    "/api/v1/tenants/{tenant_id}/tasks/{task_id}/complete",
    dependencies=[Depends(require_permission(Permission.TASK_EXECUTE))],
)
def complete_task(
    tenant_id: str,
    task_id: str,
    payload: TaskCompletionRequest,
    http_request: Request,
) -> dict:
    try:
        return orchestrator.complete_task(
            tenant_id=tenant_id,
            task_id=task_id,
            worker_id=_actor(http_request, payload.worker_id),
            result=payload.result,
        ).model_dump(mode="json")
    except Exception as exc:
        _raise_http(exc)
        raise AssertionError("unreachable")


@app.post(
    "/api/v1/tenants/{tenant_id}/tasks/{task_id}/fail",
    dependencies=[Depends(require_permission(Permission.TASK_EXECUTE))],
)
def fail_task(
    tenant_id: str,
    task_id: str,
    payload: TaskFailureRequest,
    http_request: Request,
) -> dict:
    try:
        return orchestrator.fail_task(
            tenant_id=tenant_id,
            task_id=task_id,
            worker_id=_actor(http_request, payload.worker_id),
            failure_class=payload.failure_class,
            message=payload.message,
            force_retryable=payload.force_retryable,
        ).model_dump(mode="json")
    except Exception as exc:
        _raise_http(exc)
        raise AssertionError("unreachable")


@app.post("/api/v1/tenants/{tenant_id}/tasks/{task_id}/gate/approve", dependencies=[Depends(require_permission(Permission.ENGAGEMENT_APPROVE))])
def approve_task_gate(tenant_id: str, task_id: str, decision: GateDecision, http_request: Request) -> dict:
    try:
        return orchestrator.approve_gate(
            tenant_id=tenant_id,
            task_id=task_id,
            decision=decision.model_copy(update={"actor_id": _actor(http_request, decision.actor_id)}),
        ).model_dump(mode="json")
    except Exception as exc:
        _raise_http(exc)
        raise AssertionError("unreachable")


@app.post("/api/v1/tenants/{tenant_id}/tasks/{task_id}/gate/reject", dependencies=[Depends(require_permission(Permission.ENGAGEMENT_APPROVE))])
def reject_task_gate(tenant_id: str, task_id: str, decision: GateDecision, http_request: Request) -> dict:
    try:
        return orchestrator.reject_gate(
            tenant_id=tenant_id,
            task_id=task_id,
            decision=decision.model_copy(update={"actor_id": _actor(http_request, decision.actor_id)}),
        ).model_dump(mode="json")
    except Exception as exc:
        _raise_http(exc)
        raise AssertionError("unreachable")


@app.post("/api/v1/tenants/{tenant_id}/engagements/{engagement_id}/cancel", dependencies=[Depends(require_permission(Permission.ENGAGEMENT_APPROVE))])
def cancel_engagement_workflow(
    tenant_id: str,
    engagement_id: str,
    payload: CancellationRequest,
    http_request: Request,
) -> dict:
    try:
        return orchestrator.cancel_engagement(
            tenant_id=tenant_id,
            engagement_id=engagement_id,
            actor_id=_actor(http_request, payload.actor_id),
            reason=payload.reason,
        ).model_dump(mode="json")
    except Exception as exc:
        _raise_http(exc)
        raise AssertionError("unreachable")


@app.post("/api/v1/demo/orchestration/run", dependencies=[Depends(require_permission(Permission.DEMO_OPERATE))])
def run_orchestration_demo() -> dict:
    root = Path(__file__).resolve().parents[2]
    return run_orchestrator_demo(
        database=database,
        demo_root=settings.demo_root,
        workflow_path=root / "examples/workflows/software-change-management.json",
    )


@app.get(
    "/api/v1/tenants/{tenant_id}/engagements/{engagement_id}/attempts",
    dependencies=[Depends(require_permission(Permission.ENGAGEMENT_READ))],
)
def list_engagement_attempts(tenant_id: str, engagement_id: str) -> list[dict]:
    try:
        return [
            item.model_dump(mode="json")
            for item in orchestrator.list_attempts(
                tenant_id=tenant_id, engagement_id=engagement_id
            )
        ]
    except Exception as exc:
        _raise_http(exc)
        raise AssertionError("unreachable")


@app.get(
    "/api/v1/tenants/{tenant_id}/tasks/{task_id}/attempts",
    dependencies=[Depends(require_permission(Permission.ENGAGEMENT_READ))],
)
def list_task_attempts(tenant_id: str, task_id: str) -> list[dict]:
    try:
        return [
            item.model_dump(mode="json")
            for item in orchestrator.list_attempts(tenant_id=tenant_id, task_id=task_id)
        ]
    except Exception as exc:
        _raise_http(exc)
        raise AssertionError("unreachable")


@app.post(
    "/api/v1/tenants/{tenant_id}/tasks/{task_id}/administrative-retry",
    dependencies=[Depends(require_permission(Permission.ENGAGEMENT_APPROVE))],
)
def administrative_retry_task(
    tenant_id: str, task_id: str, payload: CancellationRequest, http_request: Request
) -> dict:
    try:
        return orchestrator.force_retry_task(
            tenant_id=tenant_id,
            task_id=task_id,
            actor_id=_actor(http_request, payload.actor_id),
            reason=payload.reason,
        ).model_dump(mode="json")
    except Exception as exc:
        _raise_http(exc)
        raise AssertionError("unreachable")


@app.post(
    "/api/v1/tenants/{tenant_id}/tasks/{task_id}/administrative-skip",
    dependencies=[Depends(require_permission(Permission.ENGAGEMENT_APPROVE))],
)
def administrative_skip_task(
    tenant_id: str, task_id: str, payload: CancellationRequest, http_request: Request
) -> dict:
    try:
        return orchestrator.force_skip_task(
            tenant_id=tenant_id,
            task_id=task_id,
            actor_id=_actor(http_request, payload.actor_id),
            reason=payload.reason,
        ).model_dump(mode="json")
    except Exception as exc:
        _raise_http(exc)
        raise AssertionError("unreachable")


@app.post(
    "/api/v1/tenants/{tenant_id}/schedules",
    dependencies=[Depends(require_permission(Permission.SCHEDULE_WRITE))],
)
def create_schedule_draft(tenant_id: str, payload: ScheduleDraftInput) -> dict:
    try:
        return schedule_authoring.create_draft(tenant_id=tenant_id, draft=payload).model_dump(
            mode="json"
        )
    except Exception as exc:
        _raise_http(exc)
        raise AssertionError("unreachable")


@app.get(
    "/api/v1/tenants/{tenant_id}/schedules",
    dependencies=[Depends(require_permission(Permission.SCHEDULE_READ))],
)
def list_schedules(tenant_id: str, plan_id: str | None = None) -> list[dict]:
    return [
        item.model_dump(mode="json")
        for item in schedule_authoring.list(tenant_id=tenant_id, plan_id=plan_id)
    ]


@app.get(
    "/api/v1/tenants/{tenant_id}/schedules/{schedule_id}",
    dependencies=[Depends(require_permission(Permission.SCHEDULE_READ))],
)
def get_schedule(tenant_id: str, schedule_id: str) -> dict:
    try:
        return schedule_authoring.get(tenant_id=tenant_id, schedule_id=schedule_id).model_dump(
            mode="json"
        )
    except Exception as exc:
        _raise_http(exc)
        raise AssertionError("unreachable")


@app.post(
    "/api/v1/tenants/{tenant_id}/schedules/{schedule_id}/revisions",
    dependencies=[Depends(require_permission(Permission.SCHEDULE_WRITE))],
)
def revise_schedule(tenant_id: str, schedule_id: str, payload: ScheduleDraftInput) -> dict:
    try:
        return schedule_authoring.revise(
            tenant_id=tenant_id, schedule_id=schedule_id, draft=payload
        ).model_dump(mode="json")
    except Exception as exc:
        _raise_http(exc)
        raise AssertionError("unreachable")


@app.post(
    "/api/v1/tenants/{tenant_id}/schedules/{schedule_id}/approve",
    dependencies=[Depends(require_permission(Permission.SCHEDULE_APPROVE))],
)
def approve_schedule(
    tenant_id: str, schedule_id: str, payload: ScheduleDecision, http_request: Request
) -> dict:
    try:
        return schedule_authoring.approve(
            tenant_id=tenant_id,
            schedule_id=schedule_id,
            decision=payload.model_copy(
                update={"actor_id": _actor(http_request, payload.actor_id)}
            ),
        ).model_dump(mode="json")
    except Exception as exc:
        _raise_http(exc)
        raise AssertionError("unreachable")


@app.post(
    "/api/v1/tenants/{tenant_id}/schedules/{schedule_id}/disable",
    dependencies=[Depends(require_permission(Permission.SCHEDULE_APPROVE))],
)
def disable_schedule(
    tenant_id: str, schedule_id: str, payload: ScheduleDecision, http_request: Request
) -> dict:
    try:
        return schedule_authoring.disable(
            tenant_id=tenant_id,
            schedule_id=schedule_id,
            decision=payload.model_copy(
                update={"actor_id": _actor(http_request, payload.actor_id)}
            ),
        ).model_dump(mode="json")
    except Exception as exc:
        _raise_http(exc)
        raise AssertionError("unreachable")


@app.post("/api/v1/tenants/{tenant_id}/schedules/{schedule_id}/simulate", dependencies=[Depends(require_permission(Permission.SCHEDULE_READ))])
def simulate_schedule(
    tenant_id: str, schedule_id: str, request: ScheduleSimulationRequest
) -> list[dict]:
    try:
        return [
            item.model_dump(mode="json")
            for item in scheduler.simulate(
                tenant_id=tenant_id,
                schedule_id=schedule_id,
                window_start=request.window_start,
                window_end=request.window_end,
                limit=request.limit,
            )
        ]
    except Exception as exc:
        _raise_http(exc)
        raise AssertionError("unreachable")


@app.post("/api/v1/tenants/{tenant_id}/schedules/{schedule_id}/evaluate", dependencies=[Depends(require_permission(Permission.SCHEDULE_WRITE))])
def evaluate_schedule(
    tenant_id: str, schedule_id: str, request: ScheduleEvaluationRequest
) -> dict:
    try:
        return scheduler.evaluate_due(
            tenant_id=tenant_id,
            schedule_id=schedule_id,
            context=request.context,
            worker_id=request.worker_id,
            lease_seconds=request.lease_seconds,
        ).model_dump(mode="json")
    except Exception as exc:
        _raise_http(exc)
        raise AssertionError("unreachable")


@app.get("/api/v1/tenants/{tenant_id}/schedules/{schedule_id}/occurrences", dependencies=[Depends(require_permission(Permission.SCHEDULE_READ))])
def list_schedule_occurrences(tenant_id: str, schedule_id: str) -> list[dict]:
    try:
        return [
            occurrence.model_dump(mode="json")
            for occurrence in scheduler.list_occurrences(
                tenant_id=tenant_id, schedule_id=schedule_id
            )
        ]
    except Exception as exc:
        _raise_http(exc)
        raise AssertionError("unreachable")


@app.get("/api/v1/tenants/{tenant_id}/occurrences/{occurrence_id}", dependencies=[Depends(require_permission(Permission.SCHEDULE_READ))])
def get_schedule_occurrence(tenant_id: str, occurrence_id: str) -> dict:
    try:
        return scheduler.get_occurrence(
            tenant_id=tenant_id, occurrence_id=occurrence_id
        ).model_dump(mode="json")
    except Exception as exc:
        _raise_http(exc)
        raise AssertionError("unreachable")


@app.post("/api/v1/tenants/{tenant_id}/occurrences/{occurrence_id}/approve", dependencies=[Depends(require_permission(Permission.SCHEDULE_APPROVE))])
def approve_schedule_occurrence(
    tenant_id: str,
    occurrence_id: str,
    decision: OccurrenceDecision,
    http_request: Request,
) -> dict:
    try:
        return scheduler.approve_occurrence(
            tenant_id=tenant_id,
            occurrence_id=occurrence_id,
            decision=decision.model_copy(update={"actor_id": _actor(http_request, decision.actor_id)}),
        ).model_dump(mode="json")
    except Exception as exc:
        _raise_http(exc)
        raise AssertionError("unreachable")


@app.post("/api/v1/tenants/{tenant_id}/occurrences/{occurrence_id}/cancel", dependencies=[Depends(require_permission(Permission.SCHEDULE_APPROVE))])
def cancel_schedule_occurrence(
    tenant_id: str,
    occurrence_id: str,
    decision: OccurrenceDecision,
    http_request: Request,
) -> dict:
    try:
        return scheduler.cancel_occurrence(
            tenant_id=tenant_id,
            occurrence_id=occurrence_id,
            decision=decision.model_copy(update={"actor_id": _actor(http_request, decision.actor_id)}),
        ).model_dump(mode="json")
    except Exception as exc:
        _raise_http(exc)
        raise AssertionError("unreachable")


@app.post("/api/v1/tenants/{tenant_id}/evidence", dependencies=[Depends(require_permission(Permission.EVIDENCE_WRITE))])
async def ingest_evidence(
    tenant_id: str,
    request: Request,
    source_type: str = Query(min_length=1, max_length=64),
    source_locator: str = Query(min_length=1, max_length=4000),
    actor_id: str = Query(min_length=1, max_length=255),
    actor_type: str = Query(default="service", min_length=1, max_length=32),
    engagement_id: str | None = Query(default=None, max_length=64),
    task_id: str | None = Query(default=None, max_length=64),
    acquisition_key: str | None = Query(default=None, max_length=255),
    original_filename: str | None = Query(default=None, max_length=512),
    classification: str = Query(default="internal", min_length=1, max_length=64),
    accepted: bool = False,
    tainted: bool = False,
    retention_until: date | None = None,
    legal_hold: bool = False,
) -> dict:
    payload = await _bounded_request_body(request)
    content_type = request.headers.get("content-type")
    mime_type = content_type.split(";", 1)[0].strip() if content_type else None
    try:
        return vault.ingest_bytes(
            tenant_id=tenant_id,
            payload=payload,
            source_type=source_type,
            source_locator=source_locator,
            actor_id=_actor(request, actor_id),
            actor_type=actor_type,
            engagement_id=engagement_id,
            task_id=task_id,
            acquisition_key=acquisition_key,
            original_filename=original_filename,
            mime_type=mime_type,
            classification=classification,
            accepted=accepted,
            tainted=tainted,
            retention_until=retention_until,
            legal_hold=legal_hold,
        ).model_dump(mode="json")
    except Exception as exc:
        _raise_http(exc)
        raise AssertionError("unreachable")


@app.post("/api/v1/tenants/{tenant_id}/evidence/derived", dependencies=[Depends(require_permission(Permission.EVIDENCE_WRITE))])
async def create_derived_evidence(
    tenant_id: str,
    request: Request,
    source_evidence_id: list[str] = Query(),
    operation: str = Query(min_length=1, max_length=64),
    tool_version: str = Query(min_length=1, max_length=128),
    actor_id: str = Query(min_length=1, max_length=255),
    actor_type: str = Query(default="service", min_length=1, max_length=32),
    acquisition_key: str | None = Query(default=None, max_length=255),
    original_filename: str | None = Query(default=None, max_length=512),
    classification: str | None = Query(default=None, max_length=64),
    accepted: bool = False,
) -> dict:
    payload = await _bounded_request_body(request)
    content_type = request.headers.get("content-type")
    mime_type = content_type.split(";", 1)[0].strip() if content_type else None
    try:
        return vault.create_derivative(
            tenant_id=tenant_id,
            source_evidence_ids=source_evidence_id,
            payload=payload,
            operation=operation,
            tool_version=tool_version,
            actor_id=_actor(request, actor_id),
            actor_type=actor_type,
            acquisition_key=acquisition_key,
            original_filename=original_filename,
            mime_type=mime_type,
            classification=classification,
            accepted=accepted,
        ).model_dump(mode="json")
    except Exception as exc:
        _raise_http(exc)
        raise AssertionError("unreachable")


@app.get("/api/v1/tenants/{tenant_id}/evidence", dependencies=[Depends(require_permission(Permission.EVIDENCE_READ))])
def list_evidence(
    tenant_id: str,
    engagement_id: str | None = None,
    include_deleted: bool = False,
    limit: int = Query(default=500, ge=1, le=1000),
) -> list[dict]:
    try:
        return [
            item.model_dump(mode="json")
            for item in vault.list(
                tenant_id,
                engagement_id=engagement_id,
                include_deleted=include_deleted,
                limit=limit,
            )
        ]
    except Exception as exc:
        _raise_http(exc)
        raise AssertionError("unreachable")


@app.get("/api/v1/tenants/{tenant_id}/evidence/{evidence_id}", dependencies=[Depends(require_permission(Permission.EVIDENCE_READ))])
def get_evidence(tenant_id: str, evidence_id: str, include_deleted: bool = False) -> dict:
    try:
        return vault.get(
            tenant_id, evidence_id, include_deleted=include_deleted
        ).model_dump(mode="json")
    except Exception as exc:
        _raise_http(exc)
        raise AssertionError("unreachable")


@app.get("/api/v1/tenants/{tenant_id}/evidence/{evidence_id}/content", dependencies=[Depends(require_permission(Permission.EVIDENCE_READ))])
def download_evidence_content(
    tenant_id: str,
    evidence_id: str,
    http_request: Request,
    actor_id: str = Query(min_length=1, max_length=255),
    purpose: str = Query(min_length=1, max_length=4000),
) -> Response:
    try:
        item = vault.get(tenant_id, evidence_id)
        payload = vault.read_bytes(
            tenant_id,
            evidence_id,
            actor_id=_actor(http_request, actor_id),
            purpose=purpose,
        )
        return Response(
            content=payload,
            media_type=item.mime_type or "application/octet-stream",
            headers={"X-Evidence-SHA256": item.content_sha256},
        )
    except Exception as exc:
        _raise_http(exc)
        raise AssertionError("unreachable")


@app.post("/api/v1/tenants/{tenant_id}/evidence/{evidence_id}/verify", dependencies=[Depends(require_permission(Permission.EVIDENCE_READ))])
def verify_evidence_integrity(tenant_id: str, evidence_id: str) -> dict:
    try:
        return vault.verify_integrity(tenant_id, evidence_id).model_dump(mode="json")
    except Exception as exc:
        _raise_http(exc)
        raise AssertionError("unreachable")


@app.get("/api/v1/tenants/{tenant_id}/evidence/{evidence_id}/custody", dependencies=[Depends(require_permission(Permission.EVIDENCE_READ))])
def get_evidence_custody(tenant_id: str, evidence_id: str) -> dict:
    try:
        return {
            "verification": vault.verify_custody_chain(
                tenant_id, evidence_id
            ).model_dump(mode="json"),
            "events": [
                event.model_dump(mode="json")
                for event in vault.list_custody(tenant_id, evidence_id)
            ],
        }
    except Exception as exc:
        _raise_http(exc)
        raise AssertionError("unreachable")


@app.get("/api/v1/tenants/{tenant_id}/evidence/{evidence_id}/lineage", dependencies=[Depends(require_permission(Permission.EVIDENCE_READ))])
def get_evidence_lineage(tenant_id: str, evidence_id: str) -> dict:
    try:
        return vault.lineage(tenant_id, evidence_id).model_dump(mode="json")
    except Exception as exc:
        _raise_http(exc)
        raise AssertionError("unreachable")


@app.put("/api/v1/tenants/{tenant_id}/evidence/{evidence_id}/retention", dependencies=[Depends(require_permission(Permission.EVIDENCE_ADMIN))])
def update_evidence_retention(
    tenant_id: str,
    evidence_id: str,
    payload: EvidenceRetentionRequest,
    http_request: Request,
) -> dict:
    try:
        return vault.set_retention(
            tenant_id,
            evidence_id,
            actor_id=_actor(http_request, payload.actor_id),
            retention_until=payload.retention_until,
            legal_hold=payload.legal_hold,
            reason=payload.reason,
        ).model_dump(mode="json")
    except Exception as exc:
        _raise_http(exc)
        raise AssertionError("unreachable")


@app.post("/api/v1/tenants/{tenant_id}/evidence/{evidence_id}/purge", dependencies=[Depends(require_permission(Permission.EVIDENCE_ADMIN))])
def purge_evidence(
    tenant_id: str,
    evidence_id: str,
    payload: EvidencePurgeRequest,
    http_request: Request,
) -> dict:
    try:
        return vault.purge(
            tenant_id,
            evidence_id,
            actor_id=_actor(http_request, payload.actor_id),
            reason=payload.reason,
            as_of=payload.as_of,
        ).model_dump(mode="json")
    except Exception as exc:
        _raise_http(exc)
        raise AssertionError("unreachable")


@app.post("/api/v1/tenants/{tenant_id}/evidence-exports", dependencies=[Depends(require_permission(Permission.EVIDENCE_READ))])
def export_evidence(tenant_id: str, payload: EvidenceExportRequest, http_request: Request) -> FileResponse:
    settings.evidence_export_root.mkdir(parents=True, exist_ok=True)
    descriptor, raw_path = tempfile.mkstemp(
        prefix=f"{tenant_id}-evidence-", suffix=".zip", dir=settings.evidence_export_root
    )
    os.close(descriptor)
    path = Path(raw_path)
    try:
        verification = vault.create_export(
            tenant_id=tenant_id,
            evidence_ids=payload.evidence_ids,
            destination=path,
            actor_id=_actor(http_request, payload.actor_id),
            purpose=payload.purpose,
            include_ancestors=payload.include_ancestors,
        )
    except Exception as exc:
        path.unlink(missing_ok=True)
        _raise_http(exc)
        raise AssertionError("unreachable")
    return FileResponse(
        path,
        filename="assuranceos-evidence-export.zip",
        media_type="application/zip",
        headers={
            "X-Package-SHA256": verification.package_sha256,
            "X-Manifest-SHA256": verification.manifest_sha256 or "",
        },
        background=BackgroundTask(path.unlink, missing_ok=True),
    )


@app.post("/api/v1/tenants/{tenant_id}/connectors", dependencies=[Depends(require_permission(Permission.CONNECTOR_WRITE))])
def register_connector(tenant_id: str, request: ConnectorInstanceInput) -> dict:
    try:
        return connector_service.register_instance(tenant_id, request).model_dump(mode="json")
    except Exception as exc:
        _raise_http(exc)
        raise AssertionError("unreachable")


@app.get("/api/v1/tenants/{tenant_id}/connectors", dependencies=[Depends(require_permission(Permission.CONNECTOR_READ))])
def list_connectors(tenant_id: str) -> list[dict]:
    return [item.model_dump(mode="json") for item in connector_service.list_instances(tenant_id)]


@app.post("/api/v1/tenants/{tenant_id}/connectors/{connector_instance_id}/grants", dependencies=[Depends(require_permission(Permission.CONNECTOR_APPROVE))])
def create_collection_grant(
    tenant_id: str,
    connector_instance_id: str,
    payload: CollectionGrantInput,
    http_request: Request,
) -> dict:
    try:
        return connector_service.create_grant(
            tenant_id,
            connector_instance_id,
            payload.model_copy(update={"approved_by": _actor(http_request, payload.approved_by)}),
        ).model_dump(mode="json")
    except Exception as exc:
        _raise_http(exc)
        raise AssertionError("unreachable")


@app.get("/api/v1/tenants/{tenant_id}/collection-grants", dependencies=[Depends(require_permission(Permission.CONNECTOR_READ))])
def list_collection_grants(
    tenant_id: str, connector_instance_id: str | None = None
) -> list[dict]:
    return [
        item.model_dump(mode="json")
        for item in connector_service.list_grants(tenant_id, connector_instance_id)
    ]


@app.post("/api/v1/tenants/{tenant_id}/collection-grants/{grant_id}/revoke", dependencies=[Depends(require_permission(Permission.CONNECTOR_APPROVE))])
def revoke_collection_grant(
    tenant_id: str,
    grant_id: str,
    payload: GrantRevocationRequest,
    http_request: Request,
) -> dict:
    try:
        return connector_service.revoke_grant(
            tenant_id,
            grant_id,
            actor_id=_actor(http_request, payload.actor_id),
            reason=payload.reason
        ).model_dump(mode="json")
    except Exception as exc:
        _raise_http(exc)
        raise AssertionError("unreachable")


@app.get("/api/v1/tenants/{tenant_id}/connector-runs/{run_id}", dependencies=[Depends(require_permission(Permission.CONNECTOR_READ))])
def get_connector_run(tenant_id: str, run_id: str) -> dict:
    try:
        return connector_service.get_run(tenant_id, run_id).model_dump(mode="json")
    except Exception as exc:
        _raise_http(exc)
        raise AssertionError("unreachable")


@app.get(
    "/api/v1/control-tests",
    dependencies=[Depends(require_permission(Permission.CONTROL_TEST_READ))],
)
def list_control_tests(domain: str | None = Query(default=None, max_length=64)) -> list[dict]:
    return _control_test_service().list_releases(domain=domain)


@app.get(
    "/api/v1/control-tests/{test_id}/versions/{version}",
    dependencies=[Depends(require_permission(Permission.CONTROL_TEST_READ))],
)
def get_control_test(test_id: str, version: str) -> dict:
    try:
        return _control_test_service().get_release(test_id, version)
    except Exception as exc:
        _raise_http(exc)


@app.post(
    "/api/v1/tenants/{tenant_id}/control-test-runs",
    dependencies=[Depends(require_permission(Permission.CONTROL_TEST_EXECUTE))],
)
def execute_control_test(
    tenant_id: str,
    body: ControlTestExecutionRequest,
    request: Request,
) -> dict:
    try:
        domain_request = ControlTestRunRequest(
            test_id=body.test_id,
            version=body.version,
            purpose=body.purpose,
            period_start=body.period_start,
            period_end=body.period_end,
            requested_by=_actor(request),
            idempotency_key=body.idempotency_key,
            engagement_id=body.engagement_id,
            task_id=body.task_id,
            parameters=body.parameters,
            datasets=body.datasets,
        )
        return _control_test_service().run(tenant_id, domain_request).model_dump(mode="json")
    except Exception as exc:
        _raise_http(exc)


@app.get(
    "/api/v1/tenants/{tenant_id}/control-test-runs/{run_id}",
    dependencies=[Depends(require_permission(Permission.CONTROL_TEST_READ))],
)
def get_control_test_run(tenant_id: str, run_id: str) -> dict:
    try:
        return _control_test_service().get_run(tenant_id, run_id).model_dump(mode="json")
    except Exception as exc:
        _raise_http(exc)


@app.post(
    "/api/v1/tenants/{tenant_id}/control-test-runs/{run_id}/verify-reproducibility",
    dependencies=[Depends(require_permission(Permission.CONTROL_TEST_EXECUTE))],
)
def verify_control_test_run(
    tenant_id: str,
    run_id: str,
    body: ControlTestReproductionRequest,
    request: Request,
) -> dict:
    try:
        original = _control_test_service().get_run(tenant_id, run_id)
        domain_request = ControlTestRunRequest(
            test_id=original.test_id,
            version=original.version,
            purpose=body.purpose,
            period_start=body.period_start,
            period_end=body.period_end,
            requested_by=_actor(request),
            idempotency_key=f"reproduce:{run_id}",
            parameters=body.parameters,
            datasets=body.datasets,
        )
        return _control_test_service().verify_reproducibility(
            tenant_id, run_id, domain_request
        ).model_dump(mode="json")
    except Exception as exc:
        _raise_http(exc)


@app.get("/judge", response_class=HTMLResponse)
def judge_mode() -> str:
    template = Path("apps/web/judge.html")
    if not template.exists():
        template = Path(__file__).resolve().parents[2] / "apps/web/judge.html"
    return template.read_text(encoding="utf-8")
