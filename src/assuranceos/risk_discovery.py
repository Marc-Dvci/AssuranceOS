"""Which risks a company has, derived from what the platform learned about it.

Onboarding already reads a company from public sources and produces an
attributed profile — industry, where it operates, whether it runs a
customer-facing platform, whether it processes personal data, what it has
publicly committed to. Until this module existed that profile went no further:
the risk register was declared by hand, so *every new tenant needed an auditor to
write one before the platform could do anything*. That is the difference between
a product and a consulting engagement with software attached.

This closes it. A declared taxonomy maps company attributes to candidate risks,
each one carrying the fact that triggered it and the Audit Pack that would test
it. Three properties are deliberate.

**It is a proposal, never a register.** Nothing here writes a risk. The output is
a list a human accepts or rejects, and the screen that renders it says which is
which. A risk universe nobody signed for is a risk universe nobody owns.

**It is deterministic, and therefore arguable.** No model is involved. Asked why
a risk is on the list, the answer is a fact key, its value, and the rule — not
"the model thought so". An auditor who disagrees can point at the rule. That is
worth more here than the coverage a model would add, because the output is a
scoping decision a person has to defend.

**It states what it cannot see.** The taxonomy covers risks inferable from a
public profile. Fraud, culture, concentration, litigation and anything internal
are not in it and cannot be, so the payload names that gap rather than letting an
empty category read as a clean one.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select

from .briefing import OrganizationBrief, organization_brief
from .db.models import Risk
from .db.session import Database

#: Bumped whenever a rule is added, removed or reworded. A proposal records the
#: version that produced it, so a register assembled last quarter can be shown
#: to have been assembled under different rules rather than silently re-derived.
TAXONOMY_VERSION = "assurance.risk_taxonomy.v1"


@dataclass(frozen=True)
class RiskRule:
    """One candidate risk, and the company attribute that makes it apply."""

    code: str
    title: str
    #: Why this company, in terms a reviewer can disagree with.
    rationale: str
    #: The Audit Pack that would test it. ``None`` means the platform has no
    #: released methodology for this risk yet, which is a fact worth showing: the
    #: risk is real and the platform cannot currently audit it.
    suggested_pack: str | None
    #: The profile facts consulted. Empty means the rule applies to any company.
    trigger_keys: tuple[str, ...]
    #: Given the accepted facts, does this apply?
    applies: Callable[[Mapping[str, Any]], bool]
    category: str = "operational"


def _truthy(facts: Mapping[str, Any], key: str) -> bool:
    value = facts.get(key)
    if isinstance(value, str):
        return value.strip().lower() in {"true", "yes", "1"}
    return bool(value)


def _contains(facts: Mapping[str, Any], key: str, *needles: str) -> bool:
    value = facts.get(key)
    if value is None:
        return False
    if isinstance(value, (list, tuple)):
        haystack = " ".join(str(item) for item in value)
    else:
        haystack = str(value)
    haystack = haystack.lower()
    return any(needle.lower() in haystack for needle in needles)


def _always(_: Mapping[str, Any]) -> bool:
    return True


#: The taxonomy. Ordered so the register reads in a sensible order rather than in
#: dictionary order, and kept small on purpose — a hundred generic risks is a
#: register nobody reads, which is the failure mode of every risk library.
TAXONOMY: tuple[RiskRule, ...] = (
    RiskRule(
        code="AST-R-SCM",
        title="Unauthorised production change",
        rationale=(
            "The company runs a customer-facing platform, so a change reaching "
            "production without review degrades a service other people depend on."
        ),
        suggested_pack="software-change-management@2.0.0",
        trigger_keys=("public.operates_customer_facing_platform",),
        applies=lambda facts: _truthy(facts, "public.operates_customer_facing_platform"),
        category="technology",
    ),
    RiskRule(
        code="AST-R-IAM",
        title="Terminated worker retains access",
        rationale=(
            "Every employer has leavers, and access that outlives employment is "
            "the most common way an internal system is reached by someone with no "
            "current right to it."
        ),
        suggested_pack="identity-access@1.0.0",
        trigger_keys=(),
        applies=_always,
        category="technology",
    ),
    RiskRule(
        code="AST-R-DATA",
        title="Customer data exposed through a misconfigured store",
        rationale=(
            "The profile says personal data is processed. Where it is processed on "
            "someone else's infrastructure, the configuration is the control."
        ),
        suggested_pack=None,
        trigger_keys=("public.processes_personal_data", "public.cloud_provider"),
        applies=lambda facts: _truthy(facts, "public.processes_personal_data"),
        category="data",
    ),
    RiskRule(
        code="AST-R-PAM",
        title="Standing privilege accumulates without justification",
        rationale=(
            "Infrastructure is operated on a public cloud, where administrative "
            "roles are cheap to grant and rarely reviewed after the incident that "
            "prompted them."
        ),
        suggested_pack="privileged-access@1.0.0",
        trigger_keys=("public.cloud_provider",),
        applies=lambda facts: bool(facts.get("public.cloud_provider")),
        category="technology",
    ),
    RiskRule(
        code="AST-R-SLA",
        title="Contractual service commitments are not met as contracted",
        rationale=(
            "The company sells a service under contract, so its obligations live "
            "in customer agreements. Where an internal procedure and a signed "
            "amendment disagree, the internal system is the one that will be "
            "believed and the contract is the one that binds."
        ),
        suggested_pack=None,
        trigger_keys=("industry.primary", "public.industry"),
        applies=lambda facts: _contains(
            facts, "industry.primary", "saas", "platform", "service"
        )
        or _contains(facts, "public.industry", "saas", "platform", "service"),
        category="commercial",
    ),
    RiskRule(
        code="AST-R-ATTEST",
        title="Public security commitments are not supported by tested controls",
        rationale=(
            "The company publicly claims a certification or attestation. A claim "
            "the control environment cannot evidence is a misstatement to "
            "customers before it is a control failure."
        ),
        suggested_pack=None,
        trigger_keys=("public.security_commitments",),
        applies=lambda facts: bool(facts.get("public.security_commitments")),
        category="compliance",
    ),
    RiskRule(
        code="AST-R-PRIVACY",
        title="Cross-border personal data transfer without a lawful basis",
        rationale=(
            "Personal data is processed and the company operates across borders, "
            "at least one of them inside the EU, which makes the transfer basis a "
            "control rather than a legal footnote."
        ),
        suggested_pack=None,
        trigger_keys=("public.processes_personal_data", "public.operating_locations"),
        applies=lambda facts: _truthy(facts, "public.processes_personal_data")
        and _contains(
            facts, "public.operating_locations", "FR", "DE", "IE", "NL", "ES", "IT", "BE"
        ),
        category="compliance",
    ),
    RiskRule(
        code="AST-R-VENDOR",
        title="Critical vendor fails without a tested continuity path",
        rationale=(
            "The service depends on infrastructure the company does not own. "
            "Dependency is visible from the profile; whether the fallback has ever "
            "been tested is not, which is why this scores low on confidence."
        ),
        suggested_pack=None,
        trigger_keys=("public.cloud_provider",),
        applies=lambda facts: bool(facts.get("public.cloud_provider")),
        category="operational",
    ),
    RiskRule(
        code="AST-R-EXPENSE",
        title="Employee expense claims outside policy",
        rationale=(
            "Any organisation with employees reimburses them. Included because a "
            "register that contains only the interesting risks is not a universe."
        ),
        suggested_pack="procure-to-pay@1.0.0",
        trigger_keys=(),
        applies=_always,
        category="financial",
    ),
)

#: Said out loud in the payload. An empty category on a screen reads as "nothing
#: here"; it has to read as "this method cannot see here".
BLIND_SPOTS: tuple[str, ...] = (
    "Fraud and management override — not inferable from a public profile.",
    "Culture, incentives and tone — require people, not documents.",
    "Customer and revenue concentration — needs internal financial data.",
    "Litigation, regulatory action and anything under privilege.",
    "Anything specific to this company that its public presence does not show.",
)


@dataclass(frozen=True)
class RiskProposal:
    """One candidate risk, with where it came from and what happened to it."""

    code: str
    title: str
    rationale: str
    category: str
    suggested_pack: str | None
    triggers: tuple[Mapping[str, Any], ...]
    #: "accepted" — a human registered it; "rejected" — registered and retired;
    #: "proposed" — derived and not yet decided.
    status: str = "proposed"
    risk_id: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "title": self.title,
            "rationale": self.rationale,
            "category": self.category,
            "suggested_pack": self.suggested_pack,
            "auditable": self.suggested_pack is not None,
            "triggers": [dict(item) for item in self.triggers],
            "status": self.status,
            "risk_id": self.risk_id,
        }


def propose_risks(
    organization: OrganizationBrief | None,
    *,
    registered: Sequence[Any] = (),
) -> list[RiskProposal]:
    """Derive the candidate universe, and mark what a human has already decided.

    ``registered`` is the tenant's ``Risk`` rows. Matching on code is what lets
    the screen distinguish a proposal from an accepted risk, which is the whole
    point of showing this: a list where the platform's suggestions and the
    auditor's decisions are indistinguishable would be worse than no list.
    """
    if organization is None:
        return []

    facts = {item.key: item.value for item in organization.facts}
    claim_types = {item.key: item.claim_type for item in organization.facts}
    by_code = {getattr(item, "code", None): item for item in registered}

    proposals: list[RiskProposal] = []
    for rule in TAXONOMY:
        if not rule.applies(facts):
            continue
        existing = by_code.get(rule.code)
        proposals.append(
            RiskProposal(
                code=rule.code,
                title=rule.title,
                rationale=rule.rationale,
                category=rule.category,
                suggested_pack=rule.suggested_pack,
                triggers=tuple(
                    {
                        "fact_key": key,
                        "value": facts.get(key),
                        "claim_type": claim_types.get(key, "unknown"),
                    }
                    for key in rule.trigger_keys
                    if key in facts
                ),
                status=(
                    "proposed"
                    if existing is None
                    else ("rejected" if getattr(existing, "status", "") == "retired" else "accepted")
                ),
                risk_id=getattr(existing, "risk_id", None) if existing is not None else None,
            )
        )
    return proposals


def discovered_universe(database: Database, tenant_id: str) -> dict[str, Any]:
    """The read model: what onboarding proposed, and what a human did with it."""

    organization = organization_brief(database, tenant_id)
    with database.read_session() as session:
        registered = list(session.scalars(select(Risk).where(Risk.tenant_id == tenant_id)))

    proposals = propose_risks(organization, registered=registered)
    derived_codes = {item.code for item in proposals}
    return {
        "taxonomy_version": TAXONOMY_VERSION,
        "method": "deterministic rules over the approved organization profile",
        "organization": (
            {
                "legal_name": organization.legal_name,
                "industry": organization.industry,
                "headquarters_country": organization.headquarters_country,
                "status": organization.status,
                "fact_count": len(organization.facts),
                "inferences": list(organization.inference_keys),
            }
            if organization is not None
            else None
        ),
        "proposals": [item.as_dict() for item in proposals],
        "totals": {
            "proposed": len(proposals),
            "accepted": sum(1 for item in proposals if item.status == "accepted"),
            "awaiting_decision": sum(1 for item in proposals if item.status == "proposed"),
            "auditable_today": sum(1 for item in proposals if item.suggested_pack),
            # Registered risks the taxonomy did not derive. These are the ones a
            # person added because they know the company, and counting them is the
            # honest measure of how much of the register this method actually
            # produces.
            "added_by_a_human": sum(1 for item in registered if item.code not in derived_codes),
        },
        "blind_spots": list(BLIND_SPOTS),
        "caveat": (
            "Proposed from a public profile by declared rules, not by a model. "
            "Nothing here is registered until a person accepts it."
            if organization is not None
            else "No approved organization profile in this tenant; nothing can be derived."
        ),
    }
