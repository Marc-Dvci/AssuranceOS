from __future__ import annotations

from datetime import date, datetime, timedelta
from functools import lru_cache
import hashlib
from pathlib import Path
from typing import Literal
import os
import tempfile

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from pydantic import BaseModel, Field
from starlette.background import BackgroundTask
from starlette.middleware.trustedhost import TrustedHostMiddleware
from uuid import uuid4

from .adjudication import (
    AdjudicationRequest,
    AdjudicationService,
    ClosureSubmission,
    DisputeGround,
    DisputeRequest,
    DisputeResolution,
    DisputeResolutionRequest,
    HumanDecision,
    MaterialityInputs,
    MaterialityPolicy,
    MaterialityRequest,
    ProposedFinding,
    QualityReviewRequest,
    RemediationRequest,
    RetestOutcome,
    RetestRequest,
    SeverityOverrideRequest,
    SkepticReviewer,
    writer_from_connector,
)
from .adjudication.exceptions import (
    ClosureEvidenceError,
    DisputeError,
    FindingNotFoundError,
    HumanGateError,
    IdempotencyConflictError,
    IndependenceError,
    InvalidTransitionError,
    MaterialityError,
    QualityGateError,
    RemediationNotFoundError,
    TicketingError,
)
from .adjudication.demo import run_assurance_loop_demo
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
from .evaluation import AgentEvaluationRunner
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
from .portfolio import (
    AssuranceSource,
    Candidate,
    CapacityError,
    CapacityPolicy,
    PlanNotFoundError,
    PlanStateError,
    PortfolioService,
    RiskFactors,
    RiskNotFoundError,
    ScoringPolicy,
)
from .delegation import engagement_delegation
from .economics import engagement_economics
from .risk_discovery import discovered_universe
from .product import (
    agent_catalogue,
    evaluator_overview,
    finding_detail,
    ground_truth,
    tenant_cockpit,
    trace_detail,
)
from .product_schemas import (
    AgentCatalogueResponse,
    DelegationResponse,
    EconomicsResponse,
    EvaluationSummaryResponse,
    GroundTruthResponse,
    IdempotencyProofResponse,
    JudgeOverviewResponse,
    PromptInjectionProofResponse,
)
from .monitoring import (
    ContinuousMonitoringService,
    MonitorDefinitionInput,
    MonitorExecutionInput,
)
from .onboarding import (
    FactDecisionInput,
    FactProposalInput,
    OnboardingService,
    OnboardingStartInput,
    PublicSourceInput,
)
from .reporting import (
    ClaimInput,
    ReportingError,
    ReportNotFoundError,
    ReportRequest,
    ReportingService,
    UnsupportedClaimError,
)
from .security import JwtVerifier, Permission, Principal, effective_actor, require_permission
from .standards import (
    AuditPackCompiler,
    AuditPackRegistry,
    CrosswalkInput,
    CrosswalkRelation,
    OrganizationContext,
    StandardsService,
    released_agent_versions,
    released_test_versions,
)
from .standards.definitions import CriterionInput, StandardInput
from .standards.exceptions import (
    CriteriaEffectivityError,
    CriterionNotFoundError,
    DuplicateStandardError,
    PackCompatibilityError,
    PackCompilationError,
    PackEntitlementError,
    PackNotFoundError,
    PackNotReleasedError,
    StandardNotFoundError,
)
from .vault import (
    BaselineContentInspector,
    Ed25519ManifestSigner,
    EvidenceVault,
    GoogleCloudStorageObjectStore,
)
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
from .governance.managed_armor import GoogleManagedModelArmor, build_model_armor
from .governance.telemetry import TelemetryConfig, configure_telemetry

app = FastAPI(title="AssuranceOS API", version="0.8.0")
app.state.settings = settings
if os.getenv("GOOGLE_CLOUD_PROJECT") or os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT"):
    configure_telemetry(
        TelemetryConfig(
            environment=settings.environment,
            cloud_project=os.getenv("GOOGLE_CLOUD_PROJECT"),
            cloud_region=os.getenv("GOOGLE_CLOUD_LOCATION"),
        )
    )
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


def _monitoring_service() -> ContinuousMonitoringService:
    return ContinuousMonitoringService(database, _control_test_service())


def _onboarding_service() -> OnboardingService:
    return OnboardingService(database, vault)


# The Audit Pack registry is loaded at import, like the control-test registry, so
# a deployment carrying an unsigned or incoherent pack fails to start rather than
# failing on the first engagement someone tries to compile.
audit_pack_registry = AuditPackRegistry(
    settings.audit_pack_root,
    trusted_public_key=settings.audit_pack_public_key.read_bytes(),
).load()

_pack_compiler = AuditPackCompiler(
    released_tests=released_test_versions(control_test_registry),
    released_agents=released_agent_versions(settings.agent_root),
)


def _standards_service() -> StandardsService:
    return StandardsService(database, registry=audit_pack_registry, compiler=_pack_compiler)


# The product frontend is a single self-contained document: its styles, its
# script and its font are all inline, and it loads nothing over the network.
# That buys a policy with no third-party origin in it at all — but it also means
# script-src and style-src must permit inline, so this policy is not the control
# that stops cross-site scripting. Output escaping in the frontend is. What the
# policy does stop is exfiltration to another origin, framing, base-tag
# hijacking, plugin content, and form posts to anywhere but this service.
CONTENT_SECURITY_POLICY = "; ".join(
    (
        "default-src 'self'",
        "script-src 'self' 'unsafe-inline'",
        "style-src 'self' 'unsafe-inline'",
        "img-src 'self' data:",
        "font-src 'self' data:",
        "connect-src 'self'",
        "form-action 'self'",
        "frame-ancestors 'none'",
        "base-uri 'none'",
        "object-src 'none'",
    )
)

SECURITY_HEADERS = {
    "content-security-policy": CONTENT_SECURITY_POLICY,
    "x-content-type-options": "nosniff",
    # Redundant with frame-ancestors on a current browser, and the only
    # protection on an old one.
    "x-frame-options": "DENY",
    "referrer-policy": "no-referrer",
    "permissions-policy": "geolocation=(), camera=(), microphone=(), payment=()",
    "cross-origin-opener-policy": "same-origin",
}


@app.middleware("http")
async def request_context(request: Request, call_next):
    request_id = request.headers.get("x-request-id") or f"req_{uuid4().hex[:20]}"
    request.state.request_id = request_id
    try:
        response = await call_next(request)
    except Exception:
        raise
    response.headers["x-request-id"] = request_id
    response.headers["cache-control"] = "no-store"
    for header, value in SECURITY_HEADERS.items():
        response.headers[header] = value
    # HSTS only where the connection actually was TLS. Asserting it over plain
    # HTTP pins a scheme the local runtime does not serve, and a header that is
    # wrong in development is a header nobody trusts in production.
    forwarded = request.headers.get("x-forwarded-proto", "").split(",")[0].strip()
    if (forwarded or request.url.scheme) == "https":
        response.headers["strict-transport-security"] = "max-age=31536000; includeSubDomains"
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


class OnboardingApprovalRequest(BaseModel):
    approved_by: str | None = Field(default=None, max_length=255)


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


class ProposeFindingRequest(BaseModel):
    """A proposed finding plus the canonical context the skeptic consults.

    The context is supplied by the caller rather than assumed, because which
    exception register and which audit period apply is an engagement fact. An
    absent register produces no contradiction of that kind rather than a silent
    pass.
    """

    finding: ProposedFinding
    exception_rows: list[dict] = Field(default_factory=list)
    approved_exceptions: list[dict] = Field(default_factory=list)
    compensating_controls: list[dict] = Field(default_factory=list)
    period_start: date | None = None
    period_end: date | None = None
    authored_by: str | None = None


class AdjudicateFindingRequest(BaseModel):
    decision: HumanDecision
    reason: str = Field(min_length=3, max_length=4000)
    idempotency_key: str = Field(min_length=3, max_length=255)
    # Overriding the actor requires the principal to be permitted to act for them;
    # `_actor` resolves that, and the service still refuses automated actors.
    actor_id: str | None = None


class AssessMaterialityRequest(BaseModel):
    """Measured inputs for a materiality score, and the policy to score them under.

    ``assessed_by`` is not restricted to a person the way a decision is. An agent
    may compute this: the score is arithmetic over declared inputs, and what an
    agent must not reach is the approval that follows it.
    """

    inputs: MaterialityInputs
    policy: MaterialityPolicy | None = None
    assessed_by: str | None = None


class OverrideSeverityRequest(BaseModel):
    severity: Literal["low", "medium", "high", "critical"]
    reason: str = Field(min_length=10, max_length=4000)
    actor_id: str | None = None


class ReviewQualityRequest(BaseModel):
    notes: str | None = Field(default=None, max_length=4000)
    reviewer_id: str | None = None


class RaiseDisputeRequest(BaseModel):
    ground: DisputeGround
    statement: str = Field(min_length=10, max_length=4000)
    evidence_ids: list[str] = Field(default_factory=list)
    raised_by: str | None = None


class ResolveDisputeRequest(BaseModel):
    resolution: DisputeResolution
    reason: str = Field(min_length=10, max_length=4000)
    resolved_by: str | None = None


class OpenRemediationRequest(BaseModel):
    owner_ref: str = Field(min_length=1, max_length=255)
    due_date: date
    action_plan: str = Field(min_length=3, max_length=4000)
    idempotency_key: str = Field(min_length=3, max_length=255)
    closure_evidence_required: bool = True
    escalation_policy: dict = Field(default_factory=dict)
    external_system: Literal["none", "jira", "servicenow"] = "none"
    external_target: str | None = Field(default=None, max_length=128)


class SubmitClosureRequest(BaseModel):
    response_text: str = Field(min_length=3, max_length=4000)
    closure_evidence_ids: list[str] = Field(default_factory=list)
    action_plan: str | None = None
    submitted_by: str | None = None


class RecordRetestRequest(BaseModel):
    procedure_ref: str = Field(min_length=1, max_length=255)
    idempotency_key: str = Field(min_length=3, max_length=255)
    outcome: RetestOutcome
    evidence_ids: list[str] = Field(default_factory=list)
    detail: str = Field(default="", max_length=4000)
    fresh_evidence_collected_at: datetime | None = None
    performed_by: str | None = None


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
    if isinstance(exc, (FindingNotFoundError, RemediationNotFoundError)):
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if isinstance(exc, (PackNotFoundError, StandardNotFoundError, CriterionNotFoundError)):
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if isinstance(exc, (RiskNotFoundError, PlanNotFoundError, ReportNotFoundError)):
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    # An unsupported material claim is a 422: the request was well formed, and
    # what it asked for is a document that would not be true.
    if isinstance(exc, UnsupportedClaimError):
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if isinstance(exc, ReportingError):
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    # A plan that does not fit its own capacity is a 409, not a 422: the request
    # is well formed, and what is wrong is the state of the proposal.
    if isinstance(exc, (PlanStateError, CapacityError)):
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    # An unentitled standard is a 403: the request was well formed and the caller
    # was authenticated; the platform declined to reproduce licensed text for a
    # tenant with no licence. Reporting it as a 422 would invite a retry.
    if isinstance(exc, PackEntitlementError):
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    if isinstance(exc, (PackNotReleasedError, PackCompilationError, DuplicateStandardError)):
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if isinstance(exc, (PackCompatibilityError, CriteriaEffectivityError)):
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    # A governance refusal is a 403, not a 422. The request was well formed and
    # the caller was authenticated; the system declined to let this actor cause
    # this effect. Reporting it as a validation error would invite a client to
    # "fix" the payload and retry.
    if isinstance(exc, (HumanGateError, IndependenceError, QualityGateError)):
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    if isinstance(exc, (InvalidTransitionError, IdempotencyConflictError, DisputeError)):
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if isinstance(exc, (ClosureEvidenceError, MaterialityError)):
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    # The request was valid and the system tried; the provider refused or could not
    # be reconciled. 502 says the failure is downstream, which is what a caller
    # needs in order to decide whether retrying is sensible.
    if isinstance(exc, TicketingError):
        raise HTTPException(status_code=502, detail=str(exc)) from exc
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
        "local_privacy_mode": settings.is_local_privacy,
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
        checks["control_test_registry_database"] = len(
            _control_test_service().list_releases()
        ) == len(control_test_registry.list())
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


# Declared before ``/api/v1/agents/{agent_id}`` on purpose. Routes resolve in
# registration order, so a literal segment defined after the parameterised one
# is never reached — "catalogue" would arrive as an agent id and 404.
@app.get(
    "/api/v1/agents/catalogue",
    response_model=AgentCatalogueResponse,
    dependencies=[Depends(require_permission(Permission.AGENTS_READ))],
)
def product_agent_catalogue() -> dict:
    """The signed fleet as something a department can shop from.

    The evaluator inventory answers "are these real". This answers the question
    an organisation adopting the fleet asks instead: what is each agent for,
    what may it touch, where does it stop, and what is it known not to do.
    """
    return agent_catalogue(_registry())


@app.get(
    "/api/v1/agents/{agent_id}", dependencies=[Depends(require_permission(Permission.AGENTS_READ))]
)
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
    """Re-run the golden audit in place.

    Only this engagement is replaced. The tenant also holds the approved plan,
    the issued report, and the recorded traces that ``seed_demo_tenant`` put
    there, and deleting an evaluator's whole workspace to re-run one audit would
    make the button destructive rather than repeatable.
    """
    return run_golden_engagement(settings.demo_root, ledger, reset=False)


@app.get("/api/v1/demo/events", dependencies=[Depends(require_permission(Permission.DEMO_OPERATE))])
def demo_events() -> list[dict]:
    return ledger.list_events(TENANT_ID, ENGAGEMENT_ID)


@app.post(
    "/api/v1/tenants/{tenant_id}/engagements/{engagement_id}/workflow",
    dependencies=[Depends(require_permission(Permission.ENGAGEMENT_WRITE))],
)
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


@app.post(
    "/api/v1/tenants/{tenant_id}/engagements/{engagement_id}/start",
    dependencies=[Depends(require_permission(Permission.ENGAGEMENT_WRITE))],
)
def start_engagement_workflow(tenant_id: str, engagement_id: str) -> dict:
    try:
        return orchestrator.start_engagement(
            tenant_id=tenant_id, engagement_id=engagement_id
        ).model_dump(mode="json")
    except Exception as exc:
        _raise_http(exc)
        raise AssertionError("unreachable")


@app.get(
    "/api/v1/tenants/{tenant_id}/engagements/{engagement_id}/orchestration",
    dependencies=[Depends(require_permission(Permission.ENGAGEMENT_READ))],
)
def get_engagement_orchestration(tenant_id: str, engagement_id: str) -> dict:
    try:
        return orchestrator.snapshot(tenant_id=tenant_id, engagement_id=engagement_id).model_dump(
            mode="json"
        )
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


@app.post(
    "/api/v1/tenants/{tenant_id}/tasks/{task_id}/gate/approve",
    dependencies=[Depends(require_permission(Permission.ENGAGEMENT_APPROVE))],
)
def approve_task_gate(
    tenant_id: str, task_id: str, decision: GateDecision, http_request: Request
) -> dict:
    try:
        return orchestrator.approve_gate(
            tenant_id=tenant_id,
            task_id=task_id,
            decision=decision.model_copy(
                update={"actor_id": _actor(http_request, decision.actor_id)}
            ),
        ).model_dump(mode="json")
    except Exception as exc:
        _raise_http(exc)
        raise AssertionError("unreachable")


@app.post(
    "/api/v1/tenants/{tenant_id}/tasks/{task_id}/gate/reject",
    dependencies=[Depends(require_permission(Permission.ENGAGEMENT_APPROVE))],
)
def reject_task_gate(
    tenant_id: str, task_id: str, decision: GateDecision, http_request: Request
) -> dict:
    try:
        return orchestrator.reject_gate(
            tenant_id=tenant_id,
            task_id=task_id,
            decision=decision.model_copy(
                update={"actor_id": _actor(http_request, decision.actor_id)}
            ),
        ).model_dump(mode="json")
    except Exception as exc:
        _raise_http(exc)
        raise AssertionError("unreachable")


@app.post(
    "/api/v1/tenants/{tenant_id}/engagements/{engagement_id}/cancel",
    dependencies=[Depends(require_permission(Permission.ENGAGEMENT_APPROVE))],
)
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


@app.post(
    "/api/v1/demo/orchestration/run",
    dependencies=[Depends(require_permission(Permission.DEMO_OPERATE))],
)
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
            for item in orchestrator.list_attempts(tenant_id=tenant_id, engagement_id=engagement_id)
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


@app.post(
    "/api/v1/tenants/{tenant_id}/schedules/{schedule_id}/simulate",
    dependencies=[Depends(require_permission(Permission.SCHEDULE_READ))],
)
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


@app.post(
    "/api/v1/tenants/{tenant_id}/schedules/{schedule_id}/evaluate",
    dependencies=[Depends(require_permission(Permission.SCHEDULE_WRITE))],
)
def evaluate_schedule(tenant_id: str, schedule_id: str, request: ScheduleEvaluationRequest) -> dict:
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


@app.get(
    "/api/v1/tenants/{tenant_id}/schedules/{schedule_id}/occurrences",
    dependencies=[Depends(require_permission(Permission.SCHEDULE_READ))],
)
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


@app.get(
    "/api/v1/tenants/{tenant_id}/occurrences/{occurrence_id}",
    dependencies=[Depends(require_permission(Permission.SCHEDULE_READ))],
)
def get_schedule_occurrence(tenant_id: str, occurrence_id: str) -> dict:
    try:
        return scheduler.get_occurrence(
            tenant_id=tenant_id, occurrence_id=occurrence_id
        ).model_dump(mode="json")
    except Exception as exc:
        _raise_http(exc)
        raise AssertionError("unreachable")


@app.post(
    "/api/v1/tenants/{tenant_id}/occurrences/{occurrence_id}/approve",
    dependencies=[Depends(require_permission(Permission.SCHEDULE_APPROVE))],
)
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
            decision=decision.model_copy(
                update={"actor_id": _actor(http_request, decision.actor_id)}
            ),
        ).model_dump(mode="json")
    except Exception as exc:
        _raise_http(exc)
        raise AssertionError("unreachable")


@app.post(
    "/api/v1/tenants/{tenant_id}/occurrences/{occurrence_id}/cancel",
    dependencies=[Depends(require_permission(Permission.SCHEDULE_APPROVE))],
)
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
            decision=decision.model_copy(
                update={"actor_id": _actor(http_request, decision.actor_id)}
            ),
        ).model_dump(mode="json")
    except Exception as exc:
        _raise_http(exc)
        raise AssertionError("unreachable")


@app.post(
    "/api/v1/tenants/{tenant_id}/evidence",
    dependencies=[Depends(require_permission(Permission.EVIDENCE_WRITE))],
)
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


@app.post(
    "/api/v1/tenants/{tenant_id}/evidence/derived",
    dependencies=[Depends(require_permission(Permission.EVIDENCE_WRITE))],
)
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


@app.get(
    "/api/v1/tenants/{tenant_id}/evidence",
    dependencies=[Depends(require_permission(Permission.EVIDENCE_READ))],
)
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


@app.get(
    "/api/v1/tenants/{tenant_id}/evidence/{evidence_id}",
    dependencies=[Depends(require_permission(Permission.EVIDENCE_READ))],
)
def get_evidence(tenant_id: str, evidence_id: str, include_deleted: bool = False) -> dict:
    try:
        return vault.get(tenant_id, evidence_id, include_deleted=include_deleted).model_dump(
            mode="json"
        )
    except Exception as exc:
        _raise_http(exc)
        raise AssertionError("unreachable")


@app.get(
    "/api/v1/tenants/{tenant_id}/evidence/{evidence_id}/content",
    dependencies=[Depends(require_permission(Permission.EVIDENCE_READ))],
)
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


@app.post(
    "/api/v1/tenants/{tenant_id}/evidence/{evidence_id}/verify",
    dependencies=[Depends(require_permission(Permission.EVIDENCE_READ))],
)
def verify_evidence_integrity(tenant_id: str, evidence_id: str) -> dict:
    try:
        return vault.verify_integrity(tenant_id, evidence_id).model_dump(mode="json")
    except Exception as exc:
        _raise_http(exc)
        raise AssertionError("unreachable")


@app.get(
    "/api/v1/tenants/{tenant_id}/evidence/{evidence_id}/custody",
    dependencies=[Depends(require_permission(Permission.EVIDENCE_READ))],
)
def get_evidence_custody(tenant_id: str, evidence_id: str) -> dict:
    try:
        return {
            "verification": vault.verify_custody_chain(tenant_id, evidence_id).model_dump(
                mode="json"
            ),
            "events": [
                event.model_dump(mode="json")
                for event in vault.list_custody(tenant_id, evidence_id)
            ],
        }
    except Exception as exc:
        _raise_http(exc)
        raise AssertionError("unreachable")


@app.get(
    "/api/v1/tenants/{tenant_id}/evidence/{evidence_id}/lineage",
    dependencies=[Depends(require_permission(Permission.EVIDENCE_READ))],
)
def get_evidence_lineage(tenant_id: str, evidence_id: str) -> dict:
    try:
        return vault.lineage(tenant_id, evidence_id).model_dump(mode="json")
    except Exception as exc:
        _raise_http(exc)
        raise AssertionError("unreachable")


@app.put(
    "/api/v1/tenants/{tenant_id}/evidence/{evidence_id}/retention",
    dependencies=[Depends(require_permission(Permission.EVIDENCE_ADMIN))],
)
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


@app.post(
    "/api/v1/tenants/{tenant_id}/evidence/{evidence_id}/purge",
    dependencies=[Depends(require_permission(Permission.EVIDENCE_ADMIN))],
)
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


@app.post(
    "/api/v1/tenants/{tenant_id}/evidence-exports",
    dependencies=[Depends(require_permission(Permission.EVIDENCE_READ))],
)
def export_evidence(
    tenant_id: str, payload: EvidenceExportRequest, http_request: Request
) -> FileResponse:
    settings.evidence_export_root.mkdir(parents=True, exist_ok=True)
    # The tenant is deliberately absent from the temporary file name. It used to
    # be the prefix, which puts a path segment the caller controls into a path
    # the server creates: a tenant id carrying a separator escapes the export
    # root. The tenant is inside the package manifest, where it is signed, and
    # the file itself lives for one response.
    descriptor, raw_path = tempfile.mkstemp(
        prefix="evidence-", suffix=".zip", dir=settings.evidence_export_root
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


@app.post(
    "/api/v1/tenants/{tenant_id}/onboarding-workflows",
    dependencies=[Depends(require_permission(Permission.PORTFOLIO_WRITE))],
)
def start_onboarding(tenant_id: str, body: OnboardingStartInput) -> dict:
    try:
        return _onboarding_service().start(tenant_id, body)
    except Exception as exc:
        _raise_http(exc)


@app.get(
    "/api/v1/tenants/{tenant_id}/onboarding-workflows/{workflow_id}",
    dependencies=[Depends(require_permission(Permission.PORTFOLIO_READ))],
)
def get_onboarding(tenant_id: str, workflow_id: str) -> dict:
    try:
        return _onboarding_service().get(tenant_id, workflow_id)
    except Exception as exc:
        _raise_http(exc)


@app.post(
    "/api/v1/tenants/{tenant_id}/onboarding-workflows/{workflow_id}/sources",
    dependencies=[Depends(require_permission(Permission.CONNECTOR_WRITE))],
)
def capture_onboarding_source(
    tenant_id: str, workflow_id: str, body: PublicSourceInput, request: Request
) -> dict:
    try:
        return _onboarding_service().capture_source(
            tenant_id, workflow_id, body, actor_id=_actor(request)
        )
    except Exception as exc:
        _raise_http(exc)


@app.post(
    "/api/v1/tenants/{tenant_id}/onboarding-workflows/{workflow_id}/facts",
    dependencies=[Depends(require_permission(Permission.PORTFOLIO_WRITE))],
)
def propose_onboarding_fact(tenant_id: str, workflow_id: str, body: FactProposalInput) -> dict:
    try:
        return _onboarding_service().propose_fact(tenant_id, workflow_id, body)
    except Exception as exc:
        _raise_http(exc)


@app.post(
    "/api/v1/tenants/{tenant_id}/onboarding-workflows/{workflow_id}/facts/{fact_id}/decision",
    dependencies=[Depends(require_permission(Permission.PORTFOLIO_APPROVE))],
)
def decide_onboarding_fact(
    tenant_id: str,
    workflow_id: str,
    fact_id: str,
    body: FactDecisionInput,
    request: Request,
) -> dict:
    try:
        return _onboarding_service().decide_fact(
            tenant_id,
            workflow_id,
            fact_id,
            body.model_copy(update={"decided_by": _actor(request, body.decided_by)}),
        )
    except Exception as exc:
        _raise_http(exc)


@app.post(
    "/api/v1/tenants/{tenant_id}/onboarding-workflows/{workflow_id}/approval",
    dependencies=[Depends(require_permission(Permission.PORTFOLIO_APPROVE))],
)
def approve_onboarding(
    tenant_id: str,
    workflow_id: str,
    body: OnboardingApprovalRequest,
    request: Request,
) -> dict:
    try:
        return _onboarding_service().approve(
            tenant_id, workflow_id, approved_by=_actor(request, body.approved_by)
        )
    except Exception as exc:
        _raise_http(exc)


@app.post(
    "/api/v1/tenants/{tenant_id}/connectors",
    dependencies=[Depends(require_permission(Permission.CONNECTOR_WRITE))],
)
def register_connector(tenant_id: str, request: ConnectorInstanceInput) -> dict:
    try:
        return connector_service.register_instance(tenant_id, request).model_dump(mode="json")
    except Exception as exc:
        _raise_http(exc)
        raise AssertionError("unreachable")


@app.get(
    "/api/v1/tenants/{tenant_id}/connectors",
    dependencies=[Depends(require_permission(Permission.CONNECTOR_READ))],
)
def list_connectors(tenant_id: str) -> list[dict]:
    return [item.model_dump(mode="json") for item in connector_service.list_instances(tenant_id)]


@app.post(
    "/api/v1/tenants/{tenant_id}/connectors/{connector_instance_id}/grants",
    dependencies=[Depends(require_permission(Permission.CONNECTOR_APPROVE))],
)
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


@app.get(
    "/api/v1/tenants/{tenant_id}/collection-grants",
    dependencies=[Depends(require_permission(Permission.CONNECTOR_READ))],
)
def list_collection_grants(tenant_id: str, connector_instance_id: str | None = None) -> list[dict]:
    return [
        item.model_dump(mode="json")
        for item in connector_service.list_grants(tenant_id, connector_instance_id)
    ]


@app.post(
    "/api/v1/tenants/{tenant_id}/collection-grants/{grant_id}/revoke",
    dependencies=[Depends(require_permission(Permission.CONNECTOR_APPROVE))],
)
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
            reason=payload.reason,
        ).model_dump(mode="json")
    except Exception as exc:
        _raise_http(exc)
        raise AssertionError("unreachable")


@app.get(
    "/api/v1/tenants/{tenant_id}/connector-runs/{run_id}",
    dependencies=[Depends(require_permission(Permission.CONNECTOR_READ))],
)
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
    "/api/v1/tenants/{tenant_id}/continuous-monitors",
    dependencies=[Depends(require_permission(Permission.CONNECTOR_APPROVE))],
)
def activate_continuous_monitor(
    tenant_id: str, body: MonitorDefinitionInput, request: Request
) -> dict:
    try:
        return _monitoring_service().activate(
            tenant_id,
            body.model_copy(update={"approved_by": _actor(request, body.approved_by)}),
        )
    except Exception as exc:
        _raise_http(exc)


@app.post(
    "/api/v1/tenants/{tenant_id}/continuous-monitors/{monitor_id}/runs",
    dependencies=[Depends(require_permission(Permission.CONTROL_TEST_EXECUTE))],
)
def execute_continuous_monitor(
    tenant_id: str, monitor_id: str, body: MonitorExecutionInput, request: Request
) -> dict:
    try:
        test_request = body.test_request.model_copy(
            update={"requested_by": _actor(request, body.test_request.requested_by)}
        )
        return _monitoring_service().execute(
            tenant_id, monitor_id, body.model_copy(update={"test_request": test_request})
        )
    except Exception as exc:
        _raise_http(exc)


@app.get(
    "/api/v1/tenants/{tenant_id}/continuous-monitors",
    dependencies=[Depends(require_permission(Permission.CONTROL_TEST_READ))],
)
def continuous_monitor_overview(tenant_id: str) -> dict:
    try:
        return _monitoring_service().overview(tenant_id)
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
        return (
            _control_test_service()
            .verify_reproducibility(tenant_id, run_id, domain_request)
            .model_dump(mode="json")
        )
    except Exception as exc:
        _raise_http(exc)


# --- retrieval, the claim graph, and evidence-grounded reporting --------------
#
# There is no endpoint that issues a report without rendering it first, and no
# endpoint that renders one bypassing the material-claim gate. Preparation and
# issuance are separate calls because they are separate acts.


def _reporting_service() -> ReportingService:
    return ReportingService(database, signer=_export_signer)


class PrepareReportRequest(BaseModel):
    request: ReportRequest


class IssueReportRequest(BaseModel):
    reason: str = Field(min_length=10, max_length=4000)
    issued_by: str | None = None


class RecordClaimsRequest(BaseModel):
    claims: list[ClaimInput] = Field(min_length=1)


@app.get(
    "/api/v1/tenants/{tenant_id}/evidence-search",
    dependencies=[Depends(require_permission(Permission.EVIDENCE_READ))],
)
def search_evidence(
    tenant_id: str,
    engagement_id: str | None = Query(default=None),
    query: str | None = Query(default=None),
    limit: int = Query(default=200, ge=1, le=1000),
) -> dict:
    """Access-aware retrieval over canonical evidence.

    Substring matching, deliberately not semantic. A semantic index is a useful
    way to find candidates and a bad thing to let a conclusion rest on, because
    the set it returns is not reproducible. Claims cite explicit ids; this is only
    how a person finds them.
    """
    try:
        results = _reporting_service().retrieve(
            tenant_id=tenant_id, engagement_id=engagement_id, query=query, limit=limit
        )
        return {"evidence": [item.model_dump(mode="json") for item in results]}
    except Exception as exc:
        _raise_http(exc)


@app.post(
    "/api/v1/tenants/{tenant_id}/engagements/{engagement_id}/claims",
    dependencies=[Depends(require_permission(Permission.FINDING_WRITE))],
)
def record_claims(tenant_id: str, engagement_id: str, body: RecordClaimsRequest) -> dict:
    """Persist claims and their evidence links as canonical rows."""
    try:
        created = _reporting_service().record_claims(
            tenant_id=tenant_id, engagement_id=engagement_id, claims=body.claims
        )
        return {"claims": created}
    except Exception as exc:
        _raise_http(exc)


@app.get(
    "/api/v1/tenants/{tenant_id}/evidence/{evidence_id}/usage",
    dependencies=[Depends(require_permission(Permission.EVIDENCE_READ))],
)
def evidence_usage(tenant_id: str, evidence_id: str) -> dict:
    """Every claim this record has been used to support, anywhere."""
    try:
        return {
            "usage": _reporting_service().evidence_usage(
                tenant_id=tenant_id, evidence_id=evidence_id
            )
        }
    except Exception as exc:
        _raise_http(exc)


@app.post(
    "/api/v1/tenants/{tenant_id}/engagements/{engagement_id}/reports/dry-run",
    dependencies=[Depends(require_permission(Permission.REPORT_WRITE))],
)
def dry_run_report(tenant_id: str, engagement_id: str, body: PrepareReportRequest) -> dict:
    """What would stop this report, without producing or storing anything.

    Separate from prepare so that "can this be issued" never has the side effect
    of creating a version.
    """
    try:
        issues = _reporting_service().dry_run(
            tenant_id=tenant_id, engagement_id=engagement_id, request=body.request
        )
        return {
            "renderable": not issues,
            "issues": [item.model_dump(mode="json") for item in issues],
        }
    except Exception as exc:
        _raise_http(exc)


@app.post(
    "/api/v1/tenants/{tenant_id}/engagements/{engagement_id}/reports",
    dependencies=[Depends(require_permission(Permission.REPORT_WRITE))],
)
def prepare_report(tenant_id: str, engagement_id: str, body: PrepareReportRequest) -> dict:
    """Render a report and store it as a draft.

    422 when a material claim is unsupported, with every issue in the detail. The
    request was well formed; what it asked for is a document that would not be
    true.
    """
    try:
        return _reporting_service().prepare(
            tenant_id=tenant_id, engagement_id=engagement_id, request=body.request
        )
    except Exception as exc:
        _raise_http(exc)


@app.post(
    "/api/v1/tenants/{tenant_id}/reports/{report_id}/issuance",
    dependencies=[Depends(require_permission(Permission.REPORT_ISSUE))],
)
def issue_report(
    tenant_id: str, report_id: str, body: IssueReportRequest, request: Request
) -> dict:
    """Issue a prepared report.

    Behind its own permission. A report is the organisation speaking, and issuing
    one is not the same job as writing it.
    """
    try:
        return _reporting_service().issue(
            tenant_id=tenant_id,
            report_id=report_id,
            issued_by=_actor(request, body.issued_by),
            reason=body.reason,
        )
    except Exception as exc:
        _raise_http(exc)


@app.get(
    "/api/v1/tenants/{tenant_id}/reports/{report_id}",
    dependencies=[Depends(require_permission(Permission.REPORT_READ))],
)
def get_report(tenant_id: str, report_id: str) -> dict:
    try:
        return _reporting_service().get(tenant_id=tenant_id, report_id=report_id)
    except Exception as exc:
        _raise_http(exc)


@app.get(
    "/api/v1/tenants/{tenant_id}/reports/{report_id}/verification",
    dependencies=[Depends(require_permission(Permission.REPORT_READ))],
)
def verify_report(tenant_id: str, report_id: str) -> dict:
    """Recompute a stored report's digest, and its signature where a key exists.

    "The report you were sent is the report we issued" is the claim an export
    makes, and a claim nobody can check is not one.
    """
    try:
        public_key = (
            settings.export_signing_public_key.read_bytes()
            if settings.export_signing_public_key
            else None
        )
        return _reporting_service().verify(
            tenant_id=tenant_id, report_id=report_id, public_key_pem=public_key
        )
    except Exception as exc:
        _raise_http(exc)


@app.get(
    "/api/v1/tenants/{tenant_id}/themes",
    dependencies=[Depends(require_permission(Permission.FINDING_READ))],
)
def cross_engagement_themes(tenant_id: str) -> dict:
    """Findings whose code recurs across engagements."""
    try:
        return {"themes": _reporting_service().themes(tenant_id=tenant_id)}
    except Exception as exc:
        _raise_http(exc)


# --- audit universe, risk assessment, and portfolio planning ------------------
#
# Ratings are computed and plans are recommended. Neither becomes official
# without a person, and both endpoints that make one official refuse an
# automated actor in the service beneath them.


def _portfolio_service() -> PortfolioService:
    return PortfolioService(database)


class RegisterEntityRequest(BaseModel):
    entity_type: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=255)
    external_ref: str | None = Field(default=None, max_length=255)
    criticality: float = Field(default=0.0, ge=0, le=5)
    attributes: dict = Field(default_factory=dict)


class RegisterRiskRequest(BaseModel):
    code: str = Field(min_length=2, max_length=64)
    title: str = Field(min_length=3, max_length=255)
    description: str | None = None


class AssessRiskRequest(BaseModel):
    """Declared inputs for a risk rating, and the date to score them as at.

    ``as_at`` is required rather than defaulted to today. Ratings are recomputed
    retrospectively often enough that letting the server pick the date makes
    staleness unmeasurable.
    """

    factors: RiskFactors
    as_at: date
    policy: ScoringPolicy | None = None
    assessed_by: str | None = None


class OfficialRatingRequest(BaseModel):
    rating: Literal["low", "medium", "high", "critical"]
    reason: str = Field(min_length=10, max_length=4000)
    actor_id: str | None = None


class RecordCoverageRequest(BaseModel):
    risk_code: str = Field(min_length=2, max_length=64)
    source: AssuranceSource
    obtained_on: date
    scope_note: str = Field(default="", max_length=2000)
    reference: str | None = Field(default=None, max_length=255)
    engagement_id: str | None = None
    recorded_by: str | None = None


class ProposePlanRequest(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    candidates: list[Candidate] = Field(min_length=1)
    policy: CapacityPolicy
    scenario: str = Field(default="baseline", max_length=64)
    proposed_by: str | None = None


class SimulatePlanRequest(BaseModel):
    candidates: list[Candidate] = Field(min_length=1)
    policy: CapacityPolicy


class ApprovePlanRequest(BaseModel):
    reason: str = Field(min_length=10, max_length=4000)
    approved_by: str | None = None


@app.post(
    "/api/v1/tenants/{tenant_id}/universe/entities",
    dependencies=[Depends(require_permission(Permission.PORTFOLIO_WRITE))],
)
def register_universe_entity(tenant_id: str, body: RegisterEntityRequest) -> dict:
    """Add or update something auditable. Keyed on the external reference."""
    try:
        entity_id = _portfolio_service().register_entity(
            tenant_id=tenant_id,
            entity_type=body.entity_type,
            name=body.name,
            external_ref=body.external_ref,
            criticality=body.criticality,
            attributes=body.attributes,
        )
        return {"entity_id": entity_id, "name": body.name}
    except Exception as exc:
        _raise_http(exc)


@app.post(
    "/api/v1/tenants/{tenant_id}/risks",
    dependencies=[Depends(require_permission(Permission.PORTFOLIO_WRITE))],
)
def register_risk(tenant_id: str, body: RegisterRiskRequest) -> dict:
    try:
        risk_id = _portfolio_service().register_risk(
            tenant_id=tenant_id,
            code=body.code,
            title=body.title,
            description=body.description,
        )
        return {"risk_id": risk_id, "code": body.code}
    except Exception as exc:
        _raise_http(exc)


@app.post(
    "/api/v1/tenants/{tenant_id}/risks/{code}/assessments",
    dependencies=[Depends(require_permission(Permission.PORTFOLIO_WRITE))],
)
def assess_risk(tenant_id: str, code: str, body: AssessRiskRequest, request: Request) -> dict:
    """Score a risk from declared inputs under a versioned policy.

    Behind `portfolio:write` rather than `portfolio:approve`: scoring is
    arithmetic, and an agent is meant to do it. What an agent must not reach is
    the official rating.
    """
    try:
        assessment = _portfolio_service().assess_risk(
            tenant_id=tenant_id,
            risk_code=code,
            factors=body.factors,
            assessed_by=body.assessed_by or _actor(request),
            as_at=body.as_at,
            policy=body.policy,
        )
        return {
            "assessment_id": assessment.assessment_id,
            "version": assessment.version,
            "inherent": assessment.inherent,
            "residual": assessment.residual,
            "rating": assessment.rating,
            "confidence": assessment.confidence,
            "audit_priority": assessment.audit_priority,
            "uncovered": assessment.uncovered,
            "components": dict(assessment.components_json or {}),
            "rationale": assessment.rationale,
        }
    except Exception as exc:
        _raise_http(exc)


@app.post(
    "/api/v1/tenants/{tenant_id}/risks/{code}/official-rating",
    dependencies=[Depends(require_permission(Permission.PORTFOLIO_APPROVE))],
)
def set_official_rating(
    tenant_id: str, code: str, body: OfficialRatingRequest, request: Request
) -> dict:
    """Set aside the computed rating, attributably.

    The computed value stays beside the override so the disagreement remains
    visible in the register rather than being replaced by the preferred number.
    """
    try:
        assessment = _portfolio_service().set_official_rating(
            tenant_id=tenant_id,
            risk_code=code,
            rating=body.rating,
            actor_id=_actor(request, body.actor_id),
            reason=body.reason,
        )
        return {
            "code": code,
            "computed_rating": assessment.rating,
            "official_rating": assessment.official_rating,
            "official_by": assessment.official_by,
        }
    except Exception as exc:
        _raise_http(exc)


@app.get(
    "/api/v1/tenants/{tenant_id}/risks",
    dependencies=[Depends(require_permission(Permission.PORTFOLIO_READ))],
)
def risk_register(tenant_id: str) -> dict:
    """The register, with the computed and official ratings side by side."""
    try:
        return {"risks": _portfolio_service().register_view(tenant_id=tenant_id)}
    except Exception as exc:
        _raise_http(exc)


@app.post(
    "/api/v1/tenants/{tenant_id}/assurance-coverage",
    dependencies=[Depends(require_permission(Permission.PORTFOLIO_WRITE))],
)
def record_assurance_coverage(
    tenant_id: str, body: RecordCoverageRequest, request: Request
) -> dict:
    try:
        coverage_id = _portfolio_service().record_coverage(
            tenant_id=tenant_id,
            risk_code=body.risk_code,
            source=body.source,
            obtained_on=body.obtained_on,
            recorded_by=_actor(request, body.recorded_by),
            scope_note=body.scope_note,
            reference=body.reference,
            engagement_id=body.engagement_id,
        )
        return {"coverage_id": coverage_id, "risk_code": body.risk_code}
    except Exception as exc:
        _raise_http(exc)


@app.post(
    "/api/v1/tenants/{tenant_id}/plan-proposals",
    dependencies=[Depends(require_permission(Permission.PORTFOLIO_WRITE))],
)
def propose_plan(tenant_id: str, body: ProposePlanRequest, request: Request) -> dict:
    """Recommend a plan, and record what it declined to cover."""
    try:
        return _portfolio_service().propose_plan(
            tenant_id=tenant_id,
            name=body.name,
            candidates=body.candidates,
            policy=body.policy,
            proposed_by=body.proposed_by or _actor(request),
            scenario=body.scenario,
        )
    except Exception as exc:
        _raise_http(exc)


@app.post(
    "/api/v1/tenants/{tenant_id}/plan-proposals/simulate",
    dependencies=[Depends(require_permission(Permission.PORTFOLIO_READ))],
)
def simulate_plan(tenant_id: str, body: SimulatePlanRequest) -> dict:
    """Recompute a plan under a hypothetical without recording anything.

    Behind the read permission because it writes nothing. "What stops if we lose
    two people" is a question, and answering it must not create a plan.
    """
    try:
        return _portfolio_service().simulate(candidates=body.candidates, policy=body.policy)
    except Exception as exc:
        _raise_http(exc)


@app.get(
    "/api/v1/tenants/{tenant_id}/plan-proposals/{proposal_id}",
    dependencies=[Depends(require_permission(Permission.PORTFOLIO_READ))],
)
def get_plan_proposal(tenant_id: str, proposal_id: str) -> dict:
    try:
        return _portfolio_service().proposal_view(tenant_id=tenant_id, proposal_id=proposal_id)
    except Exception as exc:
        _raise_http(exc)


@app.post(
    "/api/v1/tenants/{tenant_id}/plan-proposals/{proposal_id}/approval",
    dependencies=[Depends(require_permission(Permission.PORTFOLIO_APPROVE))],
)
def approve_plan(
    tenant_id: str, proposal_id: str, body: ApprovePlanRequest, request: Request
) -> dict:
    """Accept a proposal, and record what accepting it accepted.

    The exclusions become attributed accepted residual. An audit committee that
    accepted a plan accepted what it left out; this makes that retrievable.
    """
    try:
        return _portfolio_service().approve_plan(
            tenant_id=tenant_id,
            proposal_id=proposal_id,
            approved_by=_actor(request, body.approved_by),
            reason=body.reason,
        )
    except Exception as exc:
        _raise_http(exc)


# --- standards, criteria, and Audit Pack compilation --------------------------
#
# Compilation is the only way an engagement gets a task graph from a pack. There
# is no endpoint that accepts a hand-authored workflow *and* claims a pack
# reference, because that combination is how a methodology and the work that ran
# under it drift apart.


class RegisterStandardRequest(BaseModel):
    standard: StandardInput
    criteria: list[CriterionInput] = Field(default_factory=list)


class GrantEntitlementRequest(BaseModel):
    standard_code: str = Field(min_length=2, max_length=64)
    licence_ref: str = Field(min_length=1, max_length=255)
    expires_on: date | None = None
    granted_by: str | None = None


class CrosswalkRequest(BaseModel):
    source_standard: str
    source_version: str
    source_criterion: str
    target_standard: str
    target_version: str
    target_criterion: str
    relation: CrosswalkRelation
    rationale: str = Field(min_length=10, max_length=2000)
    asserted_by: str | None = None


class ApprovePackRequest(BaseModel):
    reason: str = Field(min_length=10, max_length=4000)
    approved_by: str | None = None


class CompileEngagementRequest(BaseModel):
    """Compile an approved pack into an engagement.

    The organisation context is supplied rather than read from wherever it
    happens to live, so the compilation record can state what the graph was a
    function of.
    """

    pack_id: str = Field(min_length=2, max_length=128)
    version: str = Field(min_length=1, max_length=32)
    entity_name: str = Field(min_length=1, max_length=255)
    period_start: date
    period_end: date
    in_scope_systems: list[str] = Field(default_factory=list)
    profile_version: int | None = None
    compiled_by: str | None = None


@app.get(
    "/api/v1/audit-packs",
    dependencies=[Depends(require_permission(Permission.STANDARDS_READ))],
)
def list_audit_packs() -> dict:
    """Every pack the platform admitted, with its digest and its requirements."""
    return {
        "packs": [
            {
                "pack_id": pack.manifest.pack_id,
                "version": pack.manifest.version,
                "package_sha256": pack.package_sha256,
                "objective": pack.manifest.objective,
                "standard": (f"{pack.manifest.standard.code}@{pack.manifest.standard.version}"),
                "entitlement_required": pack.manifest.standard.entitlement_required,
                "procedures": len(pack.manifest.procedures),
                "human_gates": list(pack.manifest.human_gates),
                "requires_control_tests": [
                    str(item) for item in pack.manifest.compatibility.requires_control_tests
                ],
            }
            for pack in audit_pack_registry.list()
        ]
    }


@app.post(
    "/api/v1/audit-packs/{pack_id}/versions/{version}/registration",
    dependencies=[Depends(require_permission(Permission.STANDARDS_WRITE))],
)
def register_audit_pack(pack_id: str, version: str, request: Request) -> dict:
    """Admit a verified pack. Idempotent on the artefact's digest."""
    try:
        pack = audit_pack_registry.get(pack_id, version)
        registration_id = _standards_service().register_pack(
            pack=pack, registered_by=_actor(request)
        )
        return {
            "registration_id": registration_id,
            "pack": pack.reference,
            "package_sha256": pack.package_sha256,
            "status": "registered",
        }
    except Exception as exc:
        _raise_http(exc)


@app.post(
    "/api/v1/audit-packs/{pack_id}/versions/{version}/approval",
    dependencies=[Depends(require_permission(Permission.STANDARDS_APPROVE))],
)
def approve_audit_pack(
    pack_id: str, version: str, body: ApprovePackRequest, request: Request
) -> dict:
    """Release a registered pack for use.

    Registration says the artefact is genuine; approval says the organisation has
    reviewed the methodology. Separated in the permission model as well as in the
    service, because they are different people's jobs.
    """
    try:
        registration_id = _standards_service().approve_pack(
            pack_id=pack_id,
            version=version,
            approved_by=_actor(request, body.approved_by),
            reason=body.reason,
        )
        return {
            "registration_id": registration_id,
            "pack": f"{pack_id}@{version}",
            "status": "approved",
        }
    except Exception as exc:
        _raise_http(exc)


@app.post(
    "/api/v1/tenants/{tenant_id}/engagements/{engagement_id}/compile",
    dependencies=[Depends(require_permission(Permission.ENGAGEMENT_WRITE))],
)
def compile_engagement_from_pack(
    tenant_id: str,
    engagement_id: str,
    body: CompileEngagementRequest,
    request: Request,
) -> dict:
    """Compile an approved Audit Pack into this engagement's task graph."""
    try:
        service = _standards_service()
        context = OrganizationContext(
            tenant_id=tenant_id,
            entity_name=body.entity_name,
            period_start=body.period_start,
            period_end=body.period_end,
            in_scope_systems=body.in_scope_systems,
            # Read from canonical state rather than accepted from the caller. An
            # entitlement a request can assert is not an entitlement.
            entitlements=service.effective_entitlements(tenant_id=tenant_id),
            profile_version=body.profile_version,
        )
        return service.compile_engagement(
            pack_id=body.pack_id,
            version=body.version,
            context=context,
            engagement_id=engagement_id,
            compiled_by=_actor(request, body.compiled_by),
        )
    except Exception as exc:
        _raise_http(exc)


@app.get(
    "/api/v1/tenants/{tenant_id}/engagements/{engagement_id}/provenance",
    dependencies=[Depends(require_permission(Permission.ENGAGEMENT_READ))],
)
def engagement_provenance(tenant_id: str, engagement_id: str) -> dict:
    """What this engagement was compiled from, and against which criteria."""
    try:
        return _standards_service().provenance(tenant_id=tenant_id, engagement_id=engagement_id)
    except Exception as exc:
        _raise_http(exc)


@app.post(
    "/api/v1/standards",
    dependencies=[Depends(require_permission(Permission.STANDARDS_WRITE))],
)
def register_standard(body: RegisterStandardRequest) -> dict:
    """Record a version of a standard and the criteria it contains."""
    try:
        standard_id = _standards_service().register_standard(
            standard=body.standard, criteria=body.criteria
        )
        return {
            "standard_id": standard_id,
            "standard": f"{body.standard.code}@{body.standard.version}",
            "criteria": len(body.criteria),
        }
    except Exception as exc:
        _raise_http(exc)


@app.post(
    "/api/v1/standards/crosswalks",
    dependencies=[Depends(require_permission(Permission.STANDARDS_WRITE))],
)
def add_crosswalk(body: CrosswalkRequest, request: Request) -> dict:
    """Assert a relationship between criteria in two standards."""
    try:
        crosswalk_id = _standards_service().add_crosswalk(
            source=(body.source_standard, body.source_version, body.source_criterion),
            target=(body.target_standard, body.target_version, body.target_criterion),
            crosswalk=CrosswalkInput(
                source_criterion=body.source_criterion,
                target_criterion=body.target_criterion,
                relation=body.relation,
                rationale=body.rationale,
                asserted_by=_actor(request, body.asserted_by),
            ),
        )
        return {"crosswalk_id": crosswalk_id, "relation": body.relation.value}
    except Exception as exc:
        _raise_http(exc)


@app.get(
    "/api/v1/standards/{code}/versions/{version}/criteria/{criterion}/impact",
    dependencies=[Depends(require_permission(Permission.STANDARDS_READ))],
)
def criterion_change_impact(code: str, version: str, criterion: str) -> dict:
    """Everything a revision of this criterion would touch."""
    try:
        return _standards_service().change_impact(
            standard_code=code, standard_version=version, criterion_code=criterion
        )
    except Exception as exc:
        _raise_http(exc)


@app.post(
    "/api/v1/tenants/{tenant_id}/standard-entitlements",
    dependencies=[Depends(require_permission(Permission.STANDARDS_APPROVE))],
)
def grant_standard_entitlement(
    tenant_id: str, body: GrantEntitlementRequest, request: Request
) -> dict:
    """Record a tenant's licence to have a standard's text reproduced."""
    try:
        entitlement_id = _standards_service().grant_entitlement(
            tenant_id=tenant_id,
            standard_code=body.standard_code,
            licence_ref=body.licence_ref,
            granted_by=_actor(request, body.granted_by),
            expires_on=body.expires_on,
        )
        return {"entitlement_id": entitlement_id, "standard_code": body.standard_code}
    except Exception as exc:
        _raise_http(exc)


# --- finding adjudication, remediation, and independent retest ----------------
#
# The lifecycle is exposed as transitions rather than as a mutable status field.
# No endpoint sets a finding's status directly, because the point of the component
# is that reaching `closed_verified` requires having passed the gates rather than
# having claimed to.


def _adjudication_service() -> AdjudicationService:
    return AdjudicationService(database)


@app.post(
    "/api/v1/tenants/{tenant_id}/engagements/{engagement_id}/findings",
    dependencies=[Depends(require_permission(Permission.FINDING_WRITE))],
)
def propose_finding(
    tenant_id: str,
    engagement_id: str,
    body: ProposeFindingRequest,
    request: Request,
) -> dict:
    """Propose a finding from accepted control-test exceptions.

    The skeptic runs before the finding is persisted as proposable, so the reply
    reports whether it survived and what was found against it either way.
    """
    try:
        skeptic = SkepticReviewer(
            approved_exceptions=body.approved_exceptions,
            period_start=body.period_start,
            period_end=body.period_end,
            compensating_controls=body.compensating_controls,
        )
        finding_id, verdict = _adjudication_service().propose(
            tenant_id=tenant_id,
            engagement_id=engagement_id,
            finding=body.finding,
            authored_by=_actor(request, body.authored_by),
            skeptic=skeptic,
            exception_rows=body.exception_rows,
        )
        return {
            "finding_id": finding_id,
            "supported": verdict.supported,
            "rationale": verdict.rationale,
            "contradictions": [item.model_dump(mode="json") for item in verdict.contradictions],
        }
    except Exception as exc:
        _raise_http(exc)


@app.get(
    "/api/v1/tenants/{tenant_id}/findings",
    dependencies=[Depends(require_permission(Permission.FINDING_READ))],
)
def list_findings(tenant_id: str, engagement_id: str | None = Query(default=None)) -> dict:
    try:
        return {
            "findings": _adjudication_service().list_findings(
                tenant_id=tenant_id, engagement_id=engagement_id
            )
        }
    except Exception as exc:
        _raise_http(exc)


@app.get(
    "/api/v1/tenants/{tenant_id}/findings/{finding_id}",
    dependencies=[Depends(require_permission(Permission.FINDING_READ))],
)
def get_finding(tenant_id: str, finding_id: str) -> dict:
    """The finding and every decision, action, and retest attached to it."""
    try:
        return (
            _adjudication_service()
            .view(tenant_id=tenant_id, finding_id=finding_id)
            .model_dump(mode="json")
        )
    except Exception as exc:
        _raise_http(exc)


@app.post(
    "/api/v1/tenants/{tenant_id}/findings/{finding_id}/decisions",
    dependencies=[Depends(require_permission(Permission.FINDING_ADJUDICATE))],
)
def adjudicate_finding(
    tenant_id: str,
    finding_id: str,
    body: AdjudicateFindingRequest,
    request: Request,
) -> dict:
    """Record a human decision on a proposed finding.

    The actor is resolved from the authenticated principal, and the service
    refuses a decision attributable to an automated actor, so this endpoint
    cannot be used to launder an agent's conclusion into an approval.
    """
    try:
        status = _adjudication_service().adjudicate(
            tenant_id=tenant_id,
            request=AdjudicationRequest(
                finding_id=finding_id,
                decision=body.decision,
                actor_id=_actor(request, body.actor_id),
                reason=body.reason,
                idempotency_key=body.idempotency_key,
            ),
        )
        return {"finding_id": finding_id, "status": status.value}
    except Exception as exc:
        _raise_http(exc)


@app.post(
    "/api/v1/tenants/{tenant_id}/findings/{finding_id}/materiality",
    dependencies=[Depends(require_permission(Permission.FINDING_WRITE))],
)
def assess_materiality(
    tenant_id: str,
    finding_id: str,
    body: AssessMaterialityRequest,
    request: Request,
) -> dict:
    """Score whether a finding is material, from measured inputs under a policy.

    Granted with ``findings:write`` rather than ``findings:adjudicate``: this is
    the step an agent is *supposed* to perform. The score may raise the finding's
    severity to the computed floor; lowering it is a separate endpoint that
    requires a person and a reason.
    """
    try:
        assessment = _adjudication_service().assess_materiality(
            tenant_id=tenant_id,
            request=MaterialityRequest(
                finding_id=finding_id,
                inputs=body.inputs,
                policy=body.policy,
                assessed_by=body.assessed_by or _actor(request),
            ),
        )
        return {
            "assessment_id": assessment.assessment_id,
            "finding_id": finding_id,
            "score": assessment.score,
            "material": assessment.material,
            "severity_floor": assessment.severity_floor,
            "policy_id": assessment.policy_id,
            "components": dict(assessment.components_json or {}),
            "rationale": assessment.rationale,
            "content_hash": assessment.content_hash,
        }
    except Exception as exc:
        _raise_http(exc)


@app.post(
    "/api/v1/tenants/{tenant_id}/findings/{finding_id}/severity-override",
    dependencies=[Depends(require_permission(Permission.FINDING_ADJUDICATE))],
)
def override_finding_severity(
    tenant_id: str,
    finding_id: str,
    body: OverrideSeverityRequest,
    request: Request,
) -> dict:
    """Set a severity below the computed materiality floor.

    Behind ``findings:adjudicate`` because talking a finding down is a decision,
    not a computation. The service additionally refuses an automated actor and
    refuses an "override" that does not actually lower the severity.
    """
    try:
        assessment = _adjudication_service().override_severity(
            tenant_id=tenant_id,
            request=SeverityOverrideRequest(
                finding_id=finding_id,
                severity=body.severity,
                actor_id=_actor(request, body.actor_id),
                reason=body.reason,
            ),
        )
        return {
            "finding_id": finding_id,
            "assessment_id": assessment.assessment_id,
            "computed_floor": assessment.severity_floor,
            "override_severity": assessment.override_severity,
            "override_by": assessment.override_by,
        }
    except Exception as exc:
        _raise_http(exc)


@app.post(
    "/api/v1/tenants/{tenant_id}/findings/{finding_id}/quality-review",
    dependencies=[Depends(require_permission(Permission.FINDING_REVIEW))],
)
def review_finding_quality(
    tenant_id: str,
    finding_id: str,
    body: ReviewQualityRequest,
    request: Request,
) -> dict:
    """Run the methodology gate over a finding.

    ``findings:review`` is deliberately a permission of its own. The auditor role
    holds it and the approver role does not, so the two gates cannot be cleared by
    one person through role membership even before the service checks identities.

    A failed review returns 200 with ``passed: false``. The review ran; what it
    found is the payload, not an error.
    """
    try:
        outcome = _adjudication_service().review_quality(
            tenant_id=tenant_id,
            request=QualityReviewRequest(
                finding_id=finding_id,
                reviewer_id=_actor(request, body.reviewer_id),
                notes=body.notes,
            ),
        )
        return {
            "finding_id": finding_id,
            "passed": outcome.passed,
            "reviewer_id": outcome.reviewer_id,
            "content_hash": outcome.content_hash,
            "summary": outcome.summary,
            "checks": [item.model_dump(mode="json") for item in outcome.checks],
        }
    except Exception as exc:
        _raise_http(exc)


@app.post(
    "/api/v1/tenants/{tenant_id}/findings/{finding_id}/disputes",
    dependencies=[Depends(require_permission(Permission.FINDING_DISPUTE))],
)
def raise_finding_dispute(
    tenant_id: str,
    finding_id: str,
    body: RaiseDisputeRequest,
    request: Request,
) -> dict:
    """Record management's contest of a finding.

    A disputed finding cannot move to remediation, so this endpoint stops the
    lifecycle rather than annotating it.
    """
    try:
        dispute_id = _adjudication_service().raise_dispute(
            tenant_id=tenant_id,
            request=DisputeRequest(
                finding_id=finding_id,
                ground=body.ground,
                statement=body.statement,
                raised_by=_actor(request, body.raised_by),
                evidence_ids=body.evidence_ids,
            ),
        )
        return {"dispute_id": dispute_id, "finding_id": finding_id, "status": "open"}
    except Exception as exc:
        _raise_http(exc)


@app.post(
    "/api/v1/tenants/{tenant_id}/disputes/{dispute_id}/resolution",
    dependencies=[Depends(require_permission(Permission.FINDING_ADJUDICATE))],
)
def resolve_finding_dispute(
    tenant_id: str,
    dispute_id: str,
    body: ResolveDisputeRequest,
    request: Request,
) -> dict:
    """Answer a dispute.

    Refused when the resolver raised the dispute or authored the finding. A
    ``modified`` resolution returns the finding to ``proposed`` and voids the
    approval it held, because the text that was approved is about to change.
    """
    try:
        status = _adjudication_service().resolve_dispute(
            tenant_id=tenant_id,
            request=DisputeResolutionRequest(
                dispute_id=dispute_id,
                resolution=body.resolution,
                reason=body.reason,
                resolved_by=_actor(request, body.resolved_by),
            ),
        )
        return {
            "dispute_id": dispute_id,
            "resolution": body.resolution.value,
            "finding_status": status.value,
        }
    except Exception as exc:
        _raise_http(exc)


@app.post(
    "/api/v1/tenants/{tenant_id}/findings/{finding_id}/remediation",
    dependencies=[Depends(require_permission(Permission.REMEDIATION_WRITE))],
)
def open_remediation(tenant_id: str, finding_id: str, body: OpenRemediationRequest) -> dict:
    """Convert an approved finding into a remediation obligation.

    Safe to replay: ``created`` is false when an action was already open, and the
    original action is returned rather than a duplicate.
    """
    try:
        action_id, created = _adjudication_service().open_remediation(
            tenant_id=tenant_id,
            request=RemediationRequest(
                finding_id=finding_id,
                owner_ref=body.owner_ref,
                due_date=body.due_date,
                action_plan=body.action_plan,
                idempotency_key=body.idempotency_key,
                closure_evidence_required=body.closure_evidence_required,
                escalation_policy=body.escalation_policy,
                external_system=body.external_system,
                external_target=body.external_target,
            ),
        )
        return {"action_id": action_id, "created": created}
    except Exception as exc:
        _raise_http(exc)


@app.post(
    "/api/v1/tenants/{tenant_id}/remediation-actions/{action_id}/ticket",
    dependencies=[Depends(require_permission(Permission.REMEDIATION_WRITE))],
)
def sync_remediation_ticket(tenant_id: str, action_id: str) -> dict:
    """File the remediation in its external system, at most once.

    A write adapter is resolved from exactly one active tenant-owned connector.
    Ambiguous or incomplete configuration is refused before any provider call.
    """
    try:
        action = _adjudication_service().get_remediation_action(
            tenant_id=tenant_id, action_id=action_id
        )
        writer = None
        if action["external_system"] != "none":
            instances = ConnectorService(database, vault).list_instances(tenant_id)
            candidates = [
                item
                for item in instances
                if item.status == "active" and item.connector_type == action["external_system"]
            ]
            if len(candidates) != 1:
                raise TicketingError(
                    f"remediation ticketing requires exactly one active "
                    f"{action['external_system']} connector; found {len(candidates)}"
                )
            writer = writer_from_connector(candidates[0])
        return _adjudication_service().sync_remediation_ticket(
            tenant_id=tenant_id, action_id=action_id, writer=writer
        )
    except Exception as exc:
        _raise_http(exc)


@app.post(
    "/api/v1/tenants/{tenant_id}/remediation-actions/{action_id}/closure",
    dependencies=[Depends(require_permission(Permission.REMEDIATION_WRITE))],
)
def submit_closure(
    tenant_id: str,
    action_id: str,
    body: SubmitClosureRequest,
    request: Request,
) -> dict:
    """Record management's assertion that an action is complete.

    An assertion is not a closure. Only an independent retest can close a finding.
    """
    try:
        response_id = _adjudication_service().submit_closure(
            tenant_id=tenant_id,
            submission=ClosureSubmission(
                action_id=action_id,
                response_text=body.response_text,
                submitted_by=_actor(request, body.submitted_by),
                closure_evidence_ids=body.closure_evidence_ids,
                action_plan=body.action_plan,
            ),
        )
        return {"action_id": action_id, "response_id": response_id}
    except Exception as exc:
        _raise_http(exc)


@app.post(
    "/api/v1/tenants/{tenant_id}/remediation-actions/{action_id}/retests",
    dependencies=[Depends(require_permission(Permission.REMEDIATION_WRITE))],
)
def record_retest(
    tenant_id: str,
    action_id: str,
    body: RecordRetestRequest,
    request: Request,
) -> dict:
    """Verify a declared remediation with an independent retest.

    Refused when the retester is the finding's author, the remediation owner, or
    whoever declared it complete.
    """
    try:
        retest_id, status = _adjudication_service().retest(
            tenant_id=tenant_id,
            request=RetestRequest(
                action_id=action_id,
                procedure_ref=body.procedure_ref,
                performed_by=_actor(request, body.performed_by),
                idempotency_key=body.idempotency_key,
                outcome=body.outcome,
                evidence_ids=body.evidence_ids,
                detail=body.detail,
                fresh_evidence_collected_at=body.fresh_evidence_collected_at,
            ),
        )
        return {"retest_id": retest_id, "status": status.value}
    except Exception as exc:
        _raise_http(exc)


@app.get(
    "/api/v1/tenants/{tenant_id}/findings/recurrence/{code}",
    dependencies=[Depends(require_permission(Permission.FINDING_READ))],
)
def finding_recurrence(tenant_id: str, code: str) -> dict:
    """The same control failing across more than one engagement."""
    try:
        match = _adjudication_service().recurrence(tenant_id=tenant_id, code=code)
        return {"code": code, "recurrence": match.model_dump(mode="json") if match else None}
    except Exception as exc:
        _raise_http(exc)


@app.get(
    "/api/v1/tenants/{tenant_id}/cockpit",
    dependencies=[Depends(require_permission(Permission.ENGAGEMENT_READ))],
)
def product_cockpit(tenant_id: str) -> dict:
    """The bounded live read model shared by the product's lifecycle routes."""
    return tenant_cockpit(database, tenant_id)


@app.get(
    "/api/v1/tenants/{tenant_id}/delegation",
    response_model=DelegationResponse,
    dependencies=[Depends(require_permission(Permission.AGENTS_READ))],
)
def product_delegation(tenant_id: str, engagement_id: str | None = None) -> dict:
    """One engagement, as it was actually routed across the specialist fleet.

    The fleet inventory says which agents exist. This says which of them touched
    this piece of work, in what order, and how much of their granted authority
    they used doing it.
    """
    return engagement_delegation(
        database, tenant_id, engagement_id=engagement_id, packages=_registry()
    )


@app.get(
    "/api/v1/tenants/{tenant_id}/economics",
    response_model=EconomicsResponse,
    dependencies=[Depends(require_permission(Permission.ENGAGEMENT_READ))],
)
def product_economics(tenant_id: str, engagement_id: str | None = None) -> dict:
    """What one engagement consumed, and what that costs at published rates.

    Metered from the model-call spans and the signed control-test runs, never
    estimated. The payload names the model that served the tokens separately
    from the model whose price was applied, and carries the sentence a surface
    has to print when the two differ.
    """
    return engagement_economics(database, tenant_id, engagement_id=engagement_id)


@app.get(
    "/api/v1/tenants/{tenant_id}/risk-discovery",
    dependencies=[Depends(require_permission(Permission.ENGAGEMENT_READ))],
)
def product_risk_discovery(tenant_id: str) -> dict:
    """The risk universe derived from the approved company profile.

    A proposal, not a register: nothing here is registered until a person accepts
    it, and the payload marks which ones they already have. Derived by declared
    rules rather than by a model, so an auditor who disagrees with an entry can be
    shown the fact and the rule that put it there.
    """
    return discovered_universe(database, tenant_id)


@app.get(
    "/api/v1/judge/overview",
    response_model=JudgeOverviewResponse,
    dependencies=[Depends(require_permission(Permission.AGENTS_READ))],
)
def judge_overview() -> dict:
    result = evaluator_overview(
        database=database,
        packages=_registry(),
        control_tests=control_test_registry.list(),
        audit_packs=audit_pack_registry.list(),
        repository_root=Path(__file__).resolve().parents[2],
        environment=settings.environment,
        model_mode=settings.model_mode,
        model_name=(
            settings.gemini_model
            if settings.model_mode in {"gemini", "vertex"}
            else os.getenv("ASSURANCEOS_LOCAL_MODEL_NAME", settings.model_mode)
        ),
    )
    qualification = _contract_evaluation()
    result["fleet"]["evaluation"] = qualification
    result["components"].append(
        {
            "name": "Agent Evaluation",
            "status": "operational" if qualification["passed"] else "attention",
            "proof": (
                f"{qualification['passed_cases']}/{qualification['case_count']} release cases · "
                f"{qualification['passed_agents']}/{qualification['agent_count']} agents"
            ),
        }
    )
    return result


@lru_cache(maxsize=1)
def _contract_evaluation() -> dict:
    return (
        AgentEvaluationRunner(repository_root=Path(__file__).resolve().parents[2])
        .run()
        .as_dict(include_agents=False)
    )


@app.get(
    "/api/v1/evaluations/summary",
    response_model=EvaluationSummaryResponse,
    dependencies=[Depends(require_permission(Permission.AGENTS_READ))],
)
def agent_evaluation_summary() -> dict:
    return _contract_evaluation()


@app.get(
    "/api/v1/judge/ground-truth",
    response_model=GroundTruthResponse,
    dependencies=[Depends(require_permission(Permission.AGENTS_READ))],
)
def judge_ground_truth() -> dict:
    return ground_truth(Path(__file__).resolve().parents[2])


@app.post(
    "/api/v1/judge/proofs/prompt-injection",
    response_model=PromptInjectionProofResponse,
    dependencies=[Depends(require_permission(Permission.PROOF_REPLAY))],
)
def replay_prompt_injection() -> dict:
    """Replay the published attack through the guardrail the runtime actually uses.

    Constructed through ``build_model_armor`` rather than ``ModelArmor()`` so that
    a deployment with a Model Armor template configured demonstrates the managed
    Google service here too. Hard-wiring the local guard meant the most-watched
    proof action on the evaluator surface was the one place the managed service
    could never appear.
    """
    root = Path(__file__).resolve().parents[2]
    evidence_path = root / "demo/asteria/sources/confluence/change_management_policy.md"
    armor = build_model_armor()
    result = armor.inspect_context(
        evidence_path.read_text(encoding="utf-8"),
        reference="change_management_policy.md",
    )
    return {
        "source": "change_management_policy.md",
        "screened_by": (
            "google_model_armor+local"
            if isinstance(armor, GoogleManagedModelArmor)
            else "local_deterministic_guardrails"
        ),
        "verdict": result.verdict,
        "tainted": bool(result.findings),
        "instruction_neutralized": bool(
            result.findings
            and result.verdict in {"redact", "block"}
            and result.sanitized_text != evidence_path.read_text(encoding="utf-8")
        ),
        "canonical_state_mutated": False,
        "detectors": [item.as_dict() for item in result.findings],
        "sanitized_sha256": hashlib.sha256(
            (result.sanitized_text or "").encode("utf-8")
        ).hexdigest(),
    }


@app.post(
    "/api/v1/judge/proofs/idempotency",
    response_model=IdempotencyProofResponse,
    dependencies=[Depends(require_permission(Permission.PROOF_REPLAY))],
)
def replay_idempotent_remediation() -> dict:
    """Replay the canonical assurance loop and expose its duplicate-action proofs.

    The replay runs in the tenant the product routes read, so the finding it
    drives to verified closure is the one an evaluator then sees in the register
    rather than a record in a tenant no screen displays.
    """
    result = run_assurance_loop_demo(
        database=database,
        repository_root=Path(__file__).resolve().parents[2],
        tenant_id=TENANT_ID,
        reset=False,
    )
    return {
        "tenant_id": result["tenant_id"],
        "engagement_id": result["engagement_id"],
        "finding_id": result["finding_id"],
        "remediation_action_id": result["remediation_action_id"],
        "remediation_opened_once": result["remediation_opened_once"],
        "external_correlation_key": result["jira_correlation_key"],
        "external_ticket_ref": result["jira_ticket_ref"],
        "external_ticket_filed_once": result["jira_ticket_filed_once"],
        "final_status": result["final_status"],
        "ground_truth_match": result["ground_truth_match"],
    }


@app.get(
    "/api/v1/tenants/{tenant_id}/findings/{finding_id}/detail",
    dependencies=[Depends(require_permission(Permission.FINDING_READ))],
)
def get_finding_detail(tenant_id: str, finding_id: str) -> dict:
    """How the finding was reached: sources, the signed test, and the decisions.

    Separate from the adjudication view, which answers what state the finding is
    in. This one answers why anyone should believe it, and it is the view a
    reviewer needs before deciding.
    """
    result = finding_detail(database, tenant_id, finding_id)
    if result is None:
        raise HTTPException(status_code=404, detail="finding not found")
    return result


@app.get(
    "/api/v1/tenants/{tenant_id}/traces/{trace_id}",
    dependencies=[Depends(require_permission(Permission.AGENTS_READ))],
)
def get_correlated_trace(tenant_id: str, trace_id: str) -> dict:
    result = trace_detail(database, tenant_id, trace_id)
    if result is None:
        raise HTTPException(status_code=404, detail="trace not found")
    return result


@lru_cache(maxsize=1)
def _product_template() -> str:
    """The single-file frontend, read once.

    It is a static asset that embeds its own font, so it is well over 100 KB and
    it cannot change while the process is running. Reading it from disk on every
    page request put a synchronous file read in front of every navigation for no
    benefit whatsoever.
    """
    template = Path("apps/web/judge.html")
    if not template.exists():
        template = Path(__file__).resolve().parents[2] / "apps/web/judge.html"
    return template.read_text(encoding="utf-8")


@app.get("/judge", response_class=HTMLResponse, include_in_schema=False)
def judge_mode() -> str:
    return _product_template()


@app.get("/", response_class=HTMLResponse, include_in_schema=False)
@app.get("/onboarding", response_class=HTMLResponse, include_in_schema=False)
@app.get("/plan-proposals", response_class=HTMLResponse, include_in_schema=False)
@app.get("/audits", response_class=HTMLResponse, include_in_schema=False)
@app.get("/findings", response_class=HTMLResponse, include_in_schema=False)
@app.get("/findings/{finding_id}", response_class=HTMLResponse, include_in_schema=False)
@app.get("/evidence", response_class=HTMLResponse, include_in_schema=False)
@app.get("/standards", response_class=HTMLResponse, include_in_schema=False)
@app.get("/governance", response_class=HTMLResponse, include_in_schema=False)
@app.get("/reporting", response_class=HTMLResponse, include_in_schema=False)
def product_app() -> str:
    return _product_template()
