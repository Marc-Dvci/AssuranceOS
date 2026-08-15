"""The risk universe derived from a company profile.

The rules are the product here, so the tests are about the rules doing work: a
different company must get a different universe, a trigger must be traceable back
to the fact that fired it, and nothing may be presented as registered that a
human has not accepted.
"""

from __future__ import annotations

from types import SimpleNamespace

from assuranceos.briefing import OrganizationBrief, OrganizationFactView
from assuranceos.risk_discovery import BLIND_SPOTS, TAXONOMY, propose_risks


def facts(**pairs) -> tuple[OrganizationFactView, ...]:
    claim = pairs.pop("_claim", "observed")
    return tuple(
        OrganizationFactView(key.replace("__", "."), value, claim)
        for key, value in pairs.items()
    )


def profile(**pairs) -> OrganizationBrief:
    return OrganizationBrief(legal_name="Test Co", industry="Testing", facts=facts(**pairs))


def codes(proposals) -> set[str]:
    return {item.code for item in proposals}


def test_a_platform_company_gets_the_change_risk_and_a_consultancy_does_not():
    platform = propose_risks(profile(public__operates_customer_facing_platform=True))
    advisory = propose_risks(profile(public__operates_customer_facing_platform=False))
    assert "AST-R-SCM" in codes(platform)
    assert "AST-R-SCM" not in codes(advisory)


def test_every_company_gets_the_risks_every_company_has():
    """A universe that only contains the interesting risks is not a universe."""
    minimal = propose_risks(profile())
    assert {"AST-R-IAM", "AST-R-EXPENSE"} <= codes(minimal)


def test_eu_operations_plus_personal_data_produces_the_transfer_risk():
    inside = propose_risks(
        profile(public__processes_personal_data=True, public__operating_locations=["FR", "US"])
    )
    outside = propose_risks(
        profile(public__processes_personal_data=True, public__operating_locations=["US", "CA"])
    )
    assert "AST-R-PRIVACY" in codes(inside)
    assert "AST-R-PRIVACY" not in codes(outside)
    # The data risk itself does not depend on where the company operates.
    assert "AST-R-DATA" in codes(outside)


def test_a_proposal_carries_the_fact_that_produced_it():
    """'The model thought so' is not an answer an auditor can argue with."""
    proposals = propose_risks(
        profile(public__cloud_provider="Google Cloud", _claim="inference")
    )
    pam = next(item for item in proposals if item.code == "AST-R-PAM")
    assert pam.triggers
    trigger = pam.triggers[0]
    assert trigger["fact_key"] == "public.cloud_provider"
    assert trigger["value"] == "Google Cloud"
    # And the claim class travels with it: this one was inferred, not observed.
    assert trigger["claim_type"] == "inference"


def test_a_registered_risk_reads_as_accepted_and_the_rest_as_proposed():
    organization = profile(public__operates_customer_facing_platform=True)
    proposals = propose_risks(
        organization,
        registered=[SimpleNamespace(code="AST-R-SCM", risk_id="rsk_1", status="active")],
    )
    by_code = {item.code: item for item in proposals}
    assert by_code["AST-R-SCM"].status == "accepted"
    assert by_code["AST-R-SCM"].risk_id == "rsk_1"
    assert by_code["AST-R-IAM"].status == "proposed"
    assert by_code["AST-R-IAM"].risk_id is None


def test_a_retired_risk_is_not_quietly_re_proposed_as_new():
    proposals = propose_risks(
        profile(),
        registered=[SimpleNamespace(code="AST-R-EXPENSE", risk_id="rsk_2", status="retired")],
    )
    expense = next(item for item in proposals if item.code == "AST-R-EXPENSE")
    assert expense.status == "rejected"


def test_nothing_is_derived_without_a_profile():
    """Silence about the company must not become a default risk universe."""
    assert propose_risks(None) == []


def test_a_risk_with_no_released_pack_says_so():
    """The platform having no methodology for a risk is a fact, not an omission."""
    proposals = propose_risks(profile(public__security_commitments=["SOC 2 Type II"]))
    attest = next(item for item in proposals if item.code == "AST-R-ATTEST")
    assert attest.suggested_pack is None
    assert attest.as_dict()["auditable"] is False


def test_the_taxonomy_declares_what_it_cannot_see():
    assert BLIND_SPOTS
    assert any("Fraud" in item for item in BLIND_SPOTS)


def test_every_rule_names_a_reason_a_person_could_disagree_with():
    for rule in TAXONOMY:
        assert len(rule.rationale) > 40, rule.code
        assert rule.title and rule.code.startswith("AST-R-")
