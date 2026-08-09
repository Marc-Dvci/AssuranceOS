"""Durable organization onboarding with source-backed, reviewable company facts."""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Any, Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, Field, field_validator, model_validator
from sqlalchemy import func, select

from .db.models import (
    OnboardingWorkflow,
    OrganizationFact,
    OrganizationFactDecision,
    OrganizationProfile,
    PublicSourceSnapshot,
    Tenant,
)
from .db.repositories import new_id
from .db.session import Database
from .vault import EvidenceVault


class OnboardingError(ValueError):
    pass


def normalize_domain(value: str | None) -> str | None:
    if value is None or not value.strip():
        return None
    candidate = value.strip().lower().rstrip(".")
    if "://" in candidate:
        parsed = urlsplit(candidate)
        if parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
            raise ValueError("primary domain must not contain a path, query, or fragment")
        candidate = parsed.hostname or ""
    if "/" in candidate or "@" in candidate or "." not in candidate:
        raise ValueError("primary domain must be a valid registrable hostname")
    try:
        return candidate.encode("idna").decode("ascii")
    except UnicodeError as exc:
        raise ValueError("primary domain is invalid") from exc


class OnboardingStartInput(BaseModel):
    workflow_key: str = Field(min_length=3, max_length=128)
    company_name: str = Field(min_length=2, max_length=255)
    primary_domain: str | None = None
    headquarters_country: str | None = Field(default=None, min_length=2, max_length=2)
    industry: str | None = Field(default=None, max_length=128)

    @field_validator("primary_domain")
    @classmethod
    def domain_is_normalized(cls, value):
        return normalize_domain(value)

    @field_validator("headquarters_country")
    @classmethod
    def uppercase_country(cls, value):
        return value.upper() if value else None


class PublicSourceInput(BaseModel):
    source_url: str = Field(min_length=8, max_length=4000)
    publisher: str = Field(min_length=1, max_length=255)
    source_quality: Literal["official", "authoritative", "reputable"]
    content: str = Field(min_length=1, max_length=2_000_000)
    mime_type: str = Field(default="text/html", max_length=128)
    retrieved_at: datetime
    effective_at: datetime | None = None
    excerpt_locator: str | None = Field(default=None, max_length=512)
    fetched_under_source_policy: bool
    discovery_snippet: bool = False

    @model_validator(mode="after")
    def canonical_underlying_source(self):
        parsed = urlsplit(self.source_url)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
            raise ValueError("public sources require a credential-free HTTPS URL")
        if parsed.fragment:
            raise ValueError("source URL must not contain a fragment")
        if self.discovery_snippet:
            raise ValueError("search snippets are discovery aids, not source snapshots")
        if not self.fetched_under_source_policy:
            raise ValueError("source was not fetched under the approved source policy")
        if self.retrieved_at.tzinfo is None or (
            self.effective_at is not None and self.effective_at.tzinfo is None
        ):
            raise ValueError("source timestamps must include a timezone")
        return self


class FactProposalInput(BaseModel):
    fact_key: str = Field(pattern=r"^[a-z][a-z0-9_.-]{1,159}$")
    value: Any
    claim_type: Literal["observed", "proposed", "inference", "assertion", "unknown"]
    snapshot_id: str | None = None
    confidence: float | None = Field(default=None, ge=0, le=1)

    @model_validator(mode="after")
    def evidence_for_positive_claims(self):
        if self.claim_type != "unknown" and not self.snapshot_id:
            raise ValueError("non-unknown public fact proposals require a source snapshot")
        return self


class FactDecisionInput(BaseModel):
    decision: Literal["accept", "correct", "not_applicable"]
    decided_by: str = Field(min_length=1, max_length=255)
    reason: str = Field(min_length=3, max_length=4000)
    corrected_value: Any | None = None

    @model_validator(mode="after")
    def correction_has_value(self):
        if self.decision == "correct" and self.corrected_value is None:
            raise ValueError("a correction requires corrected_value")
        return self


class OnboardingService:
    _PROHIBITED_FACTS = (
        "employee.health",
        "employee.ethnicity",
        "employee.religion",
        "misconduct",
        "solvency",
        "legal_violation",
    )

    def __init__(self, database: Database, vault: EvidenceVault):
        self.database = database
        self.vault = vault

    def start(self, tenant_id: str, data: OnboardingStartInput) -> dict[str, Any]:
        now = datetime.now(timezone.utc)
        with self.database.transaction() as session:
            if session.get(Tenant, tenant_id) is None:
                raise OnboardingError(f"tenant not found: {tenant_id}")
            existing = session.scalar(
                select(OnboardingWorkflow).where(
                    OnboardingWorkflow.tenant_id == tenant_id,
                    OnboardingWorkflow.workflow_key == data.workflow_key,
                )
            )
            if existing is not None:
                return self._view(session, existing)
            version = (
                int(
                    session.scalar(
                        select(func.max(OrganizationProfile.version)).where(
                            OrganizationProfile.tenant_id == tenant_id
                        )
                    )
                    or 0
                )
                + 1
            )
            profile = OrganizationProfile(
                profile_id=new_id("org"),
                tenant_id=tenant_id,
                version=version,
                status="draft",
                legal_name=data.company_name,
                primary_domain=data.primary_domain,
                headquarters_country=data.headquarters_country,
                industry=data.industry,
                created_at=now,
                updated_at=now,
            )
            session.add(profile)
            session.flush()
            workflow = OnboardingWorkflow(
                workflow_id=new_id("onb"),
                tenant_id=tenant_id,
                workflow_key=data.workflow_key,
                status="researching",
                company_name=data.company_name,
                normalized_domain=data.primary_domain,
                headquarters_country=data.headquarters_country,
                industry_hint=data.industry,
                profile_id=profile.profile_id,
                remaining_unknowns_json=[],
                readiness_json={},
                created_at=now,
                updated_at=now,
            )
            session.add(workflow)
            session.flush()
            for key, value in (
                ("legal_identity.legal_name", data.company_name),
                ("legal_identity.primary_domain", data.primary_domain),
                ("legal_identity.headquarters_country", data.headquarters_country),
                ("industry.primary", data.industry),
            ):
                if value is not None:
                    session.add(
                        OrganizationFact(
                            fact_id=new_id("fact"),
                            tenant_id=tenant_id,
                            profile_id=profile.profile_id,
                            fact_key=key,
                            value_json=value,
                            claim_type="assertion",
                            source_type="onboarding_input",
                            source_ref=workflow.workflow_id,
                            confidence=None,
                            status="accepted",
                            created_at=now,
                        )
                    )
            session.flush()
            return self._view(session, workflow)

    def capture_source(
        self, tenant_id: str, workflow_id: str, data: PublicSourceInput, *, actor_id: str
    ) -> dict[str, Any]:
        workflow = self._require_workflow(tenant_id, workflow_id)
        payload = data.content.encode("utf-8")
        digest = hashlib.sha256(payload).hexdigest()
        evidence = self.vault.ingest_bytes(
            tenant_id=tenant_id,
            payload=payload,
            source_type="public_web",
            source_locator=data.source_url,
            actor_id=actor_id,
            acquisition_key=f"public-source:{hashlib.sha256(data.source_url.encode()).hexdigest()}:{digest}",
            original_filename=f"public-source-{digest[:12]}.txt",
            mime_type=data.mime_type,
            classification="public",
            source_time=data.effective_at or data.retrieved_at,
            accepted=False,
            metadata={
                "publisher": data.publisher,
                "source_quality": data.source_quality,
                "workflow_id": workflow_id,
            },
        )
        with self.database.transaction() as session:
            existing = session.scalar(
                select(PublicSourceSnapshot).where(
                    PublicSourceSnapshot.tenant_id == tenant_id,
                    PublicSourceSnapshot.content_sha256 == digest,
                    PublicSourceSnapshot.source_url == data.source_url,
                )
            )
            if existing is None:
                existing = PublicSourceSnapshot(
                    snapshot_id=new_id("src"),
                    tenant_id=tenant_id,
                    workflow_id=workflow_id,
                    source_url=data.source_url,
                    publisher=data.publisher,
                    source_quality=data.source_quality,
                    evidence_id=evidence.evidence_id,
                    content_sha256=digest,
                    retrieved_at=data.retrieved_at,
                    effective_at=data.effective_at,
                    excerpt_locator=data.excerpt_locator,
                    metadata_json={
                        "mime_type": data.mime_type,
                        "fetched_under_source_policy": True,
                    },
                )
                session.add(existing)
                session.flush()
            row = session.get(OnboardingWorkflow, workflow.workflow_id)
            row.status = "profile_review"
            row.state_version += 1
            return self._snapshot_view(existing)

    def propose_fact(
        self, tenant_id: str, workflow_id: str, data: FactProposalInput
    ) -> dict[str, Any]:
        if any(data.fact_key.startswith(prefix) for prefix in self._PROHIBITED_FACTS):
            raise OnboardingError("fact category is outside the public-intelligence policy")
        with self.database.transaction() as session:
            workflow = self._workflow_in_session(session, tenant_id, workflow_id)
            snapshot = None
            if data.snapshot_id:
                snapshot = session.scalar(
                    select(PublicSourceSnapshot).where(
                        PublicSourceSnapshot.tenant_id == tenant_id,
                        PublicSourceSnapshot.workflow_id == workflow_id,
                        PublicSourceSnapshot.snapshot_id == data.snapshot_id,
                    )
                )
                if snapshot is None:
                    raise OnboardingError("source snapshot does not belong to this workflow")
            existing = session.scalar(
                select(OrganizationFact).where(
                    OrganizationFact.profile_id == workflow.profile_id,
                    OrganizationFact.fact_key == data.fact_key,
                )
            )
            if existing is not None:
                raise OnboardingError(f"fact {data.fact_key!r} already exists in this profile")
            fact = OrganizationFact(
                fact_id=new_id("fact"),
                tenant_id=tenant_id,
                profile_id=workflow.profile_id,
                fact_key=data.fact_key,
                value_json=data.value,
                claim_type=data.claim_type,
                source_type="public_source" if snapshot else "unresolved",
                source_ref=snapshot.evidence_id if snapshot else None,
                confidence=data.confidence,
                status="proposed",
                created_at=datetime.now(timezone.utc),
            )
            session.add(fact)
            workflow.status = "profile_review"
            workflow.state_version += 1
            session.flush()
            return self._fact_view(fact)

    def decide_fact(
        self, tenant_id: str, workflow_id: str, fact_id: str, data: FactDecisionInput
    ) -> dict[str, Any]:
        now = datetime.now(timezone.utc)
        with self.database.transaction() as session:
            workflow = self._workflow_in_session(session, tenant_id, workflow_id)
            fact = session.scalar(
                select(OrganizationFact).where(
                    OrganizationFact.tenant_id == tenant_id,
                    OrganizationFact.profile_id == workflow.profile_id,
                    OrganizationFact.fact_id == fact_id,
                )
            )
            if fact is None or fact.status != "proposed":
                raise OnboardingError("reviewable proposed fact not found")
            correction = None
            original = {
                "fact_key": fact.fact_key,
                "value": fact.value_json,
                "claim_type": fact.claim_type,
                "source_ref": fact.source_ref,
            }
            if data.decision == "accept":
                fact.status = "accepted"
            elif data.decision == "not_applicable":
                fact.status = "not_applicable"
            else:
                canonical_key = fact.fact_key
                fact.fact_key = f"{canonical_key[:120]}.proposal.{fact.fact_id[-12:]}"
                fact.status = "corrected"
                correction = OrganizationFact(
                    fact_id=new_id("fact"),
                    tenant_id=tenant_id,
                    profile_id=workflow.profile_id,
                    fact_key=canonical_key,
                    value_json=data.corrected_value,
                    claim_type="assertion",
                    source_type="user_correction",
                    source_ref=fact.fact_id,
                    confidence=None,
                    status="accepted",
                    created_at=now,
                )
                session.add(correction)
                session.flush()
            decision = OrganizationFactDecision(
                decision_id=new_id("fdc"),
                tenant_id=tenant_id,
                workflow_id=workflow_id,
                fact_id=fact.fact_id,
                decision=data.decision,
                decided_by=data.decided_by,
                reason=data.reason,
                correction_fact_id=correction.fact_id if correction else None,
                decided_at=now,
                details_json={"original_proposal": original},
            )
            session.add(decision)
            workflow.state_version += 1
            remaining = int(
                session.scalar(
                    select(func.count())
                    .select_from(OrganizationFact)
                    .where(
                        OrganizationFact.profile_id == workflow.profile_id,
                        OrganizationFact.status == "proposed",
                    )
                )
                or 0
            )
            workflow.status = "ready" if remaining == 0 else "profile_review"
            self._promote_to_profile(session, workflow, correction or fact)
            session.flush()
            return {
                "decision_id": decision.decision_id,
                "decision": decision.decision,
                "fact": self._fact_view(correction or fact),
                "profile_ready": remaining == 0,
            }

    # A reviewed fact that identifies the company has to reach the canonical
    # profile, or the correction is real and invisible: the record shows the
    # reviewer renaming the entity while every screen keeps the name the reviewer
    # rejected. The map is explicit and small on purpose -- promoting on a naming
    # convention would let any researched key overwrite canonical identity.
    _PROFILE_COLUMNS: dict[str, str] = {
        "public.legal_entity_name": "legal_name",
        "public.headquarters_country": "headquarters_country",
        "public.industry": "industry",
    }

    @classmethod
    def _promote_to_profile(cls, session, workflow, fact) -> None:
        column = cls._PROFILE_COLUMNS.get(fact.fact_key)
        if column is None or fact.status != "accepted":
            return
        value = fact.value_json
        if not isinstance(value, str) or not value.strip():
            return
        profile = session.get(OrganizationProfile, workflow.profile_id)
        if profile is not None:
            setattr(profile, column, value.strip())
            profile.updated_at = datetime.now(timezone.utc)

    def approve(self, tenant_id: str, workflow_id: str, *, approved_by: str) -> dict[str, Any]:
        now = datetime.now(timezone.utc)
        with self.database.transaction() as session:
            workflow = self._workflow_in_session(session, tenant_id, workflow_id)
            pending = int(
                session.scalar(
                    select(func.count())
                    .select_from(OrganizationFact)
                    .where(
                        OrganizationFact.profile_id == workflow.profile_id,
                        OrganizationFact.status == "proposed",
                    )
                )
                or 0
            )
            if pending:
                raise OnboardingError(f"profile has {pending} fact(s) awaiting review")
            profile = session.get(OrganizationProfile, workflow.profile_id)
            accepted = int(
                session.scalar(
                    select(func.count())
                    .select_from(OrganizationFact)
                    .where(
                        OrganizationFact.profile_id == profile.profile_id,
                        OrganizationFact.status == "accepted",
                    )
                )
                or 0
            )
            if not profile.legal_name or accepted < 1:
                raise OnboardingError("profile does not meet canonical readiness requirements")
            profile.status = "canonical"
            profile.canonical_at = now
            workflow.status = "approved"
            workflow.approved_by = approved_by
            workflow.approved_at = now
            workflow.state_version += 1
            workflow.readiness_json = {
                "canonical_fact_count": accepted,
                "source_snapshot_count": int(
                    session.scalar(
                        select(func.count())
                        .select_from(PublicSourceSnapshot)
                        .where(PublicSourceSnapshot.workflow_id == workflow_id)
                    )
                    or 0
                ),
            }
            return self._view(session, workflow)

    def get(self, tenant_id: str, workflow_id: str) -> dict[str, Any]:
        with self.database.read_session() as session:
            return self._view(session, self._workflow_in_session(session, tenant_id, workflow_id))

    def _require_workflow(self, tenant_id, workflow_id):
        with self.database.read_session() as session:
            return self._workflow_in_session(session, tenant_id, workflow_id)

    @staticmethod
    def _workflow_in_session(session, tenant_id, workflow_id):
        row = session.scalar(
            select(OnboardingWorkflow).where(
                OnboardingWorkflow.tenant_id == tenant_id,
                OnboardingWorkflow.workflow_id == workflow_id,
            )
        )
        if row is None:
            raise OnboardingError("onboarding workflow not found")
        return row

    def _view(self, session, row):
        facts = list(
            session.scalars(
                select(OrganizationFact)
                .where(OrganizationFact.profile_id == row.profile_id)
                .order_by(OrganizationFact.fact_key)
            )
        )
        sources = list(
            session.scalars(
                select(PublicSourceSnapshot).where(
                    PublicSourceSnapshot.workflow_id == row.workflow_id
                )
            )
        )
        return {
            "workflow_id": row.workflow_id,
            "workflow_key": row.workflow_key,
            "status": row.status,
            "state_version": row.state_version,
            "company": {
                "name": row.company_name,
                "domain": row.normalized_domain,
                "headquarters_country": row.headquarters_country,
                "industry_hint": row.industry_hint,
            },
            "profile_id": row.profile_id,
            "facts": [self._fact_view(item) for item in facts],
            "source_snapshots": [self._snapshot_view(item) for item in sources],
            "readiness": row.readiness_json,
            "approved_by": row.approved_by,
        }

    @staticmethod
    def _fact_view(row):
        return {
            "fact_id": row.fact_id,
            "fact_key": row.fact_key,
            "value": row.value_json,
            "claim_type": row.claim_type,
            "source_type": row.source_type,
            "source_ref": row.source_ref,
            "confidence": row.confidence,
            "status": row.status,
        }

    @staticmethod
    def _snapshot_view(row):
        return {
            "snapshot_id": row.snapshot_id,
            "source_url": row.source_url,
            "publisher": row.publisher,
            "source_quality": row.source_quality,
            "evidence_id": row.evidence_id,
            "content_sha256": row.content_sha256,
            "retrieved_at": row.retrieved_at.isoformat(),
            "excerpt_locator": row.excerpt_locator,
        }
