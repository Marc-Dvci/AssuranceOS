from __future__ import annotations

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError
from sqlalchemy import func, select

from assuranceos.db.models import EvidenceRecord, OrganizationFact, OrganizationProfile, Tenant
from assuranceos.db.repositories import TenantRepository
from assuranceos.db.session import Database
from assuranceos.onboarding import (
    FactDecisionInput,
    FactProposalInput,
    OnboardingError,
    OnboardingService,
    OnboardingStartInput,
    PublicSourceInput,
)
from assuranceos.vault import EvidenceVault


NOW = datetime(2026, 8, 8, 12, tzinfo=timezone.utc)


@pytest.fixture
def service(tmp_path):
    database = Database.from_sqlite_path(tmp_path / "onboarding.db")
    database.create_schema()
    with database.transaction() as session:
        TenantRepository(session).add(Tenant(tenant_id="tnt-a", slug="asteria", name="Asteria"))
    vault = EvidenceVault.local(database, tmp_path / "objects")
    try:
        yield database, OnboardingService(database, vault)
    finally:
        database.dispose()


def test_source_backed_profile_review_preserves_proposal_and_correction(service):
    database, onboarding = service
    started = onboarding.start(
        "tnt-a",
        OnboardingStartInput(
            workflow_key="initial-company-profile",
            company_name="Asteria Systems DemoCo",
            primary_domain="https://Asteria.Example/",
            headquarters_country="fr",
        ),
    )
    assert started["company"]["domain"] == "asteria.example"
    assert started["company"]["headquarters_country"] == "FR"

    snapshot = onboarding.capture_source(
        "tnt-a",
        started["workflow_id"],
        PublicSourceInput(
            source_url="https://asteria.example/company",
            publisher="Asteria Systems DemoCo",
            source_quality="official",
            content="Asteria builds cloud financial workflow software for businesses.",
            retrieved_at=NOW,
            excerpt_locator="main > section.company",
            fetched_under_source_policy=True,
        ),
        actor_id="agent:company-intelligence",
    )
    proposal = onboarding.propose_fact(
        "tnt-a",
        started["workflow_id"],
        FactProposalInput(
            fact_key="industry.primary",
            value="Financial services",
            claim_type="inference",
            snapshot_id=snapshot["snapshot_id"],
            confidence=0.72,
        ),
    )
    decision = onboarding.decide_fact(
        "tnt-a",
        started["workflow_id"],
        proposal["fact_id"],
        FactDecisionInput(
            decision="correct",
            corrected_value="Cloud business software",
            decided_by="company-owner@example.test",
            reason="The company provides software to financial operations teams.",
        ),
    )
    assert decision["fact"]["claim_type"] == "assertion"
    assert decision["fact"]["value"] == "Cloud business software"

    approved = onboarding.approve(
        "tnt-a", started["workflow_id"], approved_by="company-owner@example.test"
    )
    assert approved["status"] == "approved"
    assert approved["readiness"] == {
        "canonical_fact_count": 4,
        "source_snapshot_count": 1,
    }
    with database.read_session() as session:
        profile = session.get(OrganizationProfile, started["profile_id"])
        assert profile.status == "canonical" and profile.canonical_at is not None
        facts = list(
            session.scalars(
                select(OrganizationFact).where(OrganizationFact.profile_id == started["profile_id"])
            )
        )
        assert {item.status for item in facts} == {"accepted", "corrected"}
        assert session.scalar(select(func.count()).select_from(EvidenceRecord)) == 1


def test_search_snippets_and_sensitive_public_inferences_are_refused(service):
    _, onboarding = service
    started = onboarding.start(
        "tnt-a",
        OnboardingStartInput(
            workflow_key="guardrails",
            company_name="Asteria",
            primary_domain="asteria.example",
        ),
    )
    with pytest.raises(ValidationError, match="search snippets"):
        PublicSourceInput(
            source_url="https://search.example/result",
            publisher="Search",
            source_quality="reputable",
            content="A result summary",
            retrieved_at=NOW,
            fetched_under_source_policy=True,
            discovery_snippet=True,
        )
    with pytest.raises(OnboardingError, match="outside the public-intelligence policy"):
        onboarding.propose_fact(
            "tnt-a",
            started["workflow_id"],
            FactProposalInput(
                fact_key="employee.health.conditions",
                value="unknown",
                claim_type="unknown",
            ),
        )


def test_start_is_resumable_by_tenant_workflow_key(service):
    _, onboarding = service
    data = OnboardingStartInput(
        workflow_key="resumable", company_name="Asteria", primary_domain="asteria.example"
    )
    first = onboarding.start("tnt-a", data)
    second = onboarding.start("tnt-a", data)
    assert second["workflow_id"] == first["workflow_id"]
    assert second["state_version"] == first["state_version"]
