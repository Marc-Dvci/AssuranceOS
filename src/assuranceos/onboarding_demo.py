"""How the platform learns a company it has never seen.

This is the first act of the product and, until now, the only act with no
demonstration behind it. The service, its schemas, its API routes and its tests
all existed; nothing ever ran them, so the seeded tenant had no organization at
all and the cockpit could not put the company's name on its own front page.

The demonstration ingests the controlled public footprint -- the six pages a
company actually publishes about itself -- and turns them into a reviewed
profile. What matters is not that facts are extracted. It is what happens to each
one:

* every fact carries the snapshot it came from, and a fact with no snapshot is
  refused by the schema rather than accepted with a shrug;
* `claim_type` separates what a source *states* from what the reader *inferred*,
  because those two are not the same kind of thing and only one of them survives
  contact with a contradicting document;
* the press page names two entities. `Asteria Systems Group Ltd` appears in older
  material and is not the contracting entity. The proposal takes the prominent
  name and a human corrects it, on the record, with a reason -- which is the
  visible correction the whole first-use story turns on;
* the profile cannot go canonical while any fact is still proposed. Approval is a
  human act with an owner and a timestamp.

The pages are read from the published corpus rather than fetched over the
network. That boundary is stated plainly in `docs/SUBMISSION.md`: AssuranceOS has
no live public-source collector, and a fixture that pretends otherwise would be
the one dishonest thing in the demonstration.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .db.session import Database
from .onboarding import (
    FactDecisionInput,
    FactProposalInput,
    OnboardingService,
    OnboardingStartInput,
    PublicSourceInput,
)
from .vault import BaselineContentInspector, EvidenceVault

DEMO_TENANT = "tnt_asteria_demo"
WORKFLOW_KEY = "asteria-public-onboarding"
COMPANY_NAME = "Asteria Systems"
PRIMARY_DOMAIN = "asteria-demo.example"
ANALYST = "agent.company_intelligence"
REVIEWER = "usr_demo_audit_lead"

# The controlled public footprint, in the order a reader meets it. `publisher`
# and `source_quality` are recorded because a claim's weight depends on who said
# it: the company's own trust centre is official about its commitments and merely
# reputable about its performance.
PUBLIC_SOURCES: tuple[tuple[str, str, str], ...] = (
    ("corporate_overview.md", "https://asteria-demo.example/company", "official"),
    ("trust_center.md", "https://asteria-demo.example/trust", "official"),
    ("press_legal_entity.md", "https://asteria-demo.example/press", "official"),
    ("careers_engineering.md", "https://asteria-demo.example/careers/engineering", "official"),
    ("sub_processors.md", "https://asteria-demo.example/legal/sub-processors", "official"),
    ("status_page_incidents.json", "https://status.asteria-demo.example/history", "authoritative"),
)


def run_onboarding_demo(
    *,
    database: Database,
    repository_root: Path,
    tenant_id: str | None = None,
    vault: EvidenceVault | None = None,
) -> dict[str, Any]:
    """Ingest the public footprint, propose facts, correct one, approve the profile."""

    tenant = tenant_id or DEMO_TENANT
    evidence_vault = vault or EvidenceVault.local(
        database,
        repository_root / "var" / "evidence",
        inspector=BaselineContentInspector(),
    )
    service = OnboardingService(database, evidence_vault)
    workflow = service.start(
        tenant,
        OnboardingStartInput(
            workflow_key=WORKFLOW_KEY,
            company_name=COMPANY_NAME,
            primary_domain=PRIMARY_DOMAIN,
            headquarters_country="FR",
            industry="B2B SaaS - invoice automation",
        ),
    )
    workflow_id = workflow["workflow_id"]
    retrieved_at = datetime(2026, 7, 1, 9, 0, tzinfo=timezone.utc)

    snapshots: dict[str, str] = {}
    root = repository_root / "demo/asteria/sources/public"
    for filename, url, quality in PUBLIC_SOURCES:
        snapshot = service.capture_source(
            tenant,
            workflow_id,
            PublicSourceInput(
                source_url=url,
                publisher="Asteria Systems DemoCo",
                source_quality=quality,
                content=(root / filename).read_text(encoding="utf-8"),
                mime_type="application/json" if filename.endswith(".json") else "text/markdown",
                retrieved_at=retrieved_at,
                excerpt_locator=filename,
                fetched_under_source_policy=True,
            ),
            actor_id=ANALYST,
        )
        snapshots[filename] = snapshot["snapshot_id"]

    # What the sources state, and what a reader concluded from them, kept apart.
    proposals: tuple[tuple[str, Any, str, str, float], ...] = (
        (
            "public.legal_entity_name",
            "Asteria Systems Group Ltd",
            "observed",
            "press_legal_entity.md",
            0.55,
        ),
        ("public.industry", "Invoice automation SaaS", "observed", "corporate_overview.md", 0.95),
        (
            "public.headquarters_country",
            "FR",
            "observed",
            "press_legal_entity.md",
            0.9,
        ),
        (
            "public.operating_locations",
            ["FR", "DE", "GB", "US"],
            "observed",
            "press_legal_entity.md",
            0.85,
        ),
        (
            "public.cloud_provider",
            "Google Cloud",
            "inference",
            "careers_engineering.md",
            0.8,
        ),
        (
            "public.security_commitments",
            ["ISO/IEC 27001", "SOC 2 Type II"],
            "observed",
            "trust_center.md",
            0.9,
        ),
        (
            "public.processes_personal_data",
            True,
            "inference",
            "sub_processors.md",
            0.75,
        ),
        (
            "public.operates_customer_facing_platform",
            True,
            "observed",
            "status_page_incidents.json",
            0.9,
        ),
    )
    proposed: list[dict[str, Any]] = []
    for key, value, claim_type, source, confidence in proposals:
        proposed.append(
            service.propose_fact(
                tenant,
                workflow_id,
                FactProposalInput(
                    fact_key=key,
                    value=value,
                    claim_type=claim_type,
                    snapshot_id=snapshots[source],
                    confidence=confidence,
                ),
            )
        )

    # The correction. The prominent name on the press page is the historic holding
    # company; the contracting entity is named further down. A reader taking the
    # boldest string on the page gets it wrong, which is exactly why a human
    # decides and the reason is retained.
    decisions: list[dict[str, Any]] = []
    for fact in proposed:
        if fact["fact_key"] == "public.legal_entity_name":
            decisions.append(
                service.decide_fact(
                    tenant,
                    workflow_id,
                    fact["fact_id"],
                    FactDecisionInput(
                        decision="correct",
                        decided_by=REVIEWER,
                        reason=(
                            "The press page names Asteria Systems Group Ltd as the pre-2021 "
                            "holding structure. The contracting entity for customer "
                            "agreements signed after 2021 is Asteria Systems SAS, which is "
                            "the entity the audit universe must be built on."
                        ),
                        corrected_value="Asteria Systems SAS",
                    ),
                )
            )
        else:
            decisions.append(
                service.decide_fact(
                    tenant,
                    workflow_id,
                    fact["fact_id"],
                    FactDecisionInput(
                        decision="accept",
                        decided_by=REVIEWER,
                        reason="Stated by an official company source and consistent across pages.",
                    ),
                )
            )

    approved = service.approve(tenant, workflow_id, approved_by=REVIEWER)
    corrected = [item for item in decisions if item["decision"] == "correct"]
    with database.read_session() as session:
        from .db.models import OrganizationProfile

        profile = session.get(OrganizationProfile, approved["profile_id"])
        legal_name = profile.legal_name if profile else None
    return {
        "tenant_id": tenant,
        "workflow_id": workflow_id,
        "status": approved["status"],
        "profile_id": approved.get("profile_id"),
        "legal_name": legal_name,
        "source_snapshots": len(snapshots),
        "facts_proposed": len(proposed),
        "facts_corrected": len(corrected),
        "correction": corrected[0] if corrected else None,
        "inferences": sum(1 for _, _, kind, _, _ in proposals if kind == "inference"),
        "readiness": approved.get("readiness"),
    }
