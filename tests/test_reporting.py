"""The claim graph and the fail-closed renderer.

One rule carries this component: a material claim either resolves to admissible
evidence, or it carries a stated limitation, or the report does not render. Almost
every case below is a refusal, because a gate that has only ever been shown to
pass things is not known to be a gate.
"""

from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

from assuranceos.db.models import Engagement, EvidenceRecord, Tenant
from assuranceos.db.session import Database
from assuranceos.reporting import (
    ClaimInput,
    ClaimType,
    EvidencePolicy,
    EvidenceView,
    ReportingError,
    ReportingService,
    ReportNotFoundError,
    ReportRequest,
    ReportSection,
    ReportTemplate,
    ReportType,
    ReuseJustification,
    UnsupportedClaimError,
    check_claim,
    document_digest,
    render,
)

TENANT = "tnt_report"
ENGAGEMENT = "eng_report"
OTHER = "eng_other"
PERIOD = (date(2026, 7, 1), date(2026, 7, 31))
AS_AT = date(2026, 9, 1)


def view(evidence_id: str, **overrides) -> EvidenceView:
    defaults = dict(
        evidence_id=evidence_id,
        engagement_id=ENGAGEMENT,
        source_type="github",
        source_locator=f"github://asteria/api/{evidence_id}",
        classification="internal",
        accepted=True,
        tainted=False,
        integrity_status="verified",
        deleted=False,
        collected_at=datetime(2026, 8, 1, tzinfo=timezone.utc),
        source_time=datetime(2026, 7, 15, tzinfo=timezone.utc),
        content_sha256="a" * 64,
    )
    defaults.update(overrides)
    return EvidenceView(**defaults)


def claim(**overrides) -> ClaimInput:
    defaults = dict(
        key="conclusion",
        claim_type=ClaimType.CONCLUSION,
        statement="Control SCM-01 did not operate effectively during the period.",
        material=True,
        supporting_evidence_ids=["ev_a"],
    )
    defaults.update(overrides)
    return ClaimInput(**defaults)


def issues_for(target, *, evidence=None, policy=None, reuse=(), engagement_id=ENGAGEMENT):
    index = {item.evidence_id: item for item in (evidence or [view("ev_a")])}
    return check_claim(
        target,
        evidence=index,
        policy=policy or EvidencePolicy(),
        period=PERIOD,
        as_at=AS_AT,
        engagement_id=engagement_id,
        reuse={item.evidence_id: item for item in reuse},
    )


def codes(target, **kwargs) -> set[str]:
    return {item.code for item in issues_for(target, **kwargs)}


# -- the rule --------------------------------------------------------------------


def test_a_material_claim_citing_nothing_does_not_render():
    assert codes(claim(supporting_evidence_ids=[])) == {"unsupported_material_claim"}


def test_a_stated_limitation_is_the_alternative_to_evidence():
    """Support, or a limitation, or no report. The limitation is the third option."""
    assert (
        codes(
            claim(
                supporting_evidence_ids=[],
                limitations=["The archived population could not be retrieved."],
            )
        )
        == set()
    )


def test_an_incidental_claim_is_not_gated():
    """A report is mostly context nobody needs to evidence.

    Gating everything would make the gate meaningless by making it constant.
    """
    assert codes(claim(material=False, supporting_evidence_ids=[])) == set()


def test_materiality_defaults_to_true():
    """Forgetting to consider it produces a refusal, not an unsupported sentence."""
    assert ClaimInput(
        key="c", claim_type=ClaimType.CONCLUSION, statement="A statement of some kind."
    ).material


def test_a_limitation_claim_is_never_material():
    """Requiring evidence for a disclosure would make silence easier than candour."""
    stated = ClaimInput(
        key="limit",
        claim_type=ClaimType.LIMITATION,
        statement="Branch protection settings were not tested.",
        material=True,
    )
    assert stated.material is False


# -- admissibility ---------------------------------------------------------------


def test_evidence_that_was_never_accepted_supports_nothing():
    assert "inadmissible_evidence" in codes(claim(), evidence=[view("ev_a", accepted=False)])


def test_deleted_evidence_supports_nothing():
    assert "inadmissible_evidence" in codes(claim(), evidence=[view("ev_a", deleted=True)])


def test_evidence_whose_integrity_check_failed_supports_nothing():
    assert "inadmissible_evidence" in codes(
        claim(), evidence=[view("ev_a", integrity_status="mismatch")]
    )


def test_a_citation_that_points_at_nothing_is_an_error_at_any_materiality():
    assert "unknown_evidence" in codes(claim(supporting_evidence_ids=["ev_missing"]))
    assert "unknown_evidence" in codes(
        claim(material=False, supporting_evidence_ids=["ev_missing"])
    )


def test_a_material_claim_may_not_rest_solely_on_tainted_evidence():
    """Tainted is what a guardrail flagged. It may appear; it may not be the basis."""
    assert "tainted_sole_support" in codes(claim(), evidence=[view("ev_a", tainted=True)])


def test_tainted_evidence_beside_clean_evidence_is_fine():
    assert (
        codes(
            claim(supporting_evidence_ids=["ev_a", "ev_b"]),
            evidence=[view("ev_a", tainted=True), view("ev_b")],
        )
        == set()
    )


# -- reuse and freshness ---------------------------------------------------------


def test_evidence_from_another_engagement_needs_a_justification():
    """It was collected under a different scope; carrying it across must be said."""
    assert "cross_engagement_reuse" in codes(
        claim(), evidence=[view("ev_a", engagement_id=OTHER)]
    )


def test_a_justification_admits_the_reuse():
    assert (
        codes(
            claim(),
            evidence=[view("ev_a", engagement_id=OTHER)],
            reuse=[
                ReuseJustification(
                    evidence_id="ev_a",
                    rationale="Used only to state the prior-year position as context.",
                    approved_by="alice.auditor@asteria.example",
                )
            ],
        )
        == set()
    )


def test_out_of_period_evidence_needs_a_justification():
    assert "out_of_period_evidence" in codes(
        claim(),
        evidence=[view("ev_a", source_time=datetime(2026, 9, 1, tzinfo=timezone.utc))],
    )


def test_stale_evidence_needs_a_limitation_not_a_justification():
    """Two different conditions with two different answers.

    A justification permits the reuse; it does not make year-old evidence current.
    """
    stale = view("ev_a", collected_at=datetime(2024, 1, 1, tzinfo=timezone.utc))
    assert "stale_evidence" in codes(claim(), evidence=[stale])
    assert (
        codes(
            claim(limitations=["The comparison rests on evidence collected in 2024."]),
            evidence=[stale],
        )
        == set()
    )


def test_the_policy_can_permit_reuse_but_it_has_to_be_configured():
    permissive = EvidencePolicy(allow_cross_engagement=True, allow_out_of_period=True)
    assert (
        codes(
            claim(),
            evidence=[
                view(
                    "ev_a",
                    engagement_id=OTHER,
                    source_time=datetime(2026, 9, 1, tzinfo=timezone.utc),
                )
            ],
            policy=permissive,
        )
        == set()
    )


# -- contradictions --------------------------------------------------------------


def test_a_linked_contradiction_must_be_disclosed():
    """Finding contradictions is good work; publishing without them is the failure."""
    assert "undisclosed_contradiction" in codes(
        claim(contradicting_evidence_ids=["ev_b"]),
        evidence=[view("ev_a"), view("ev_b")],
    )


def test_a_disclosed_contradiction_renders_and_survives_into_the_report():
    target = claim(
        contradicting_evidence_ids=["ev_b"],
        limitations=["One record in the exception register bears against this finding."],
    )
    assert codes(target, evidence=[view("ev_a"), view("ev_b")]) == set()


# -- rendering -------------------------------------------------------------------


def template(**overrides) -> ReportTemplate:
    defaults = dict(
        template_id="engagement-report",
        version="1.0.0",
        report_type=ReportType.ENGAGEMENT_REPORT,
        title="Engagement report",
        sections=[
            ReportSection(key="findings", heading="Findings", claim_keys=["conclusion"])
        ],
        required_sections=["findings"],
    )
    defaults.update(overrides)
    return ReportTemplate(**defaults)


def request(**overrides) -> ReportRequest:
    defaults = dict(
        template=template(),
        claims=[claim()],
        period_start=PERIOD[0],
        period_end=PERIOD[1],
        as_at=AS_AT,
        prepared_by="alice.auditor@asteria.example",
    )
    defaults.update(overrides)
    return ReportRequest(**defaults)


def test_a_failed_render_produces_no_partial_document():
    """A report missing its unsupportable paragraphs is the more dangerous document."""
    document, issues = render(
        request(claims=[claim(supporting_evidence_ids=[])]),
        tenant_id=TENANT,
        engagement_id=ENGAGEMENT,
        evidence=[view("ev_a")],
    )
    assert document == {}
    assert issues


def test_a_render_reports_every_issue_rather_than_the_first():
    """A partial list invites the belief that the last fix was the last problem."""
    _, issues = render(
        request(
            claims=[
                claim(key="a", supporting_evidence_ids=[]),
                claim(key="b", supporting_evidence_ids=["ev_missing"]),
            ],
            template=template(
                sections=[
                    ReportSection(key="findings", heading="Findings", claim_keys=["a", "b"])
                ]
            ),
        ),
        tenant_id=TENANT,
        engagement_id=ENGAGEMENT,
        evidence=[view("ev_a")],
    )
    assert {item.claim_key for item in issues} == {"a", "b"}


def test_a_template_referencing_a_missing_claim_is_refused():
    with pytest.raises(ValueError, match="references claims that were not supplied"):
        request(
            template=template(
                sections=[
                    ReportSection(
                        key="findings", heading="Findings", claim_keys=["absent"]
                    )
                ]
            )
        )


def test_a_required_section_with_no_claims_is_refused():
    _, issues = render(
        request(
            template=template(
                sections=[
                    ReportSection(key="findings", heading="Findings", claim_keys=[]),
                    ReportSection(
                        key="scope", heading="Scope", claim_keys=["conclusion"]
                    ),
                ]
            )
        ),
        tenant_id=TENANT,
        engagement_id=ENGAGEMENT,
        evidence=[view("ev_a")],
    )
    assert {item.code for item in issues} == {"empty_required_section"}


def test_the_digest_excludes_itself():
    """A document that includes its own hash cannot be verified."""
    document, _ = render(
        request(), tenant_id=TENANT, engagement_id=ENGAGEMENT, evidence=[view("ev_a")]
    )
    assert document_digest(document) == document["document_sha256"]


def test_limitations_are_collected_to_the_front_of_the_report():
    """A reader who reads only the summary still meets them."""
    document, _ = render(
        request(
            claims=[
                claim(limitations=["Branch protection was not tested."]),
                ClaimInput(
                    key="limit",
                    claim_type=ClaimType.LIMITATION,
                    statement="The archived population was unavailable.",
                ),
            ],
            template=template(
                sections=[
                    ReportSection(
                        key="findings",
                        heading="Findings",
                        claim_keys=["conclusion", "limit"],
                    )
                ]
            ),
        ),
        tenant_id=TENANT,
        engagement_id=ENGAGEMENT,
        evidence=[view("ev_a")],
    )
    assert document["limitations"] == [
        "Branch protection was not tested.",
        "The archived population was unavailable.",
    ]


# -- the service -----------------------------------------------------------------


@pytest.fixture
def database(tmp_path):
    db = Database.from_sqlite_path(tmp_path / "reporting.db")
    db.create_schema()
    with db.transaction() as session:
        session.add(Tenant(tenant_id=TENANT, slug="rp", name="Reporting"))
        session.flush()
        session.add(
            Engagement(
                engagement_id=ENGAGEMENT,
                tenant_id=TENANT,
                code="SCM-RP",
                title="SCM",
                status="reporting",
                audit_pack_ref="software-change-management@2.0.0",
                period_start=PERIOD[0],
                period_end=PERIOD[1],
            )
        )
        session.flush()
        session.add_all(
            [
                EvidenceRecord(
                    evidence_id="ev_a",
                    tenant_id=TENANT,
                    engagement_id=ENGAGEMENT,
                    source_type="github",
                    source_locator="github://asteria/api/pull/1002",
                    content_sha256="a" * 64,
                    accepted=True,
                    integrity_status="verified",
                    collected_at=datetime(2026, 8, 1, tzinfo=timezone.utc),
                    source_time=datetime(2026, 7, 11, tzinfo=timezone.utc),
                    classification="internal",
                ),
                EvidenceRecord(
                    evidence_id="ev_secret",
                    tenant_id=TENANT,
                    engagement_id=ENGAGEMENT,
                    source_type="hr",
                    source_locator="workday://asteria/case/17",
                    content_sha256="b" * 64,
                    accepted=True,
                    integrity_status="verified",
                    collected_at=datetime(2026, 8, 1, tzinfo=timezone.utc),
                    classification="restricted",
                ),
            ]
        )
    try:
        yield db
    finally:
        db.dispose()


@pytest.fixture
def service(database):
    return ReportingService(database)


def test_retrieval_excludes_a_classification_the_caller_may_not_see(service):
    """Excluded rather than redacted: a redacted row still tells the reader it exists."""
    ids = {item.evidence_id for item in service.retrieve(tenant_id=TENANT)}
    assert ids == {"ev_a"}

    widened = {
        item.evidence_id
        for item in service.retrieve(
            tenant_id=TENANT, visible_classifications=["internal", "restricted"]
        )
    }
    assert widened == {"ev_a", "ev_secret"}


def test_resolving_a_cited_id_does_not_apply_the_narrow_view(service):
    """The access decision belongs where the citation was made, not at render time."""
    resolved = service.resolve(tenant_id=TENANT, evidence_ids=["ev_secret"])
    assert [item.evidence_id for item in resolved] == ["ev_secret"]


def test_preparing_an_unsupported_report_writes_nothing(service, database):
    from assuranceos.db.models import ReportVersion

    with pytest.raises(UnsupportedClaimError, match="unresolved issue"):
        service.prepare(
            tenant_id=TENANT,
            engagement_id=ENGAGEMENT,
            request=request(claims=[claim(supporting_evidence_ids=[])]),
        )
    with database.read_session() as session:
        assert session.query(ReportVersion).count() == 0


def test_a_report_is_prepared_then_issued_by_a_person(service):
    prepared = service.prepare(
        tenant_id=TENANT, engagement_id=ENGAGEMENT, request=request()
    )
    assert prepared["status"] == "draft"

    with pytest.raises(ReportingError, match="attributable to a person"):
        service.issue(
            tenant_id=TENANT,
            report_id=prepared["report_id"],
            issued_by="agent:engagement-director",
            reason="Issued automatically.",
        )

    issued = service.issue(
        tenant_id=TENANT,
        report_id=prepared["report_id"],
        issued_by="dana.director@asteria.example",
        reason="Reviewed against the engagement file.",
    )
    assert issued["status"] == "issued"
    assert issued["created"] is True


def test_issuance_is_idempotent(service):
    prepared = service.prepare(
        tenant_id=TENANT, engagement_id=ENGAGEMENT, request=request()
    )
    first = service.issue(
        tenant_id=TENANT,
        report_id=prepared["report_id"],
        issued_by="dana.director@asteria.example",
        reason="Issued.",
    )
    second = service.issue(
        tenant_id=TENANT,
        report_id=prepared["report_id"],
        issued_by="dana.director@asteria.example",
        reason="Issued again.",
    )
    assert first["created"] is True and second["created"] is False


def test_a_draft_edited_after_preparation_cannot_be_issued(service, database):
    """The tamper case: a report changed in the database between the two acts."""
    from assuranceos.db.models import ReportVersion

    prepared = service.prepare(
        tenant_id=TENANT, engagement_id=ENGAGEMENT, request=request()
    )
    with database.transaction() as session:
        record = session.get(ReportVersion, prepared["report_id"])
        document = dict(record.document_json)
        document["title"] = "A different report"
        record.document_json = document

    with pytest.raises(ReportingError, match="no longer matches the digest"):
        service.issue(
            tenant_id=TENANT,
            report_id=prepared["report_id"],
            issued_by="dana.director@asteria.example",
            reason="Issuing the edited draft.",
        )

    verification = service.verify(tenant_id=TENANT, report_id=prepared["report_id"])
    assert verification["digest_matches"] is False


def test_verification_distinguishes_unchecked_from_invalid(service):
    """'Not checked' and 'checked and wrong' must not collapse into one answer."""
    prepared = service.prepare(
        tenant_id=TENANT, engagement_id=ENGAGEMENT, request=request()
    )
    result = service.verify(tenant_id=TENANT, report_id=prepared["report_id"])
    assert result["digest_matches"] is True
    assert result["signature_checked"] is False
    assert result["signature_valid"] is None


def test_a_signed_report_verifies_against_its_key(database):
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    from assuranceos.vault.signing import Ed25519ManifestSigner

    key = Ed25519PrivateKey.generate()
    signer = Ed25519ManifestSigner(private_key=key, key_id="test-report-v1")
    service = ReportingService(database, signer=signer)
    prepared = service.prepare(
        tenant_id=TENANT, engagement_id=ENGAGEMENT, request=request()
    )
    result = service.verify(
        tenant_id=TENANT,
        report_id=prepared["report_id"],
        public_key_pem=signer.public_key_pem(),
    )
    assert result["signed"] is True
    assert result["signature_valid"] is True


def test_the_claim_graph_answers_where_a_record_has_been_used(service):
    """The question asked when a record turns out to be wrong."""
    service.record_claims(
        tenant_id=TENANT,
        engagement_id=ENGAGEMENT,
        claims=[claim(), claim(key="second", statement="A second conclusion citing it.")],
    )
    usage = service.evidence_usage(tenant_id=TENANT, evidence_id="ev_a")
    assert len(usage) == 2
    assert {item["relationship"] for item in usage} == {"supports"}


def test_contradictions_are_stored_as_contradictions(service):
    """Stored rather than dropped, which is what lets the renderer refuse later."""
    service.record_claims(
        tenant_id=TENANT,
        engagement_id=ENGAGEMENT,
        claims=[
            claim(
                contradicting_evidence_ids=["ev_secret"],
                limitations=["One record bears against this finding."],
            )
        ],
    )
    usage = service.evidence_usage(tenant_id=TENANT, evidence_id="ev_secret")
    assert [item["relationship"] for item in usage] == ["contradicts"]


def test_an_unknown_report_is_refused(service):
    with pytest.raises(ReportNotFoundError):
        service.get(tenant_id=TENANT, report_id="rpt_nope")
