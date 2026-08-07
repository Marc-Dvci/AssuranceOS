"""Materiality, the methodology gate, disputes, and external remediation tickets.

These are the four steps Component 7 designed and deferred. Each exists to stop a
specific way an autonomous audit awards itself authority:

* materiality is *computed* from declared inputs, so severity is not an adjective
  a model picks;
* the methodology gate is held by someone who is not the author, and spent the
  moment the finding changes;
* a dispute stops the lifecycle rather than annotating it;
* a remediation ticket is filed at most once, including across a crash that leaves
  local state and the provider disagreeing.

The cases are written against the refusals rather than the happy paths, because a
gate that has never refused anything is not known to work.
"""

from __future__ import annotations

from datetime import date

import pytest

from assuranceos.adjudication import (
    AdjudicationRequest,
    AdjudicationService,
    DisputeGround,
    DisputeRequest,
    DisputeResolution,
    DisputeResolutionRequest,
    FactorAssertion,
    FindingStatus,
    HumanDecision,
    HumanGateError,
    IndependenceError,
    JiraTicketWriter,
    MaterialityError,
    MaterialityInputs,
    MaterialityPolicy,
    MaterialityRequest,
    NullTicketWriter,
    QualitativeFactor,
    QualityCheck,
    QualityGateError,
    QualityReviewRequest,
    RemediationRequest,
    ServiceNowTicketWriter,
    TicketingError,
    assess,
    content_hash,
    correlation_key,
    finding_from_exceptions,
)
from assuranceos.adjudication.repository import AdjudicationRepository
from assuranceos.connectors.exceptions import ConnectorProtocolError
from assuranceos.connectors.transport import FixtureTransport, HttpResponse
from assuranceos.db.models import Engagement, Tenant
from assuranceos.db.session import Database

TENANT = "tnt_gates"
ENGAGEMENT = "eng_gates"
PERIOD = (date(2026, 1, 1), date(2026, 6, 30))

AUTHOR = "agent:operating-effectiveness"
REVIEWER = "carol.qa@asteria.example"
APPROVER = "alice.auditor@asteria.example"
OWNER = "platform-team@asteria.example"
DIRECTOR = "dana.director@asteria.example"


@pytest.fixture
def database(tmp_path):
    db = Database.from_sqlite_path(tmp_path / "gates.db")
    db.create_schema()
    with db.transaction() as session:
        session.add(Tenant(tenant_id=TENANT, slug="gates", name="Gates"))
        session.flush()
        session.add(
            Engagement(
                engagement_id=ENGAGEMENT,
                tenant_id=TENANT,
                code="SCM-GATES",
                title="SCM gates",
                audit_pack_ref="pack.scm@1.0.0",
                status="fieldwork",
                period_start=PERIOD[0],
                period_end=PERIOD[1],
            )
        )
    try:
        yield db
    finally:
        db.dispose()


@pytest.fixture
def service(database):
    return AdjudicationService(database)


EXCEPTIONS = [
    {"exception_key": "PR-42", "subject_ref": "PR-42"},
    {"exception_key": "PR-77", "subject_ref": "PR-77"},
]


def propose(service, *, severity="high", authored_by=AUTHOR, **overrides) -> str:
    finding = finding_from_exceptions(
        code=overrides.pop("code", "SCM-01"),
        title="Production changes merged without an approved ticket",
        severity=severity,
        criteria="Change policy v4 requires an approved ticket before merge.",
        risk_statement="Unauthorised change may reach production undetected.",
        exceptions=EXCEPTIONS,
        evidence_ids=["ev_changes", "ev_policy"],
        source_run_id="run_scm_01",
        period=PERIOD,
        **overrides,
    )
    finding_id, _ = service.propose(
        tenant_id=TENANT,
        engagement_id=ENGAGEMENT,
        finding=finding,
        authored_by=authored_by,
        exception_rows=EXCEPTIONS,
    )
    return finding_id


def score(service, finding_id, **inputs):
    return service.assess_materiality(
        tenant_id=TENANT,
        request=MaterialityRequest(
            finding_id=finding_id,
            inputs=MaterialityInputs(
                population_size=inputs.pop("population_size", 40),
                exception_count=inputs.pop("exception_count", 2),
                **inputs,
            ),
            assessed_by=AUTHOR,
        ),
    )


def review(service, finding_id, reviewer=REVIEWER):
    return service.review_quality(
        tenant_id=TENANT,
        request=QualityReviewRequest(finding_id=finding_id, reviewer_id=reviewer),
    )


def approve(service, finding_id, actor=APPROVER):
    return service.adjudicate(
        tenant_id=TENANT,
        request=AdjudicationRequest(
            finding_id=finding_id,
            decision=HumanDecision.APPROVE,
            actor_id=actor,
            reason="Confirmed against the change register.",
            idempotency_key=f"approve:{finding_id}:{actor}",
        ),
    )


# -- materiality as arithmetic --------------------------------------------------


def test_the_score_is_reproducible_from_its_inputs():
    """Same inputs, same policy, same number. No clock, no database, no model."""
    inputs = MaterialityInputs(population_size=200, exception_count=15)
    first = assess(inputs)
    second = assess(inputs)
    assert first.score == second.score == pytest.approx(1.5)
    assert first.severity_floor == "medium"
    assert first.components["dominant_term"] == "quantitative"


def test_a_small_population_does_not_produce_a_rate():
    """Three of five is 60%, and 60% of nothing is not a materiality signal.

    Below the policy floor the quantitative term is dropped entirely rather than
    scaled down, because the objection to a five-item population is that the rate
    is meaningless, not that it is small.
    """
    result = assess(MaterialityInputs(population_size=5, exception_count=3))
    assert result.components["population_below_floor"] is True
    assert result.components["quantitative"] == 0
    assert result.material is False
    assert "below the policy floor" in result.rationale


def test_a_qualitative_factor_can_carry_a_numerically_tiny_finding():
    """One in a thousand, and reportable to a regulator, is still material.

    The terms combine by max rather than by average precisely so this case is not
    diluted by the quantitative term that says it is negligible.
    """
    result = assess(
        MaterialityInputs(
            population_size=1000,
            exception_count=1,
            factors=[
                FactorAssertion(
                    factor=QualitativeFactor.REGULATORY_REPORTABLE,
                    rationale="Reportable under the operational-resilience regime.",
                    evidence_ids=["ev_scope"],
                )
            ],
        )
    )
    assert result.components["quantitative"] == pytest.approx(0.02)
    assert result.score == pytest.approx(2.0)
    assert result.severity_floor == "high"
    assert result.components["dominant_term"] == "qualitative"


def test_a_qualitative_factor_must_cite_evidence():
    """The one control on materiality inflation: point at a record.

    Rejected at the type boundary, so no downstream state can hold an unevidenced
    factor and no caller has to remember to check.
    """
    with pytest.raises(ValueError):
        FactorAssertion(
            factor=QualitativeFactor.FRAUD_INDICATOR,
            rationale="It feels serious.",
            evidence_ids=[],
        )


def test_a_population_that_does_not_reconcile_is_refused():
    with pytest.raises(ValueError, match="does not reconcile"):
        MaterialityInputs(population_size=10, exception_count=11)


def test_the_same_factor_cannot_be_asserted_twice_to_double_its_weight():
    with pytest.raises(ValueError, match="asserted twice"):
        MaterialityInputs(
            population_size=100,
            exception_count=1,
            factors=[
                FactorAssertion(
                    factor=QualitativeFactor.REPEAT_FINDING,
                    rationale="Seen last year.",
                    evidence_ids=["ev_a"],
                ),
                FactorAssertion(
                    factor=QualitativeFactor.REPEAT_FINDING,
                    rationale="Seen the year before too.",
                    evidence_ids=["ev_b"],
                ),
            ],
        )


def test_severity_bands_must_be_ordered():
    with pytest.raises(ValueError, match="highest threshold down"):
        MaterialityPolicy(severity_bands=[(1.0, "medium"), (3.0, "critical")])


def test_monetary_exposure_can_drive_the_score_on_its_own():
    result = assess(
        MaterialityInputs(population_size=5000, exception_count=2, monetary_exposure=750_000),
        MaterialityPolicy(monetary_threshold=250_000),
    )
    assert result.components["dominant_term"] == "monetary"
    assert result.severity_floor == "critical"


# -- materiality against a finding ----------------------------------------------


def test_materiality_escalates_a_severity_the_author_understated(service):
    finding_id = propose(service, severity="low")
    assessment = score(
        service,
        finding_id,
        factors=[
            FactorAssertion(
                factor=QualitativeFactor.REGULATORY_REPORTABLE,
                rationale="Reportable under the operational-resilience regime.",
                evidence_ids=["ev_scope"],
            )
        ],
    )
    assert assessment.severity_floor == "high"
    assert service.view(tenant_id=TENANT, finding_id=finding_id).severity == "high"


def test_materiality_never_lowers_a_severity_by_itself(service):
    """An assessment that scores low leaves a high finding alone.

    Escalation is automatic because raising a severity needs no permission.
    Lowering one does, and routing it through the same call would let a rescore
    perform it as a side effect.
    """
    finding_id = propose(service, severity="critical")
    assessment = score(service, finding_id, population_size=1000, exception_count=1)
    assert assessment.severity_floor == "low"
    assert service.view(tenant_id=TENANT, finding_id=finding_id).severity == "critical"


def test_an_agent_cannot_lower_a_severity(service):
    from assuranceos.adjudication import SeverityOverrideRequest

    finding_id = propose(service, severity="high")
    score(
        service,
        finding_id,
        factors=[
            FactorAssertion(
                factor=QualitativeFactor.REGULATORY_REPORTABLE,
                rationale="Reportable.",
                evidence_ids=["ev_scope"],
            )
        ],
    )
    with pytest.raises(HumanGateError, match="requires a person"):
        service.override_severity(
            tenant_id=TENANT,
            request=SeverityOverrideRequest(
                finding_id=finding_id,
                severity="low",
                actor_id="agent:finding-adjudicator",
                reason="I have reconsidered my own conclusion.",
            ),
        )


def test_an_override_without_an_assessment_has_no_floor_to_override(service):
    from assuranceos.adjudication import SeverityOverrideRequest

    finding_id = propose(service)
    with pytest.raises(MaterialityError, match="no current materiality assessment"):
        service.override_severity(
            tenant_id=TENANT,
            request=SeverityOverrideRequest(
                finding_id=finding_id,
                severity="low",
                actor_id=DIRECTOR,
                reason="It does not seem that serious to me.",
            ),
        )


# -- the methodology gate --------------------------------------------------------


def test_approval_is_refused_until_the_gates_are_cleared(service):
    finding_id = propose(service)
    with pytest.raises(QualityGateError) as excinfo:
        approve(service, finding_id)
    message = str(excinfo.value)
    assert "no materiality assessment exists" in message
    assert "no passing quality review exists" in message


def test_the_author_cannot_review_their_own_work(service):
    finding_id = propose(service)
    score(service, finding_id)
    outcome = review(service, finding_id, reviewer=AUTHOR)
    assert outcome.passed is False
    assert QualityCheck.NOT_SELF_REVIEWED in {item.check for item in outcome.failures}


def test_the_reviewer_cannot_also_approve(service):
    finding_id = propose(service)
    score(service, finding_id)
    review(service, finding_id)
    with pytest.raises(IndependenceError, match="cannot also approve"):
        approve(service, finding_id, actor=REVIEWER)
    # A third person can. The refusal is about the pairing, not about the finding.
    assert approve(service, finding_id) is FindingStatus.APPROVED


def test_a_failed_review_is_recorded_rather_than_raised(service):
    """The reviewer's job is to report what they found.

    Refusing to store a failure would leave the only durable trace of a badly
    supported finding in the application logs.
    """
    finding_id = propose(service)
    outcome = review(service, finding_id)
    assert outcome.passed is False
    view = service.view(tenant_id=TENANT, finding_id=finding_id)
    assert view.quality_reviews[0]["passed"] is False
    assert "materiality_assessed" in view.quality_reviews[0]["failed_checks"]


def test_undisclosed_contradictions_fail_the_gate(service):
    """Contradictions found and not disclosed is the case the rule is for.

    An absence of contradictions needs no limitation; a suppressed exception the
    reader is never told about is exactly what "contradictory evidence must be
    disclosed" means.
    """
    from assuranceos.adjudication import SkepticReviewer

    rows = [
        {"exception_key": "PR-42", "subject_ref": "PR-42"},
        {"exception_key": "PR-77", "subject_ref": "PR-77"},
    ]
    finding = finding_from_exceptions(
        code="SCM-05",
        title="Changes merged without an approved ticket",
        severity="medium",
        criteria="Change policy v4 requires an approved ticket before merge.",
        risk_statement="Unauthorised change may reach production.",
        exceptions=rows,
        evidence_ids=["ev_changes"],
        source_run_id="run_scm_05",
    )
    finding_id, verdict = service.propose(
        tenant_id=TENANT,
        engagement_id=ENGAGEMENT,
        finding=finding,
        authored_by=AUTHOR,
        skeptic=SkepticReviewer(
            approved_exceptions=[{"subject_ref": "PR-42", "reference": "EXC-1"}]
        ),
        exception_rows=rows,
    )
    assert verdict.supported
    score(service, finding_id)
    outcome = review(service, finding_id)
    assert QualityCheck.LIMITATIONS_DISCLOSED in {item.check for item in outcome.failures}


def test_a_finding_that_was_never_searched_fails_the_gate(service, database):
    """An empty contradiction list is ambiguous; the timestamp is not."""
    finding_id = propose(service)
    score(service, finding_id)
    with database.transaction() as session:
        record = AdjudicationRepository(session).get_finding(TENANT, finding_id)
        record.skeptic_reviewed_at = None

    outcome = review(service, finding_id)
    failed = {item.check for item in outcome.failures}
    assert QualityCheck.CONTRADICTIONS_SEARCHED in failed


def test_the_gate_can_be_waived_only_by_configuration(database):
    """A deployment with no second reviewer says so explicitly.

    The waiver exists so that an engagement type genuinely lacking a reviewer is a
    stated setting rather than an undocumented code path — and so the default can
    be shown to bite.
    """
    permissive = AdjudicationService(database, require_quality_review=False)
    finding_id = propose(permissive)
    score(permissive, finding_id)
    assert approve(permissive, finding_id) is FindingStatus.APPROVED


def test_materiality_is_still_required_when_quality_review_is_waived(database):
    permissive = AdjudicationService(database, require_quality_review=False)
    finding_id = propose(permissive)
    with pytest.raises(QualityGateError, match="no materiality assessment"):
        approve(permissive, finding_id)


def test_the_content_hash_ignores_immaterial_differences():
    """Version numbers and timestamps do not spend a review; substance does."""
    base = dict(
        code="SCM-01",
        title="Changes merged without an approved ticket",
        severity="high",
        criteria="Change policy v4.",
        observed_condition="2 exceptions.",
        risk_statement="Unauthorised change.",
        evidence_ids=["ev_b", "ev_a"],
        exception_keys=["PR-77", "PR-42"],
    )
    reordered = dict(base, evidence_ids=["ev_a", "ev_b"], exception_keys=["PR-42", "PR-77"])
    assert content_hash(**base) == content_hash(**reordered)
    assert content_hash(**base) != content_hash(**dict(base, severity="low"))


# -- disputes --------------------------------------------------------------------


def dispute(service, finding_id, raised_by=OWNER, ground=DisputeGround.SEVERITY_OVERSTATED):
    return service.raise_dispute(
        tenant_id=TENANT,
        request=DisputeRequest(
            finding_id=finding_id,
            ground=ground,
            statement="Two exceptions in forty is not a high-severity failure.",
            raised_by=raised_by,
            evidence_ids=["ev_oncall"],
        ),
    )


def test_an_upheld_dispute_restores_the_status_it_interrupted(service):
    finding_id = propose(service)
    score(service, finding_id)
    review(service, finding_id)
    approve(service, finding_id)

    dispute_id = dispute(service, finding_id)
    assert service.view(tenant_id=TENANT, finding_id=finding_id).status is FindingStatus.DISPUTED

    status = service.resolve_dispute(
        tenant_id=TENANT,
        request=DisputeResolutionRequest(
            dispute_id=dispute_id,
            resolution=DisputeResolution.UPHELD,
            reason="The floor follows from reportability, not from the count.",
            resolved_by=DIRECTOR,
        ),
    )
    assert status is FindingStatus.APPROVED


def test_a_modified_dispute_voids_the_approval_it_was_granted_under(service):
    """Conceding that the finding must change spends the approval.

    Both the review and the approval were given for text that is about to be
    rewritten, so carrying either forward would attribute a decision to someone
    who never made it about this version.
    """
    finding_id = propose(service)
    score(service, finding_id)
    review(service, finding_id)
    approve(service, finding_id)
    dispute_id = dispute(service, finding_id, ground=DisputeGround.CONDITION_INACCURATE)

    status = service.resolve_dispute(
        tenant_id=TENANT,
        request=DisputeResolutionRequest(
            dispute_id=dispute_id,
            resolution=DisputeResolution.MODIFIED,
            reason="The population included merges from an out-of-scope repository.",
            resolved_by=DIRECTOR,
        ),
    )
    assert status is FindingStatus.PROPOSED


def test_a_withdrawn_dispute_is_terminal(service):
    finding_id = propose(service)
    dispute_id = dispute(service, finding_id, ground=DisputeGround.OUT_OF_SCOPE)
    status = service.resolve_dispute(
        tenant_id=TENANT,
        request=DisputeResolutionRequest(
            dispute_id=dispute_id,
            resolution=DisputeResolution.WITHDRAWN,
            reason="The repository is out of scope for this engagement.",
            resolved_by=DIRECTOR,
        ),
    )
    assert status is FindingStatus.WITHDRAWN
    from assuranceos.adjudication import InvalidTransitionError

    with pytest.raises(InvalidTransitionError):
        dispute(service, finding_id)


def test_the_author_cannot_resolve_a_dispute_against_their_own_finding(service):
    """Authored by a person here, so the check under test is the one that fires.

    An agent author would be refused one step earlier by the human gate, which
    would leave the independence check untested.
    """
    human_author = "erin.senior@asteria.example"
    finding_id = propose(service, authored_by=human_author)
    dispute_id = dispute(service, finding_id)
    with pytest.raises(IndependenceError, match="authored the finding"):
        service.resolve_dispute(
            tenant_id=TENANT,
            request=DisputeResolutionRequest(
                dispute_id=dispute_id,
                resolution=DisputeResolution.UPHELD,
                reason="My finding stands because I say it stands.",
                resolved_by=human_author,
            ),
        )


def test_an_agent_cannot_resolve_a_dispute(service):
    finding_id = propose(service, code="SCM-09")
    dispute_id = dispute(service, finding_id)
    with pytest.raises(HumanGateError, match="attributable to a person"):
        service.resolve_dispute(
            tenant_id=TENANT,
            request=DisputeResolutionRequest(
                dispute_id=dispute_id,
                resolution=DisputeResolution.UPHELD,
                reason="Resolved automatically by the adjudication agent.",
                resolved_by="agent:finding-adjudicator",
            ),
        )


def test_only_one_dispute_is_open_at_a_time(service):
    from assuranceos.adjudication import DisputeError

    finding_id = propose(service)
    dispute(service, finding_id)
    with pytest.raises(DisputeError, match="already has an open dispute"):
        dispute(service, finding_id, ground=DisputeGround.CRITERIA_INCORRECT)


def test_rounds_are_numbered_and_escalate_past_the_limit(service):
    """The history is a sequence, not a mutable field.

    Past the round limit the disagreement is flagged for escalation rather than
    refused: blocking it would leave management with no route except acceptance.
    """
    finding_id = propose(service)
    for round_no in range(4):
        dispute_id = dispute(service, finding_id, ground=DisputeGround.MATERIALITY_DISPUTED)
        service.resolve_dispute(
            tenant_id=TENANT,
            request=DisputeResolutionRequest(
                dispute_id=dispute_id,
                resolution=DisputeResolution.UPHELD,
                reason=f"Round {round_no} considered and the finding stands.",
                resolved_by=DIRECTOR,
            ),
        )
    disputes = service.view(tenant_id=TENANT, finding_id=finding_id).disputes
    assert [item["round_no"] for item in disputes] == [1, 2, 3, 4]
    assert [item["escalated"] for item in disputes] == [False, False, False, True]


# -- external remediation tickets ------------------------------------------------


JIRA_BASE = "https://asteria.atlassian.net"
SNOW_BASE = "https://asteria.service-now.com"


def open_action(service, finding_id, *, system="jira", target="AUD") -> str:
    action_id, _ = service.open_remediation(
        tenant_id=TENANT,
        request=RemediationRequest(
            finding_id=finding_id,
            owner_ref=OWNER,
            due_date=date(2026, 9, 30),
            action_plan="Enforce the ticket reference in the merge gate.",
            idempotency_key=f"rem:{finding_id}",
            external_system=system,
            external_target=target,
        ),
    )
    return action_id


def approved_finding(service, code="SCM-01") -> str:
    finding_id = propose(service, code=code)
    score(service, finding_id)
    review(service, finding_id)
    approve(service, finding_id)
    return finding_id


def test_jira_files_once_and_then_adopts(service):
    """The second sync runs with no local reference and still creates nothing.

    That is the crash case: the provider made the ticket, this side never
    committed the reference. A local guard alone would file a duplicate.
    """
    action_id = open_action(service, approved_finding(service))
    key = correlation_key(action_id)
    transport = FixtureTransport(
        {
            ("POST", f"{JIRA_BASE}/rest/api/3/search/jql"): [
                HttpResponse(status_code=200, headers={}, json_body={"issues": []}),
                HttpResponse(
                    status_code=200,
                    headers={},
                    json_body={"issues": [{"key": "AUD-9", "fields": {"labels": [key]}}]},
                ),
            ],
            ("POST", f"{JIRA_BASE}/rest/api/3/issue"): [
                HttpResponse(status_code=201, headers={}, json_body={"key": "AUD-9"})
            ],
        }
    )
    writer = JiraTicketWriter(base_url=JIRA_BASE, transport=transport)

    first = service.sync_remediation_ticket(
        tenant_id=TENANT, action_id=action_id, writer=writer
    )
    assert first["created"] is True
    assert first["external_ref"] == "AUD-9"

    _forget_reference(service, action_id)
    second = service.sync_remediation_ticket(
        tenant_id=TENANT, action_id=action_id, writer=writer
    )
    assert second["created"] is False
    assert second["external_ref"] == "AUD-9"
    # Two searches and exactly one create.
    creates = [r for r in transport.requests if r.url.endswith("/rest/api/3/issue")]
    assert len(creates) == 1
    assert key in creates[0].json_body["fields"]["labels"]


def test_a_second_sync_with_the_reference_intact_never_reaches_the_provider(service):
    action_id = open_action(service, approved_finding(service))
    transport = FixtureTransport(
        {
            ("POST", f"{JIRA_BASE}/rest/api/3/search/jql"): [
                HttpResponse(status_code=200, headers={}, json_body={"issues": []})
            ],
            ("POST", f"{JIRA_BASE}/rest/api/3/issue"): [
                HttpResponse(status_code=201, headers={}, json_body={"key": "AUD-9"})
            ],
        }
    )
    writer = JiraTicketWriter(base_url=JIRA_BASE, transport=transport)
    service.sync_remediation_ticket(tenant_id=TENANT, action_id=action_id, writer=writer)
    calls = len(transport.requests)

    replay = service.sync_remediation_ticket(
        tenant_id=TENANT, action_id=action_id, writer=writer
    )
    assert replay["reason"] == "already filed"
    assert len(transport.requests) == calls


def test_servicenow_uses_its_native_correlation_field(service):
    action_id = open_action(service, approved_finding(service), system="servicenow", target="sn_task")
    key = correlation_key(action_id)
    query_url = (
        f"{SNOW_BASE}/api/now/table/sn_task"
        f"?sysparm_fields=sys_id%2Cnumber%2Cshort_description"
        f"&sysparm_limit=2&sysparm_query=correlation_id%3D{key.replace(':', '%3A')}"
    )
    transport = FixtureTransport(
        {
            ("GET", query_url): [
                HttpResponse(status_code=200, headers={}, json_body={"result": []})
            ],
            ("POST", f"{SNOW_BASE}/api/now/table/sn_task"): [
                HttpResponse(
                    status_code=201,
                    headers={},
                    json_body={"result": {"sys_id": "abc123", "number": "TASK0042"}},
                )
            ],
        }
    )
    writer = ServiceNowTicketWriter(base_url=SNOW_BASE, transport=transport)
    result = service.sync_remediation_ticket(
        tenant_id=TENANT, action_id=action_id, writer=writer
    )
    assert result["external_ref"] == "TASK0042"
    inserted = [r for r in transport.requests if r.method == "POST"][0]
    assert inserted.json_body["correlation_id"] == key


def test_two_tickets_under_one_correlation_key_is_refused_not_guessed(service):
    """The invariant is already broken; picking one would hide it."""
    action_id = open_action(service, approved_finding(service))
    transport = FixtureTransport(
        {
            ("POST", f"{JIRA_BASE}/rest/api/3/search/jql"): [
                HttpResponse(
                    status_code=200,
                    headers={},
                    json_body={"issues": [{"key": "AUD-9"}, {"key": "AUD-10"}]},
                )
            ]
        }
    )
    writer = JiraTicketWriter(base_url=JIRA_BASE, transport=transport)
    with pytest.raises(TicketingError, match="must map to exactly one"):
        service.sync_remediation_ticket(
            tenant_id=TENANT, action_id=action_id, writer=writer
        )
    view = service.view(tenant_id=TENANT, finding_id=_finding_of(service, action_id))
    assert view.actions[0]["external_sync_state"] == "failed"


def test_a_provider_failure_is_recorded_before_it_is_raised(service):
    action_id = open_action(service, approved_finding(service))

    class Refusing:
        system = "jira"

        def create_or_get(self, request):
            raise ConnectorProtocolError("Jira issue creation returned no issue key")

    with pytest.raises(TicketingError, match="returned no issue key"):
        service.sync_remediation_ticket(
            tenant_id=TENANT, action_id=action_id, writer=Refusing()
        )
    view = service.view(tenant_id=TENANT, finding_id=_finding_of(service, action_id))
    assert view.actions[0]["external_sync_state"] == "failed"
    assert view.actions[0]["external_ref"] is None


def test_a_writer_for_the_wrong_system_is_refused(service):
    action_id = open_action(service, approved_finding(service))
    with pytest.raises(TicketingError, match="files into 'none'"):
        service.sync_remediation_ticket(
            tenant_id=TENANT, action_id=action_id, writer=NullTicketWriter()
        )


def test_an_action_with_no_target_cannot_be_filed(service):
    action_id = open_action(service, approved_finding(service), target=None)
    transport = FixtureTransport({})
    with pytest.raises(TicketingError, match="names no project or table"):
        service.sync_remediation_ticket(
            tenant_id=TENANT,
            action_id=action_id,
            writer=JiraTicketWriter(base_url=JIRA_BASE, transport=transport),
        )


def test_an_internally_tracked_remediation_takes_the_same_path(service):
    """``external_system="none"`` is a writer, not a branch nothing tests."""
    action_id = open_action(service, approved_finding(service), system="none", target=None)
    result = service.sync_remediation_ticket(
        tenant_id=TENANT, action_id=action_id, writer=NullTicketWriter()
    )
    assert result["external_ref"] == correlation_key(action_id)
    assert result["created"] is False


# -- helpers --------------------------------------------------------------------


def _forget_reference(service, action_id: str) -> None:
    with service.database.transaction() as session:
        action = AdjudicationRepository(session).get_action(TENANT, action_id)
        action.external_ref = None
        action.external_sync_state = "pending"


def _finding_of(service, action_id: str) -> str:
    with service.database.read_session() as session:
        return AdjudicationRepository(session).get_action(TENANT, action_id).finding_id
