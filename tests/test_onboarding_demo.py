"""The first-use path: public footprint in, reviewed profile out."""

from __future__ import annotations

from pathlib import Path

import pytest

from assuranceos.db import Database
from assuranceos.db.models import OrganizationProfile, Tenant
from assuranceos.db.repositories import TenantRepository
from assuranceos.onboarding_demo import PUBLIC_SOURCES, run_onboarding_demo
from assuranceos.product import tenant_cockpit
from assuranceos.vault import BaselineContentInspector, EvidenceVault

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def database(tmp_path):
    database = Database.from_sqlite_path(tmp_path / "onboarding-demo.db")
    database.create_schema()
    with database.transaction() as session:
        TenantRepository(session).add(
            Tenant(tenant_id="tnt_onboarding_demo", slug="asteria", name="Asteria Systems")
        )
    return database


@pytest.fixture
def result(database, tmp_path):
    vault = EvidenceVault.local(
        database, tmp_path / "evidence", inspector=BaselineContentInspector()
    )
    return run_onboarding_demo(
        database=database,
        repository_root=ROOT,
        tenant_id="tnt_onboarding_demo",
        vault=vault,
    )


def test_the_profile_is_approved_from_source_backed_facts(result):
    assert result["status"] == "approved"
    assert result["source_snapshots"] == len(PUBLIC_SOURCES)
    assert result["facts_proposed"] == 8
    assert result["readiness"]["source_snapshot_count"] == len(PUBLIC_SOURCES)


def test_the_reviewer_overrules_the_prominent_legal_entity(result):
    """The bold name on the press page is the pre-2021 holding company."""
    assert result["facts_corrected"] == 1
    correction = result["correction"]
    assert correction["decision"] == "correct"
    assert correction["fact"]["value"] == "Asteria Systems SAS"


def test_a_correction_reaches_the_canonical_profile(result, database):
    """A correction the product records and no screen shows is not a correction."""
    with database.read_session() as session:
        profile = session.get(OrganizationProfile, result["profile_id"])
        assert profile.legal_name == "Asteria Systems SAS"
        assert profile.status == "canonical"


def test_the_cockpit_can_name_the_company_it_audits(result, database):
    """Without this the product's front page reads 'Assurance at a glance'."""
    cockpit = tenant_cockpit(database, "tnt_onboarding_demo")

    assert cockpit["organization"]["legal_name"] == "Asteria Systems SAS"
    assert cockpit["onboarding"][0]["status"] == "approved"


def test_inference_is_kept_apart_from_what_a_source_states(result, database):
    """'They run on Google Cloud' is read off a careers page, not stated anywhere."""
    cockpit = tenant_cockpit(database, "tnt_onboarding_demo")
    facts = {item["key"]: item for item in cockpit["organization"]["facts"]}

    assert facts["public.cloud_provider"]["claim_type"] == "inference"
    assert facts["public.industry"]["claim_type"] == "observed"
    assert result["inferences"] == 2


def test_every_researched_fact_resolves_to_a_snapshot(result, database):
    """A fact with no source is refused by the schema, not accepted with a shrug."""
    cockpit = tenant_cockpit(database, "tnt_onboarding_demo")
    researched = [
        item
        for item in cockpit["organization"]["facts"]
        if item["key"].startswith("public.") and item["source_type"] == "public_source"
    ]

    assert researched
    assert all(item["source_ref"] for item in researched)
