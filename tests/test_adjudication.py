"""The finding lifecycle, from accepted exception to closed or reopened.

The cases follow the acceptance demonstration for the component: the seeded
Asteria exception becomes a finding, the skeptic rejects the ones canonical
records explain, a human approves the supported one, remediation opens once even
under replay, management submits closure evidence, an independent retester
verifies it, and the finding closes or is deterministically reopened.

The gates get their own cases, because each is a place where an autonomous
pipeline would otherwise award itself authority: a model cannot approve, a replay
cannot open a second ticket, and an author cannot retest their own work.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from assuranceos.adjudication import (
    AdjudicationRequest,
    AdjudicationService,
    ClosureEvidenceError,
    ClosureSubmission,
    ContradictionKind,
    FindingStatus,
    HumanDecision,
    HumanGateError,
    IdempotencyConflictError,
    IndependenceError,
    InvalidTransitionError,
    ProposedFinding,
    RemediationRequest,
    RetestOutcome,
    RetestRequest,
    SkepticReviewer,
    finding_from_exceptions,
)
from assuranceos.db.models import Engagement, Tenant
from assuranceos.db.session import Database

TENANT = "tnt_asteria"
ENGAGEMENT = "eng_scm_fy26"
PERIOD = (date(2026, 1, 1), date(2026, 6, 30))


@pytest.fixture
def database(tmp_path):
    db = Database.from_sqlite_path(tmp_path / "adjudication.db")
    db.create_schema()
    with db.transaction() as session:
        session.add(Tenant(tenant_id=TENANT, slug="asteria", name="Asteria"))
        session.flush()
        session.add(
            Engagement(
                engagement_id=ENGAGEMENT,
                tenant_id=TENANT,
                code="SCM-FY26",
                title="SCM FY26",
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


UNAPPROVED_CHANGES = [
    {
        "exception_key": "PR-42",
        "subject_ref": "PR-42",
        "attributes": {"occurred_on": "2026-03-04"},
    },
    {
        "exception_key": "PR-77",
        "subject_ref": "PR-77",
        "attributes": {"occurred_on": "2026-04-19"},
    },
]


def scm_finding(**overrides) -> ProposedFinding:
    defaults = dict(
        code="SCM-01",
        title="Production changes merged without an approved ticket",
        severity="high",
        criteria="Change policy v4 requires an approved ticket before merge.",
        risk_statement="Unauthorised change may reach production undetected.",
        exceptions=UNAPPROVED_CHANGES,
        evidence_ids=["ev_changes", "ev_policy"],
        source_run_id="run_scm_01",
        period=PERIOD,
    )
    defaults.update(overrides)
    return finding_from_exceptions(**defaults)


def approve(service, finding_id, actor="alice.auditor@asteria.example"):
    return service.adjudicate(
        tenant_id=TENANT,
        request=AdjudicationRequest(
            finding_id=finding_id,
            decision=HumanDecision.APPROVE,
            actor_id=actor,
            reason="Exceptions confirmed against the change register.",
            idempotency_key=f"approve:{finding_id}",
        ),
    )


def open_remediation(service, finding_id, key="rem-1", **overrides):
    request = RemediationRequest(
        finding_id=finding_id,
        owner_ref="platform-team@asteria.example",
        due_date=date(2026, 9, 30),
        action_plan="Enforce ticket reference in the merge gate.",
        idempotency_key=key,
        external_system=overrides.pop("external_system", "jira"),
        **overrides,
    )
    return service.open_remediation(tenant_id=TENANT, request=request)


# -- proposal and the skeptic -------------------------------------------------


def test_observed_condition_is_computed_not_narrated():
    """The count and the population are facts, not something a model asserts."""
    finding = scm_finding()
    assert "2 exception(s)" in finding.observed_condition
    assert "PR-42" in finding.observed_condition
    assert finding.affected_population["exception_count"] == 2


def test_a_finding_must_cite_evidence():
    with pytest.raises(ValueError, match="must cite at least one evidence id"):
        scm_finding(evidence_ids=[])


def test_supported_finding_is_proposed(service):
    finding_id, verdict = service.propose(
        tenant_id=TENANT,
        engagement_id=ENGAGEMENT,
        finding=scm_finding(),
        authored_by="agent:operating-effectiveness",
        skeptic=SkepticReviewer(period_start=PERIOD[0], period_end=PERIOD[1]),
        exception_rows=UNAPPROVED_CHANGES,
    )
    assert verdict.supported
    assert service.view(tenant_id=TENANT, finding_id=finding_id).status is FindingStatus.PROPOSED


def test_skeptic_rejects_a_finding_whose_exceptions_are_all_explained(service):
    """One exception is a registered waiver, the other falls outside the period.

    Neither is a finding, and a pipeline that raised them anyway would be worse
    than no pipeline: it teaches the audit function to ignore its own output.
    """
    rows = [
        {
            "exception_key": "SVC-ACCT-1",
            "subject_ref": "SVC-ACCT-1",
            "attributes": {"occurred_on": "2026-02-10"},
        },
        {
            "exception_key": "PR-09",
            "subject_ref": "PR-09",
            "attributes": {"occurred_on": "2025-11-02"},
        },
    ]
    skeptic = SkepticReviewer(
        approved_exceptions=[
            {
                "subject_ref": "SVC-ACCT-1",
                "reference": "EXC-2026-004",
                "expires_on": "2026-12-31",
                "evidence_id": "ev_exceptions",
            }
        ],
        period_start=PERIOD[0],
        period_end=PERIOD[1],
    )

    finding_id, verdict = service.propose(
        tenant_id=TENANT,
        engagement_id=ENGAGEMENT,
        finding=scm_finding(code="SCM-02", exceptions=rows),
        authored_by="agent:operating-effectiveness",
        skeptic=skeptic,
        exception_rows=rows,
    )

    assert not verdict.supported
    kinds = {c.kind for c in verdict.contradictions}
    assert kinds == {ContradictionKind.APPROVED_EXCEPTION, ContradictionKind.OUT_OF_PERIOD}

    view = service.view(tenant_id=TENANT, finding_id=finding_id)
    assert view.status is FindingStatus.REJECTED
    # The search is retained even though nothing was raised.
    assert any(d["decision_type"] == "skeptic_reject" for d in view.decisions)


def test_expired_waiver_does_not_explain_an_exception(service):
    """A stale exception register must not quietly suppress a real finding."""
    skeptic = SkepticReviewer(
        approved_exceptions=[
            {
                "subject_ref": "PR-42",
                "reference": "EXC-2025-001",
                "expires_on": "2026-01-31",
            }
        ],
        period_start=PERIOD[0],
        period_end=PERIOD[1],
    )
    verdict = skeptic.review(scm_finding(), exception_rows=UNAPPROVED_CHANGES)
    assert verdict.supported
    assert not verdict.contradictions


def test_untested_compensating_control_does_not_explain_an_exception():
    skeptic = SkepticReviewer(
        compensating_controls=[
            {"control_ref": "CC-1", "covers_subjects": ["PR-42"], "tested_effective": False}
        ]
    )
    verdict = skeptic.review(scm_finding(), exception_rows=UNAPPROVED_CHANGES)
    assert not verdict.contradictions


def test_partially_explained_finding_still_stands(service):
    """Three of five explained is still a finding built on the other two."""
    skeptic = SkepticReviewer(
        approved_exceptions=[{"subject_ref": "PR-42", "reference": "EXC-1"}],
        period_start=PERIOD[0],
        period_end=PERIOD[1],
    )
    verdict = skeptic.review(scm_finding(), exception_rows=UNAPPROVED_CHANGES)
    assert verdict.supported
    assert len(verdict.contradictions) == 1
    assert "1 of 2" in verdict.rationale


# -- the human gate -----------------------------------------------------------


def test_an_agent_cannot_approve_a_finding(service):
    """The single point where a pipeline is stopped from concluding on its own."""
    finding_id, _ = service.propose(
        tenant_id=TENANT,
        engagement_id=ENGAGEMENT,
        finding=scm_finding(),
        authored_by="agent:operating-effectiveness",
        exception_rows=UNAPPROVED_CHANGES,
    )
    with pytest.raises(HumanGateError, match="requires a decision attributable to a person"):
        service.adjudicate(
            tenant_id=TENANT,
            request=AdjudicationRequest(
                finding_id=finding_id,
                decision=HumanDecision.APPROVE,
                actor_id="agent:quality-reviewer",
                reason="Looks right to me.",
                idempotency_key="key-1",
            ),
        )
    assert (
        service.view(tenant_id=TENANT, finding_id=finding_id).status is FindingStatus.PROPOSED
    )


def test_remediation_cannot_open_before_approval(service):
    finding_id, _ = service.propose(
        tenant_id=TENANT,
        engagement_id=ENGAGEMENT,
        finding=scm_finding(),
        authored_by="agent:operating-effectiveness",
        exception_rows=UNAPPROVED_CHANGES,
    )
    with pytest.raises(InvalidTransitionError):
        open_remediation(service, finding_id)


# -- the full loop ------------------------------------------------------------


def test_the_loop_closes_a_verified_finding(service):
    finding_id, verdict = service.propose(
        tenant_id=TENANT,
        engagement_id=ENGAGEMENT,
        finding=scm_finding(),
        authored_by="agent:operating-effectiveness",
        skeptic=SkepticReviewer(period_start=PERIOD[0], period_end=PERIOD[1]),
        exception_rows=UNAPPROVED_CHANGES,
    )
    assert verdict.supported
    assert approve(service, finding_id) is FindingStatus.APPROVED

    action_id, created = open_remediation(service, finding_id)
    assert created

    service.submit_closure(
        tenant_id=TENANT,
        submission=ClosureSubmission(
            action_id=action_id,
            response_text="Merge gate now rejects commits without a ticket.",
            submitted_by="platform-team@asteria.example",
            closure_evidence_ids=["ev_gate_config"],
        ),
    )

    retest_id, status = service.retest(
        tenant_id=TENANT,
        request=RetestRequest(
            action_id=action_id,
            procedure_ref="SCM-01-retest",
            performed_by="bob.retester@asteria.example",
            idempotency_key="rt-1",
            outcome=RetestOutcome.CLOSED_VERIFIED,
            evidence_ids=["ev_changes_q3"],
            detail="40 changes sampled post-remediation, no exceptions.",
        ),
    )

    assert status is FindingStatus.CLOSED_VERIFIED
    view = service.view(tenant_id=TENANT, finding_id=finding_id)
    assert view.status is FindingStatus.CLOSED_VERIFIED
    assert view.retests[0]["retest_id"] == retest_id
    assert view.actions[0]["status"] == "closed"


def test_a_failed_retest_reopens_the_finding(service):
    finding_id, _ = service.propose(
        tenant_id=TENANT,
        engagement_id=ENGAGEMENT,
        finding=scm_finding(),
        authored_by="agent:operating-effectiveness",
        exception_rows=UNAPPROVED_CHANGES,
    )
    approve(service, finding_id)
    action_id, _ = open_remediation(service, finding_id)
    service.submit_closure(
        tenant_id=TENANT,
        submission=ClosureSubmission(
            action_id=action_id,
            response_text="Done.",
            submitted_by="platform-team@asteria.example",
            closure_evidence_ids=["ev_gate_config"],
        ),
    )

    _, status = service.retest(
        tenant_id=TENANT,
        request=RetestRequest(
            action_id=action_id,
            procedure_ref="SCM-01-retest",
            performed_by="bob.retester@asteria.example",
            idempotency_key="rt-1",
            outcome=RetestOutcome.PARTIALLY_REMEDIATED,
            evidence_ids=["ev_changes_q3"],
            detail="2 of 40 sampled changes still merged without a ticket.",
        ),
    )
    assert status is FindingStatus.REOPENED

    # A reopened finding rejoins the loop at remediation, never at proposal.
    action_again = service.reopen_for_remediation(
        tenant_id=TENANT,
        finding_id=finding_id,
        request=RemediationRequest(
            finding_id=finding_id,
            owner_ref="platform-team@asteria.example",
            due_date=date(2026, 12, 31),
            action_plan="Close the remaining bypass path.",
            idempotency_key="rem-2",
        ),
    )
    assert action_again == action_id
    assert (
        service.view(tenant_id=TENANT, finding_id=finding_id).status
        is FindingStatus.REMEDIATION_OPEN
    )


def test_every_non_closing_outcome_reopens(service):
    """Closure is the claim that needs evidence; reopening is the safe default."""
    for index, outcome in enumerate(
        [
            RetestOutcome.PARTIALLY_REMEDIATED,
            RetestOutcome.INEFFECTIVE,
            RetestOutcome.INSUFFICIENT_EVIDENCE,
            RetestOutcome.REOPEN,
        ]
    ):
        finding_id, _ = service.propose(
            tenant_id=TENANT,
            engagement_id=ENGAGEMENT,
            finding=scm_finding(code=f"SCM-1{index}"),
            authored_by="agent:operating-effectiveness",
            exception_rows=UNAPPROVED_CHANGES,
        )
        approve(service, finding_id)
        action_id, _ = open_remediation(service, finding_id, key=f"rem-{index}")
        service.submit_closure(
            tenant_id=TENANT,
            submission=ClosureSubmission(
                action_id=action_id,
                response_text="Done.",
                submitted_by="platform-team@asteria.example",
                closure_evidence_ids=["ev_x"],
            ),
        )
        _, status = service.retest(
            tenant_id=TENANT,
            request=RetestRequest(
                action_id=action_id,
                procedure_ref="r",
                performed_by="bob.retester@asteria.example",
                idempotency_key=f"rt-{index}",
                outcome=outcome,
                evidence_ids=["ev_y"],
            ),
        )
        assert status is FindingStatus.REOPENED, outcome


# -- idempotency --------------------------------------------------------------


def test_replay_does_not_open_a_second_remediation(service):
    """Replay is normal in a durable orchestrator; a duplicate ticket is not."""
    finding_id, _ = service.propose(
        tenant_id=TENANT,
        engagement_id=ENGAGEMENT,
        finding=scm_finding(),
        authored_by="agent:operating-effectiveness",
        exception_rows=UNAPPROVED_CHANGES,
    )
    approve(service, finding_id)

    first, created_first = open_remediation(service, finding_id, key="rem-1")
    second, created_second = open_remediation(service, finding_id, key="rem-1")

    assert first == second
    assert created_first and not created_second
    assert len(service.view(tenant_id=TENANT, finding_id=finding_id).actions) == 1


def test_a_different_key_still_cannot_open_a_second_action(service):
    """Keying on the finding, not the key, is what makes this hold."""
    finding_id, _ = service.propose(
        tenant_id=TENANT,
        engagement_id=ENGAGEMENT,
        finding=scm_finding(),
        authored_by="agent:operating-effectiveness",
        exception_rows=UNAPPROVED_CHANGES,
    )
    approve(service, finding_id)
    open_remediation(service, finding_id, key="rem-1")

    with pytest.raises(IdempotencyConflictError, match="carries one open action"):
        service.open_remediation(
            tenant_id=TENANT,
            request=RemediationRequest(
                finding_id=finding_id,
                owner_ref="someone-else@asteria.example",
                due_date=date(2026, 10, 1),
                action_plan="A different plan entirely.",
                idempotency_key="rem-2",
            ),
        )


def test_replayed_approval_records_one_decision(service):
    finding_id, _ = service.propose(
        tenant_id=TENANT,
        engagement_id=ENGAGEMENT,
        finding=scm_finding(),
        authored_by="agent:operating-effectiveness",
        exception_rows=UNAPPROVED_CHANGES,
    )
    approve(service, finding_id)
    approve(service, finding_id)

    view = service.view(tenant_id=TENANT, finding_id=finding_id)
    human = [d for d in view.decisions if d["decision_type"] == "human:approve"]
    assert len(human) == 1


# -- independence and evidence ------------------------------------------------


def test_the_finding_author_cannot_retest_their_own_work(service):
    finding_id, _ = service.propose(
        tenant_id=TENANT,
        engagement_id=ENGAGEMENT,
        finding=scm_finding(),
        authored_by="agent:operating-effectiveness",
        exception_rows=UNAPPROVED_CHANGES,
    )
    approve(service, finding_id)
    action_id, _ = open_remediation(service, finding_id)
    service.submit_closure(
        tenant_id=TENANT,
        submission=ClosureSubmission(
            action_id=action_id,
            response_text="Done.",
            submitted_by="platform-team@asteria.example",
            closure_evidence_ids=["ev_gate_config"],
        ),
    )

    with pytest.raises(IndependenceError, match="author of the finding"):
        service.retest(
            tenant_id=TENANT,
            request=RetestRequest(
                action_id=action_id,
                procedure_ref="r",
                performed_by="agent:operating-effectiveness",
                idempotency_key="rt-1",
                outcome=RetestOutcome.CLOSED_VERIFIED,
                evidence_ids=["ev_z"],
            ),
        )


def test_the_remediation_owner_cannot_retest_their_own_work(service):
    finding_id, _ = service.propose(
        tenant_id=TENANT,
        engagement_id=ENGAGEMENT,
        finding=scm_finding(),
        authored_by="agent:operating-effectiveness",
        exception_rows=UNAPPROVED_CHANGES,
    )
    approve(service, finding_id)
    action_id, _ = open_remediation(service, finding_id)
    service.submit_closure(
        tenant_id=TENANT,
        submission=ClosureSubmission(
            action_id=action_id,
            response_text="Done.",
            submitted_by="platform-team@asteria.example",
            closure_evidence_ids=["ev_gate_config"],
        ),
    )

    with pytest.raises(IndependenceError, match="owner of the remediation"):
        service.retest(
            tenant_id=TENANT,
            request=RetestRequest(
                action_id=action_id,
                procedure_ref="r",
                performed_by="Platform-Team@Asteria.example",  # case differs only
                idempotency_key="rt-1",
                outcome=RetestOutcome.CLOSED_VERIFIED,
                evidence_ids=["ev_z"],
            ),
        )


def test_closure_requires_the_evidence_the_action_demands(service):
    finding_id, _ = service.propose(
        tenant_id=TENANT,
        engagement_id=ENGAGEMENT,
        finding=scm_finding(),
        authored_by="agent:operating-effectiveness",
        exception_rows=UNAPPROVED_CHANGES,
    )
    approve(service, finding_id)
    action_id, _ = open_remediation(service, finding_id)

    with pytest.raises(ClosureEvidenceError, match="requires closure evidence"):
        service.submit_closure(
            tenant_id=TENANT,
            submission=ClosureSubmission(
                action_id=action_id,
                response_text="Trust me, it is fixed.",
                submitted_by="platform-team@asteria.example",
                closure_evidence_ids=[],
            ),
        )


def test_a_retest_cannot_close_on_no_evidence(service):
    finding_id, _ = service.propose(
        tenant_id=TENANT,
        engagement_id=ENGAGEMENT,
        finding=scm_finding(),
        authored_by="agent:operating-effectiveness",
        exception_rows=UNAPPROVED_CHANGES,
    )
    approve(service, finding_id)
    action_id, _ = open_remediation(service, finding_id)
    service.submit_closure(
        tenant_id=TENANT,
        submission=ClosureSubmission(
            action_id=action_id,
            response_text="Done.",
            submitted_by="platform-team@asteria.example",
            closure_evidence_ids=["ev_gate_config"],
        ),
    )

    with pytest.raises(ClosureEvidenceError, match="fresh evidence"):
        service.retest(
            tenant_id=TENANT,
            request=RetestRequest(
                action_id=action_id,
                procedure_ref="r",
                performed_by="bob.retester@asteria.example",
                idempotency_key="rt-1",
                outcome=RetestOutcome.CLOSED_VERIFIED,
                evidence_ids=[],
            ),
        )


def test_independence_basis_is_recorded_for_re_verification(service):
    """The separation-of-duties claim must be checkable from the record."""
    finding_id, _ = service.propose(
        tenant_id=TENANT,
        engagement_id=ENGAGEMENT,
        finding=scm_finding(),
        authored_by="agent:operating-effectiveness",
        exception_rows=UNAPPROVED_CHANGES,
    )
    approve(service, finding_id)
    action_id, _ = open_remediation(service, finding_id)
    service.submit_closure(
        tenant_id=TENANT,
        submission=ClosureSubmission(
            action_id=action_id,
            response_text="Done.",
            submitted_by="platform-team@asteria.example",
            closure_evidence_ids=["ev_gate_config"],
        ),
    )
    service.retest(
        tenant_id=TENANT,
        request=RetestRequest(
            action_id=action_id,
            procedure_ref="r",
            performed_by="bob.retester@asteria.example",
            idempotency_key="rt-1",
            outcome=RetestOutcome.CLOSED_VERIFIED,
            evidence_ids=["ev_q3"],
        ),
    )

    basis = service.view(tenant_id=TENANT, finding_id=finding_id).retests[0][
        "independence_basis"
    ]
    assert basis == {
        "authored_by": "agent:operating-effectiveness",
        "remediated_by": "platform-team@asteria.example",
        "performed_by": "bob.retester@asteria.example",
    }


# -- attribution and recurrence -----------------------------------------------


def test_every_transition_is_reconstructable_from_canonical_state(service, database):
    from assuranceos.db.repositories import AuditEventRepository

    finding_id, _ = service.propose(
        tenant_id=TENANT,
        engagement_id=ENGAGEMENT,
        finding=scm_finding(),
        authored_by="agent:operating-effectiveness",
        exception_rows=UNAPPROVED_CHANGES,
    )
    approve(service, finding_id)
    action_id, _ = open_remediation(service, finding_id)
    service.submit_closure(
        tenant_id=TENANT,
        submission=ClosureSubmission(
            action_id=action_id,
            response_text="Done.",
            submitted_by="platform-team@asteria.example",
            closure_evidence_ids=["ev_gate_config"],
        ),
    )
    service.retest(
        tenant_id=TENANT,
        request=RetestRequest(
            action_id=action_id,
            procedure_ref="r",
            performed_by="bob.retester@asteria.example",
            idempotency_key="rt-1",
            outcome=RetestOutcome.CLOSED_VERIFIED,
            evidence_ids=["ev_q3"],
        ),
    )

    with database.read_session() as session:
        events = AuditEventRepository(session).list(TENANT, ENGAGEMENT)
    types = [event["event_type"] for event in events]
    assert types == [
        "finding.proposed",
        "finding.approve",
        "remediation.opened",
        "remediation.closure_submitted",
        "finding.closed_verified",
    ]


def test_recurrence_is_detected_across_engagements(service, database):
    with database.transaction() as session:
        session.add(
            Engagement(
                engagement_id="eng_scm_fy27",
                tenant_id=TENANT,
                code="SCM-FY27",
                title="SCM FY27",
                audit_pack_ref="pack.scm@1.0.0",
                status="fieldwork",
                period_start=PERIOD[0] + timedelta(days=365),
                period_end=PERIOD[1] + timedelta(days=365),
            )
        )

    for engagement in (ENGAGEMENT, "eng_scm_fy27"):
        service.propose(
            tenant_id=TENANT,
            engagement_id=engagement,
            finding=scm_finding(),
            authored_by="agent:operating-effectiveness",
            exception_rows=UNAPPROVED_CHANGES,
        )

    match = service.recurrence(tenant_id=TENANT, code="SCM-01")
    assert match is not None
    assert match.occurrences == 2
    assert match.engagement_ids == [ENGAGEMENT, "eng_scm_fy27"]


def test_a_rejected_finding_does_not_count_as_recurrence(service, database):
    """Recurrence measures repeated failure, not repeated proposal."""
    with database.transaction() as session:
        session.add(
            Engagement(
                engagement_id="eng_scm_fy27",
                tenant_id=TENANT,
                code="SCM-FY27",
                title="SCM FY27",
                audit_pack_ref="pack.scm@1.0.0",
                status="fieldwork",
                period_start=PERIOD[0] + timedelta(days=365),
                period_end=PERIOD[1] + timedelta(days=365),
            )
        )

    service.propose(
        tenant_id=TENANT,
        engagement_id=ENGAGEMENT,
        finding=scm_finding(),
        authored_by="agent:operating-effectiveness",
        exception_rows=UNAPPROVED_CHANGES,
    )
    # Second engagement: every exception is a registered waiver, so nothing stands.
    service.propose(
        tenant_id=TENANT,
        engagement_id="eng_scm_fy27",
        finding=scm_finding(),
        authored_by="agent:operating-effectiveness",
        skeptic=SkepticReviewer(
            approved_exceptions=[
                {"subject_ref": "PR-42", "reference": "E1"},
                {"subject_ref": "PR-77", "reference": "E2"},
            ]
        ),
        exception_rows=UNAPPROVED_CHANGES,
    )

    assert service.recurrence(tenant_id=TENANT, code="SCM-01") is None
