"""The service-delivery engagement: a commitment nobody inside the company can see.

Asteria's incident process is healthy by its own measure. Every P1 in July was
answered inside the target on its ticket, and every ticket says "met". The target
on those tickets came from the Jira SLA automation, which was configured from the
incident response plan, which quotes a clause a contract amendment replaced in
April. Three internal systems agree with each other, and all three disagree with
the contract.

Nothing inside the incident process can detect that, because the obligation is
written in a document the incident process has never read. Finding it needs three
source systems joined at once — the ticketing export, the contract register, and
the procedure page — which is exactly the kind of work that does not get done
where there is no audit function to do it.

This module runs that engagement through the same services every other one uses:
the signed SLA-01 procedure in its sandbox, the skeptic's contradiction search,
and a proposed finding that stops at the human gate.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

from .adjudication.definitions import MaterialityRequest, QualityReviewRequest
from .adjudication.materiality import (
    FactorAssertion,
    MaterialityInputs,
    QualitativeFactor,
)
from .adjudication.service import AdjudicationService, finding_from_exceptions
from .adjudication.skeptic import SkepticReviewer
from .control_testing.definitions import ControlTestRunRequest
from .control_testing.demo import build_service
from .corpus import PERIOD_END, PERIOD_START, AsteriaCorpus
from .db.models import Engagement, Tenant
from .db.repositories import TenantRepository
from .db.session import Database

DEMO_TENANT = "tnt_asteria_demo"
SLA_ENGAGEMENT_ID = "eng_asteria_sla_2026h2"
AUDIT_PERIOD = (PERIOD_START, PERIOD_END)

#: What the skeptic is given to argue with. These are the two incidents a naive
#: test would report and should not: one belongs to a customer whose contract was
#: never amended, and one predates the period.
CONSIDERED_AND_REJECTED: list[dict[str, Any]] = [
    {
        "subject_ref": "INC-4413 · Contoso Manufacturing NV",
        "reason": (
            "Answered in 5.75 hours, which breaches the four-hour Northwind target but "
            "not Contoso's. MSA-CT-2025-004 has never been amended and still sets eight "
            "hours, so the response was compliant. Applying one customer's clause to the "
            "whole population would have reported this as a breach."
        ),
    },
    {
        "subject_ref": "INC-4361 · opened 2026-03-18",
        "reason": (
            "Answered in 6.67 hours, outside the four-hour target but before the "
            "amendment took effect on 2026-04-01 and outside the audit period. It is "
            "not in the tested population."
        ),
    },
    {
        "subject_ref": "INC-4407 · Northwind Trading BV",
        "reason": (
            "Answered in 46 minutes. The control did operate correctly here, which is "
            "why the conclusion is that the target is wrong rather than that the team "
            "is slow."
        ),
    },
]


def run_service_delivery_demo(
    *,
    database: Database,
    repository_root: Path,
    tenant_id: str | None = None,
) -> dict[str, Any]:
    """Run SLA-01 over the corpus and propose the finding it supports."""
    tenant = tenant_id or DEMO_TENANT
    root = Path(repository_root)

    with database.transaction() as session:
        repository = TenantRepository(session)
        if repository.get(tenant) is None:
            repository.add(
                Tenant(
                    tenant_id=tenant,
                    slug="asteria",
                    name="Asteria Systems DemoCo",
                    status="active",
                    region="europe-west1",
                )
            )
            session.flush()
        existing = session.get(Engagement, SLA_ENGAGEMENT_ID)
        if existing is not None:
            session.delete(existing)
            session.flush()
        session.add(
            Engagement(
                engagement_id=SLA_ENGAGEMENT_ID,
                tenant_id=tenant,
                code="AST-SLA-2026-H2",
                title="Customer service commitments",
                status="fieldwork",
                audit_pack_ref="service-delivery@1.0.0",
                period_start=AUDIT_PERIOD[0],
                period_end=AUDIT_PERIOD[1],
                scope_json={
                    "customers": ["Northwind Trading BV", "Contoso Manufacturing NV"],
                    "priorities": ["P1"],
                },
            )
        )

    service = build_service(database, root)
    corpus = AsteriaCorpus(root / "demo/asteria")
    datasets = corpus.sla_datasets()
    population = next(item for item in datasets if item.name == "incidents")

    run = service.run(
        tenant,
        ControlTestRunRequest(
            test_id="SLA-01",
            version="1.0.0",
            purpose=(
                "Whether contractual incident response commitments were met in July "
                "2026, and whether the organisation is configured to know"
            ),
            period_start=AUDIT_PERIOD[0],
            period_end=AUDIT_PERIOD[1],
            requested_by="agent:operating-effectiveness",
            idempotency_key="asteria:sla-01:2026-07",
            engagement_id=SLA_ENGAGEMENT_ID,
            parameters={
                "expected_population_count": len(population.records),
                "in_scope_priorities": ["P1"],
            },
            datasets=datasets,
        ),
    )

    exceptions = [item.model_dump(mode="json") for item in run.exceptions]
    breaches = [item for item in exceptions if item["classification"] == "sla_breach"]
    design = next(
        (item for item in exceptions if item["classification"] == "control_design_gap"), None
    )
    evidence_ids = sorted(
        {value for item in exceptions for value in (item.get("evidence_ids") or [])}
    )

    proposed = finding_from_exceptions(
        code="SLA-01",
        title=(
            "Incident response is measured against a service level the contract "
            "replaced in April"
        ),
        severity="high",
        criteria=(
            "MSA-NW-2024-011 amendment 2, effective 2026-04-01, requires a first "
            "response to a Northwind P1 incident within 4 hours, 24x7, with a service "
            "credit of 5% of the monthly fee per breach."
        ),
        risk_statement=(
            "The incident response plan and the Jira SLA automation still carry the "
            "superseded 8-hour target, so P1 responses that breach the contract are "
            "recorded as met and no escalation is raised. Service credits accrue "
            "silently and the breach is first visible to the customer."
        ),
        exceptions=breaches,
        evidence_ids=evidence_ids,
        source_run_id=run.run_id,
        confidence=0.92,
        period=AUDIT_PERIOD,
        limitations=[
            "Three P1 incidents in the period breached the amended target. Two further "
            "P1 incidents were examined and are not reported: one is governed by an "
            "unamended contract and one falls outside the period.",
            "Whether each recorded first response was substantive is not testable from "
            "the ticketing export and was not assessed.",
        ],
    )

    adjudication = AdjudicationService(database)
    finding_id, verdict = adjudication.propose(
        tenant_id=tenant,
        engagement_id=SLA_ENGAGEMENT_ID,
        finding=proposed,
        authored_by="agent:operating-effectiveness",
        skeptic=SkepticReviewer(
            period_start=AUDIT_PERIOD[0],
            period_end=AUDIT_PERIOD[1],
        ),
        exception_rows=breaches,
    )

    # The contradiction search is what makes the difference between a finding
    # that survived scrutiny and one nobody examined, so what it declined to
    # report is written onto the finding rather than left in this script.
    _record_considerations(database, tenant, finding_id)

    # Materiality and quality review are the platform's own gates and it can
    # clear them itself. Leaving them open would put three blockers in front of
    # the reviewer when only one of them is a decision anybody is owed: the
    # human approval.
    adjudication.assess_materiality(
        tenant_id=tenant,
        request=MaterialityRequest(
            finding_id=finding_id,
            inputs=MaterialityInputs(
                population_size=5,
                exception_count=len(breaches),
                monetary_exposure=7200.0,
                factors=[
                    FactorAssertion(
                        factor=QualitativeFactor.CUSTOMER_IMPACT,
                        rationale=(
                            "Three breaches of an executed service level trigger "
                            "service credits of 5% of the monthly fee each."
                        ),
                        evidence_ids=evidence_ids,
                    )
                ],
            ),
            assessed_by="agent:scope-materiality",
        ),
    )
    adjudication.review_quality(
        tenant_id=tenant,
        request=QualityReviewRequest(
            finding_id=finding_id,
            reviewer_id="carol.qa@asteria.example",
            notes=(
                "Contract clause traced to the executed amendment; population "
                "reconciles to five in-period P1 incidents across two customers."
            ),
        ),
    )

    return {
        "tenant_id": tenant,
        "engagement_id": SLA_ENGAGEMENT_ID,
        "run_id": run.run_id,
        "conclusion": run.conclusion,
        "population_count": run.population_count,
        "breaches": [item["subject_ref"] for item in breaches],
        "design_gap": (design or {}).get("reason"),
        "finding_id": finding_id,
        "skeptic_suppressed": [item.exception_key for item in verdict.suppressed]
        if hasattr(verdict, "suppressed")
        else [],
        "considered_and_rejected": [item["subject_ref"] for item in CONSIDERED_AND_REJECTED],
    }


def _record_considerations(database: Database, tenant_id: str, finding_id: str) -> None:
    from .db.models import Finding

    with database.transaction() as session:
        finding = session.get(Finding, finding_id)
        if finding is None or finding.tenant_id != tenant_id:
            return
        finding.contradictions_json = [
            {"subject_ref": item["subject_ref"], "reason": item["reason"]}
            for item in CONSIDERED_AND_REJECTED
        ]
        finding.skeptic_rationale = (
            "Five P1 incidents were opened in the period. Each was matched to the "
            "contract clause in force for that customer on the day it opened, rather "
            "than to a single target applied across the population. Three breached the "
            "clause that governs them; two did not and are not reported."
        )
        finding.cause = (
            "Amendment 2 was executed on 2026-03-11 and no owner was assigned to "
            "propagate it into the incident response plan or the Jira SLA scheme. The "
            "plan's own rule requires that within ten business days of execution."
        )
        finding.consequence = (
            "EUR 7,200 of service credits are due for July 2026 alone, and the same "
            "exposure recurs every month the configuration is left uncorrected."
        )


def sla_period() -> tuple[date, date]:
    return AUDIT_PERIOD
