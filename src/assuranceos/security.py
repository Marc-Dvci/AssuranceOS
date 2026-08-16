from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from functools import lru_cache
from typing import Annotated, Any, Callable

import jwt
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer


class Permission(StrEnum):
    AGENTS_READ = "agents:read"
    DEMO_OPERATE = "demo:operate"
    # Replaying a published proof is held apart from operating the demonstration.
    # `demo:operate` resets tenants and launches the golden audit, which is an
    # administrator's move because it changes what the next observer sees. The
    # two proof replays change nothing: the prompt-injection replay screens a
    # committed file and returns the verdict, and the idempotency replay exists
    # precisely to demonstrate that running it again opens no second remediation.
    # They are therefore safe to grant to a read-only evaluator, and a control an
    # evaluator cannot exercise is a control they have to take on trust.
    PROOF_REPLAY = "proofs:replay"
    ENGAGEMENT_READ = "engagements:read"
    ENGAGEMENT_WRITE = "engagements:write"
    ENGAGEMENT_APPROVE = "engagements:approve"
    SCHEDULE_READ = "schedules:read"
    SCHEDULE_WRITE = "schedules:write"
    SCHEDULE_APPROVE = "schedules:approve"
    EVIDENCE_READ = "evidence:read"
    EVIDENCE_WRITE = "evidence:write"
    EVIDENCE_ADMIN = "evidence:admin"
    CONNECTOR_READ = "connectors:read"
    CONNECTOR_WRITE = "connectors:write"
    CONNECTOR_APPROVE = "connectors:approve"
    OUTBOX_OPERATE = "outbox:operate"
    TASK_EXECUTE = "tasks:execute"
    CONTROL_TEST_READ = "control-tests:read"
    CONTROL_TEST_EXECUTE = "control-tests:execute"
    FINDING_READ = "findings:read"
    FINDING_WRITE = "findings:write"
    # Deciding a finding is separated from proposing one and granted only to
    # approver and admin. The service already refuses an approval attributed to
    # an automated actor; keeping the same separation in the permission model
    # means the worker role that runs agents cannot reach the endpoint at all.
    FINDING_ADJUDICATE = "findings:adjudicate"
    # The methodology gate is its own permission and is held by the auditor role,
    # never by the approver. Separating them in the permission model means the two
    # gates cannot be cleared by one person through role membership alone, before
    # the service ever compares identities. `worker` does not hold it either: an
    # agent must not be able to pass its own work through review.
    FINDING_REVIEW = "findings:review"
    # Contesting a finding is management's move, not the audit function's. It is
    # separated so a business-owner principal can be granted the ability to push
    # back without being granted the ability to write or decide findings.
    FINDING_DISPUTE = "findings:dispute"
    REMEDIATION_WRITE = "remediation:write"
    STANDARDS_READ = "standards:read"
    STANDARDS_WRITE = "standards:write"
    # Approving an Audit Pack is approving a methodology the organisation will
    # stand behind. Held apart from writing standards for the same reason
    # adjudicating a finding is held apart from writing one.
    STANDARDS_APPROVE = "standards:approve"
    PORTFOLIO_READ = "portfolio:read"
    PORTFOLIO_WRITE = "portfolio:write"
    # Making a risk rating official, or approving an audit plan, is a decision
    # about what the function will and will not look at this year. Held apart
    # from writing them for the same reason adjudicating a finding is held apart
    # from proposing one.
    PORTFOLIO_APPROVE = "portfolio:approve"
    REPORT_READ = "reports:read"
    REPORT_WRITE = "reports:write"
    # Issuing a report is the organisation speaking. Held apart from writing one
    # because they are different jobs, and because the service beneath refuses an
    # automated actor - a separation that only means something if the permission
    # model agrees.
    REPORT_ISSUE = "reports:issue"


ROLE_PERMISSIONS: dict[str, frozenset[Permission]] = {
    "viewer": frozenset(
        {
            Permission.AGENTS_READ,
            Permission.PROOF_REPLAY,
            Permission.ENGAGEMENT_READ,
            Permission.SCHEDULE_READ,
            Permission.EVIDENCE_READ,
            Permission.CONNECTOR_READ,
            Permission.CONTROL_TEST_READ,
            Permission.FINDING_READ,
            Permission.STANDARDS_READ,
            Permission.PORTFOLIO_READ,
            Permission.REPORT_READ,
        }
    ),
    "auditor": frozenset(
        {
            Permission.AGENTS_READ,
            Permission.ENGAGEMENT_READ,
            Permission.ENGAGEMENT_WRITE,
            Permission.SCHEDULE_READ,
            Permission.EVIDENCE_READ,
            Permission.EVIDENCE_WRITE,
            Permission.CONNECTOR_READ,
            Permission.CONNECTOR_WRITE,
            Permission.CONTROL_TEST_READ,
            Permission.CONTROL_TEST_EXECUTE,
            Permission.FINDING_READ,
            Permission.FINDING_WRITE,
            Permission.FINDING_REVIEW,
            Permission.REMEDIATION_WRITE,
            Permission.STANDARDS_READ,
            Permission.STANDARDS_WRITE,
            Permission.PORTFOLIO_READ,
            Permission.PORTFOLIO_WRITE,
            Permission.REPORT_READ,
            Permission.REPORT_WRITE,
        }
    ),
    # Management: may contest a finding and respond to it, and may do nothing else
    # to it. The role exists so the dispute workflow has a principal that is
    # plainly not part of the audit function.
    "business_owner": frozenset(
        {
            Permission.ENGAGEMENT_READ,
            Permission.EVIDENCE_READ,
            Permission.FINDING_READ,
            Permission.FINDING_DISPUTE,
            Permission.REMEDIATION_WRITE,
            Permission.REPORT_READ,
        }
    ),
    "approver": frozenset(
        {
            Permission.AGENTS_READ,
            Permission.ENGAGEMENT_READ,
            Permission.ENGAGEMENT_APPROVE,
            Permission.SCHEDULE_READ,
            Permission.SCHEDULE_APPROVE,
            Permission.EVIDENCE_READ,
            Permission.EVIDENCE_ADMIN,
            Permission.CONNECTOR_READ,
            Permission.CONNECTOR_APPROVE,
            Permission.CONTROL_TEST_READ,
            Permission.FINDING_READ,
            Permission.FINDING_ADJUDICATE,
            Permission.STANDARDS_READ,
            Permission.STANDARDS_APPROVE,
            Permission.PORTFOLIO_READ,
            Permission.PORTFOLIO_APPROVE,
            Permission.REPORT_READ,
            Permission.REPORT_ISSUE,
        }
    ),
    "operator": frozenset(
        {
            Permission.AGENTS_READ,
            Permission.ENGAGEMENT_READ,
            Permission.ENGAGEMENT_WRITE,
            Permission.SCHEDULE_READ,
            Permission.SCHEDULE_WRITE,
            Permission.EVIDENCE_READ,
            Permission.CONNECTOR_READ,
            Permission.OUTBOX_OPERATE,
            Permission.CONTROL_TEST_READ,
            Permission.CONTROL_TEST_EXECUTE,
            Permission.FINDING_READ,
            Permission.STANDARDS_READ,
            Permission.PORTFOLIO_READ,
        }
    ),
    "admin": frozenset(Permission),
    "worker": frozenset(
        {
            Permission.ENGAGEMENT_READ,
            Permission.ENGAGEMENT_WRITE,
            Permission.SCHEDULE_READ,
            Permission.EVIDENCE_READ,
            Permission.EVIDENCE_WRITE,
            Permission.CONNECTOR_READ,
            Permission.CONNECTOR_WRITE,
            Permission.OUTBOX_OPERATE,
            Permission.TASK_EXECUTE,
            Permission.CONTROL_TEST_READ,
            Permission.CONTROL_TEST_EXECUTE,
            Permission.FINDING_READ,
            Permission.FINDING_WRITE,
            Permission.STANDARDS_READ,
            Permission.PORTFOLIO_READ,
            Permission.PORTFOLIO_WRITE,
        }
    ),
}


@dataclass(frozen=True)
class Principal:
    subject: str
    tenant_ids: frozenset[str]
    roles: frozenset[str]
    explicit_permissions: frozenset[Permission] = frozenset()
    token_id: str | None = None

    @property
    def permissions(self) -> frozenset[Permission]:
        permissions = set(self.explicit_permissions)
        for role in self.roles:
            permissions.update(ROLE_PERMISSIONS.get(role, ()))
        return frozenset(permissions)

    def can_access_tenant(self, tenant_id: str) -> bool:
        return "*" in self.tenant_ids or tenant_id in self.tenant_ids

    @classmethod
    def local_system(cls) -> "Principal":
        return cls(subject="local-system", tenant_ids=frozenset({"*"}), roles=frozenset({"admin"}))


class JwtVerifier:
    """Verifies externally issued JWTs and maps stable claims to an AssuranceOS principal.

    The verifier supports either a local HMAC key or an OIDC/JWKS issuer. It never accepts an
    unsigned token and requires expiration, subject, issued-at, and audience claims.
    """

    def __init__(
        self,
        *,
        issuer: str,
        audience: str,
        algorithms: tuple[str, ...],
        secret: str | None = None,
        jwks_url: str | None = None,
        leeway_seconds: int = 30,
    ):
        if not issuer or not audience:
            raise ValueError("JWT issuer and audience are required")
        if bool(secret) == bool(jwks_url):
            raise ValueError("configure exactly one of JWT secret or JWKS URL")
        if not algorithms:
            raise ValueError("at least one JWT algorithm is required")
        if any(algorithm.lower() == "none" for algorithm in algorithms):
            raise ValueError("unsigned JWTs are not allowed")
        if secret:
            if len(secret.encode("utf-8")) < 32:
                raise ValueError("JWT HMAC secret must contain at least 32 bytes")
            if any(not algorithm.upper().startswith("HS") for algorithm in algorithms):
                raise ValueError("JWT HMAC secrets may only be used with HS algorithms")
        if jwks_url and any(algorithm.upper().startswith("HS") for algorithm in algorithms):
            raise ValueError("JWKS verification cannot use HS algorithms")
        self.issuer = issuer
        self.audience = audience
        self.algorithms = algorithms
        self.secret = secret
        self.jwks_url = jwks_url
        self.leeway_seconds = leeway_seconds

    @lru_cache(maxsize=1)
    def _jwk_client(self) -> jwt.PyJWKClient:
        assert self.jwks_url is not None
        return jwt.PyJWKClient(self.jwks_url, cache_keys=True, lifespan=300)

    def verify(self, token: str) -> Principal:
        try:
            key: Any = self.secret
            if self.jwks_url:
                key = self._jwk_client().get_signing_key_from_jwt(token).key
            claims = jwt.decode(
                token,
                key=key,
                algorithms=list(self.algorithms),
                audience=self.audience,
                issuer=self.issuer,
                leeway=self.leeway_seconds,
                options={"require": ["exp", "iat", "sub", "aud", "iss"]},
            )
        except jwt.PyJWTError as exc:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="invalid bearer token",
                headers={"WWW-Authenticate": "Bearer"},
            ) from exc
        return self._principal_from_claims(claims)

    @staticmethod
    def _string_set(value: Any, *, claim: str) -> frozenset[str]:
        if value is None:
            return frozenset()
        if isinstance(value, str):
            values = value.split()
        elif isinstance(value, list) and all(isinstance(item, str) for item in value):
            values = value
        else:
            raise HTTPException(status_code=401, detail=f"invalid {claim} claim")
        return frozenset(item for item in values if item)

    def _principal_from_claims(self, claims: dict[str, Any]) -> Principal:
        subject = claims.get("sub")
        if not isinstance(subject, str) or not subject:
            raise HTTPException(status_code=401, detail="invalid subject claim")
        roles = self._string_set(claims.get("roles"), claim="roles")
        tenant_ids = self._string_set(claims.get("tenant_ids"), claim="tenant_ids")
        if not tenant_ids and "admin" not in roles:
            raise HTTPException(status_code=403, detail="token has no tenant assignment")
        if "admin" in roles and not tenant_ids:
            tenant_ids = frozenset({"*"})
        raw_permissions = self._string_set(claims.get("permissions"), claim="permissions")
        try:
            permissions = frozenset(Permission(item) for item in raw_permissions)
        except ValueError as exc:
            raise HTTPException(status_code=401, detail="token contains an unknown permission") from exc
        token_id = claims.get("jti")
        return Principal(
            subject=subject,
            tenant_ids=tenant_ids,
            roles=roles,
            explicit_permissions=permissions,
            token_id=token_id if isinstance(token_id, str) else None,
        )


_bearer = HTTPBearer(auto_error=False)


def _settings(request: Request) -> Any:
    return request.app.state.settings


def _verifier(request: Request) -> JwtVerifier | None:
    return getattr(request.app.state, "jwt_verifier", None)


async def authenticated_principal(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> Principal:
    settings = _settings(request)
    if settings.auth_mode == "disabled":
        principal = Principal.local_system()
        request.state.principal = principal
        return principal
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="bearer token required",
            headers={"WWW-Authenticate": "Bearer"},
        )
    verifier = _verifier(request)
    if verifier is None:
        raise HTTPException(status_code=503, detail="authentication verifier is not configured")
    principal = verifier.verify(credentials.credentials)
    request.state.principal = principal
    return principal


def require_permission(permission: Permission) -> Callable[..., Principal]:
    async def dependency(
        request: Request,
        principal: Annotated[Principal, Depends(authenticated_principal)],
    ) -> Principal:
        if permission not in principal.permissions:
            raise HTTPException(status_code=403, detail=f"missing permission: {permission.value}")
        tenant_id = request.path_params.get("tenant_id")
        if tenant_id and not principal.can_access_tenant(str(tenant_id)):
            raise HTTPException(status_code=403, detail="tenant access denied")
        return principal

    return dependency


def effective_actor(principal: Principal, requested_actor: str | None = None) -> str:
    """Prevents callers from attributing a decision to another identity.

    A local disabled-auth process retains compatibility with CLI demos. Authenticated requests use
    the verified subject; a supplied actor must match that subject unless the caller is an admin.
    """

    if requested_actor and requested_actor != principal.subject and "admin" not in principal.roles:
        raise HTTPException(status_code=403, detail="actor_id must match the authenticated subject")
    return requested_actor if requested_actor and "admin" in principal.roles else principal.subject
