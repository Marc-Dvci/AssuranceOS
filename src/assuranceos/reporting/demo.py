"""Issuing a report that cannot contain an unsupported sentence.

The demonstration this component owes is a refusal. So the run below assembles a
realistic engagement report and tries to render it **six times**, each with one
defect that a conventional reporting layer would happily publish:

* a material conclusion citing nothing;
* a conclusion whose only support was never accepted into the vault;
* a conclusion resting solely on evidence a guardrail flagged as tainted;
* a conclusion citing evidence collected for a different engagement;
* a conclusion citing evidence dated outside the audit period;
* a conclusion with a linked contradiction that no limitation discloses.

Each refusal names the claim, the code, and the reason. Then the same report is
rendered with the defects resolved — the contradiction disclosed rather than
deleted, the reuse justified rather than hidden — issued by a person, and verified
by recomputing its digest.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any

from ..db.models import Engagement, EvidenceRecord, Tenant
from ..db.repositories import AuditEventRepository, TenantRepository
from ..db.session import Database
from .definitions import (
    ClaimInput,
    ClaimType,
    EvidencePolicy,
    ReportRequest,
    ReportSection,
    ReportTemplate,
    ReportType,
    ReuseJustification,
)
from .exceptions import ReportingError
from .service import ReportingService

DEMO_TENANT = "tnt_asteria"
ENGAGEMENT = "eng_asteria_scm_report"
OTHER_ENGAGEMENT = "eng_asteria_prior_year"
PERIOD = (date(2026, 7, 1), date(2026, 7, 31))
AS_AT = date(2026, 9, 15)

TEMPLATE = ReportTemplate(
    template_id="engagement-report",
    version="1.0.0",
    report_type=ReportType.ENGAGEMENT_REPORT,
    title="Software change management - engagement report",
    sections=[
        ReportSection(
            key="scope",
            heading="Scope and approach",
            claim_keys=["scope-statement", "population-limitation"],
        ),
        ReportSection(
            key="findings",
            heading="Findings",
            claim_keys=["scm-01-conclusion"],
        ),
        ReportSection(
            key="limitations",
            heading="Limitations",
            claim_keys=["population-limitation"],
        ),
    ],
    required_sections=["findings"],
)


def run_reporting_demo(*, database: Database) -> dict[str, Any]:
    """Try to publish six unsupportable reports, then publish a supportable one."""
    _reset_and_seed(database)
    service = ReportingService(database)

    def attempt(claim: ClaimInput, **overrides: Any) -> str:
        request = _request([_scope_claim(), claim, _limitation_claim()], **overrides)
        issues = service.dry_run(
            tenant_id=DEMO_TENANT, engagement_id=ENGAGEMENT, request=request
        )
        return sorted({item.code for item in issues})

    refusals = {
        "cites_nothing": attempt(
            _conclusion(supporting_evidence_ids=[]),
        ),
        "evidence_never_accepted": attempt(
            _conclusion(supporting_evidence_ids=["ev_unaccepted"]),
        ),
        "tainted_sole_support": attempt(
            _conclusion(supporting_evidence_ids=["ev_policy_tainted"]),
        ),
        "cross_engagement_reuse": attempt(
            _conclusion(supporting_evidence_ids=["ev_prior_year"]),
        ),
        "out_of_period_evidence": attempt(
            _conclusion(supporting_evidence_ids=["ev_august_merges"]),
        ),
        "undisclosed_contradiction": attempt(
            _conclusion(
                supporting_evidence_ids=["ev_pr_1002"],
                contradicting_evidence_ids=["ev_exception_register"],
            ),
        ),
    }

    # The same report, with the defects resolved rather than removed. The
    # contradiction is disclosed; the reuse is justified; the tainted policy
    # document still appears, alongside evidence that is not tainted.
    good_claims = [
        _scope_claim(),
        _conclusion(
            supporting_evidence_ids=["ev_pr_1002", "ev_policy_tainted"],
            contradicting_evidence_ids=["ev_exception_register"],
            limitations=[
                "Two of the three exceptions raised by SCM-01 are explained by canonical "
                "records - an approved exception and a merge outside the period - and are "
                "not reported as findings.",
            ],
        ),
        _limitation_claim(),
        # A justification permits the *reuse*; it does not make year-old evidence
        # current. Staleness is a separate condition and needs a limitation, which
        # is the distinction between "we may use this" and "here is how old it is".
        ClaimInput(
            key="prior-year-comparison",
            claim_type=ClaimType.CONTEXT,
            statement=(
                "The same control was tested in the prior year and reached an effective "
                "conclusion."
            ),
            material=True,
            supporting_evidence_ids=["ev_prior_year"],
            limitations=[
                "The prior-year comparison rests on evidence collected in August 2025 and "
                "is stated as history, not as current assurance.",
            ],
        ),
    ]
    request = _request(
        good_claims,
        sections_override=[
            ReportSection(
                key="scope",
                heading="Scope and approach",
                claim_keys=["scope-statement", "prior-year-comparison"],
            ),
            ReportSection(key="findings", heading="Findings", claim_keys=["scm-01-conclusion"]),
            ReportSection(
                key="limitations",
                heading="Limitations",
                claim_keys=["population-limitation"],
            ),
        ],
        reuse_justifications=[
            ReuseJustification(
                evidence_id="ev_prior_year",
                rationale=(
                    "Used only to state the prior-year conclusion as context; it supports "
                    "no conclusion about the current period."
                ),
                approved_by="alice.auditor@asteria.example",
            )
        ],
    )
    remaining = service.dry_run(
        tenant_id=DEMO_TENANT, engagement_id=ENGAGEMENT, request=request
    )
    service.record_claims(
        tenant_id=DEMO_TENANT, engagement_id=ENGAGEMENT, claims=good_claims
    )
    prepared = service.prepare(
        tenant_id=DEMO_TENANT, engagement_id=ENGAGEMENT, request=request
    )

    agent_issuance = _refusal(
        lambda: service.issue(
            tenant_id=DEMO_TENANT,
            report_id=prepared["report_id"],
            issued_by="agent:engagement-director",
            reason="Issued automatically.",
        ),
        ReportingError,
    )
    issued = service.issue(
        tenant_id=DEMO_TENANT,
        report_id=prepared["report_id"],
        issued_by="dana.director@asteria.example",
        reason="Reviewed against the engagement file and issued to the audit committee.",
    )
    verification = service.verify(tenant_id=DEMO_TENANT, report_id=prepared["report_id"])
    tampered = _tamper_and_verify(service, database, prepared["report_id"])

    usage = service.evidence_usage(tenant_id=DEMO_TENANT, evidence_id="ev_pr_1002")
    with database.read_session() as session:
        events = AuditEventRepository(session).list(DEMO_TENANT, ENGAGEMENT)

    document = prepared["document"]
    return {
        "tenant_id": DEMO_TENANT,
        "engagement_id": ENGAGEMENT,
        "refusals": refusals,
        # Every defect above produced at least one code of its own. A gate that
        # refuses everything with one message cannot be acted on.
        "distinct_refusal_codes": sorted(
            {code for codes in refusals.values() for code in codes}
        ),
        "supportable_report_has_no_issues": remaining == [],
        "report_id": prepared["report_id"],
        "document_sha256": prepared["document_sha256"],
        "material_claims": sum(1 for item in document["claims"] if item["material"]),
        # The contradiction survives into the issued report rather than being
        # dropped once it was disclosed.
        "contradiction_in_issued_report": any(
            item["contradicting_evidence"] for item in document["claims"]
        ),
        "limitations_in_issued_report": document["limitations"],
        "evidence_index": [item["evidence_id"] for item in document["evidence_index"]],
        "agent_issuance_refused": agent_issuance,
        "issued": issued["status"] == "issued",
        "verification": verification,
        "tampered_report_detected": tampered,
        "evidence_usage": usage,
        "audit_event_types": [event["event_type"] for event in events],
    }


# -- claim builders --------------------------------------------------------------


def _scope_claim() -> ClaimInput:
    return ClaimInput(
        key="scope-statement",
        claim_type=ClaimType.CONTEXT,
        statement=(
            "The engagement tested the complete population of production merges to "
            "asteria/api in July 2026 against change policy v4."
        ),
        material=True,
        supporting_evidence_ids=["ev_changes"],
    )


def _limitation_claim() -> ClaimInput:
    return ClaimInput(
        key="population-limitation",
        claim_type=ClaimType.LIMITATION,
        statement=(
            "Repository branch-protection settings were not tested; this engagement "
            "covers merge records only."
        ),
    )


def _conclusion(**overrides: Any) -> ClaimInput:
    defaults: dict[str, Any] = dict(
        key="scm-01-conclusion",
        claim_type=ClaimType.CONCLUSION,
        statement=(
            "Control SCM-01 did not operate effectively: one production merge in the "
            "period reached asteria/api without an approved change ticket."
        ),
        material=True,
        confidence=0.8,
        supporting_evidence_ids=["ev_pr_1002"],
        finding_id=None,
    )
    defaults.update(overrides)
    return ClaimInput(**defaults)


def _request(claims: list[ClaimInput], **overrides: Any) -> ReportRequest:
    sections = overrides.pop("sections_override", None)
    template = TEMPLATE if sections is None else TEMPLATE.model_copy(update={"sections": sections})
    return ReportRequest(
        template=template,
        claims=claims,
        period_start=PERIOD[0],
        period_end=PERIOD[1],
        as_at=AS_AT,
        policy=EvidencePolicy(),
        prepared_by="alice.auditor@asteria.example",
        **overrides,
    )


def _refusal(action: Any, expected: Any) -> str:
    try:
        action()
    except expected as exc:  # type: ignore[misc]
        return str(exc)
    return ""


def _tamper_and_verify(service: ReportingService, database: Database, report_id: str) -> str:
    """Edit a stored report and show verification catch it.

    The case an export's promise rests on. A digest nobody recomputes is a
    decoration, so the demonstration recomputes it against a document that was
    changed after preparation.
    """
    from assuranceos.db.models import ReportVersion

    with database.transaction() as session:
        record = session.get(ReportVersion, report_id)
        document = dict(record.document_json)
        claims = list(document["claims"])
        claims[1] = {**claims[1], "statement": "Control SCM-01 operated effectively."}
        document["claims"] = claims
        record.document_json = document

    result = service.verify(tenant_id=DEMO_TENANT, report_id=report_id)
    outcome = (
        "digest mismatch detected" if not result["digest_matches"] else "NOT DETECTED"
    )
    # Restore, so a rerun of the demonstration starts from a consistent state.
    with database.transaction() as session:
        record = session.get(ReportVersion, report_id)
        document = dict(record.document_json)
        claims = list(document["claims"])
        claims[1] = {
            **claims[1],
            "statement": (
                "Control SCM-01 did not operate effectively: one production merge in the "
                "period reached asteria/api without an approved change ticket."
            ),
        }
        document["claims"] = claims
        record.document_json = document
    return outcome


def _reset_and_seed(database: Database) -> None:
    def evidence(
        evidence_id: str,
        *,
        engagement_id: str | None,
        locator: str,
        accepted: bool = True,
        tainted: bool = False,
        collected: datetime,
        source_time: datetime | None = None,
    ) -> EvidenceRecord:
        return EvidenceRecord(
            evidence_id=evidence_id,
            tenant_id=DEMO_TENANT,
            engagement_id=engagement_id,
            source_type="github" if "pr" in evidence_id else "confluence",
            source_locator=locator,
            content_sha256=f"{abs(hash(evidence_id)):064x}"[:64],
            accepted=accepted,
            tainted=tainted,
            integrity_status="verified",
            collected_at=collected,
            source_time=source_time or collected,
            classification="internal",
        )

    with database.transaction() as session:
        tenant = TenantRepository(session).get(DEMO_TENANT)
        if tenant is not None:
            session.delete(tenant)
    with database.transaction() as session:
        TenantRepository(session).add(
            Tenant(
                tenant_id=DEMO_TENANT,
                slug="asteria",
                name="Asteria Systems DemoCo",
                status="active",
                region="europe-west1",
            )
        )
        session.flush()
        for engagement_id, code, period in (
            (ENGAGEMENT, "SCM-2026-07", PERIOD),
            (OTHER_ENGAGEMENT, "SCM-2025-07", (date(2025, 7, 1), date(2025, 7, 31))),
        ):
            session.add(
                Engagement(
                    engagement_id=engagement_id,
                    tenant_id=DEMO_TENANT,
                    code=code,
                    title="Software change management",
                    status="reporting",
                    audit_pack_ref="software-change-management@2.0.0",
                    period_start=period[0],
                    period_end=period[1],
                )
            )
        session.flush()
        session.add_all(
            [
                evidence(
                    "ev_pr_1002",
                    engagement_id=ENGAGEMENT,
                    locator="github://asteria/api/pull/1002",
                    collected=datetime(2026, 8, 1, tzinfo=timezone.utc),
                    source_time=datetime(2026, 7, 11, tzinfo=timezone.utc),
                ),
                evidence(
                    "ev_changes",
                    engagement_id=ENGAGEMENT,
                    locator="github://asteria/api/pulls?merged=2026-07",
                    collected=datetime(2026, 8, 1, tzinfo=timezone.utc),
                    source_time=datetime(2026, 7, 31, tzinfo=timezone.utc),
                ),
                evidence(
                    "ev_exception_register",
                    engagement_id=ENGAGEMENT,
                    locator="jira://EXC/register",
                    collected=datetime(2026, 8, 1, tzinfo=timezone.utc),
                    source_time=datetime(2026, 7, 20, tzinfo=timezone.utc),
                ),
                # The policy document carrying the seeded prompt injection. It may
                # appear in a report; it may not be the only thing a conclusion
                # rests on.
                evidence(
                    "ev_policy_tainted",
                    engagement_id=ENGAGEMENT,
                    locator="confluence://asteria/change-management-policy",
                    tainted=True,
                    collected=datetime(2026, 8, 1, tzinfo=timezone.utc),
                    source_time=datetime(2026, 7, 5, tzinfo=timezone.utc),
                ),
                # Collected but never accepted into the vault.
                evidence(
                    "ev_unaccepted",
                    engagement_id=ENGAGEMENT,
                    locator="confluence://asteria/draft-note",
                    accepted=False,
                    collected=datetime(2026, 8, 1, tzinfo=timezone.utc),
                    source_time=datetime(2026, 7, 15, tzinfo=timezone.utc),
                ),
                evidence(
                    "ev_prior_year",
                    engagement_id=OTHER_ENGAGEMENT,
                    locator="github://asteria/api/pulls?merged=2025-07",
                    collected=datetime(2025, 8, 1, tzinfo=timezone.utc),
                    source_time=datetime(2025, 7, 31, tzinfo=timezone.utc),
                ),
                evidence(
                    "ev_august_merges",
                    engagement_id=ENGAGEMENT,
                    locator="github://asteria/api/pulls?merged=2026-08",
                    collected=datetime(2026, 9, 1, tzinfo=timezone.utc),
                    source_time=datetime(2026, 8, 20, tzinfo=timezone.utc),
                ),
            ]
        )
