"""What an agent is actually told to do.

Every agent in this fleet ships a system prompt describing its profession — its
mandate, its non-goals, the evidence taxonomy, when to abstain. That prompt is
identical for every engagement it will ever run, because it is a statement about
the role rather than about the work. The *task* is the other half, and until this
module existed the governed worker supplied it as
``f"Execute task {lease.task_key} for engagement {lease.engagement_id}."`` — a
key and an identifier, from which no model can tell what a good answer looks
like.

Everything a proper briefing needs is already canonical state and was simply
never assembled:

* the **Audit Pack procedure** for this step carries the action in the pack
  author's own words, along with the control, the criteria those controls cite,
  and the quality rules the pack will be reviewed against — the compiler puts all
  of it in ``execution_policy``;
* the **organization profile** carries the industry, the business model, where
  the company operates, what it has publicly committed to, and which of those
  facts a human overruled — with each one's claim type, so the briefing can say
  which are observed and which are the platform's own inference;
* the **engagement** carries the period and the systems in scope;
* the **lease** carries the human gate the task must stop at.

So the briefing is composed, never authored. Three properties follow, and each
one is a test in ``tests/test_briefing.py``:

**It adapts to the company by construction.** A different tenant has a different
profile, so the same pack step produces a different briefing. Nothing has to be
rewritten per customer, which is the difference between a product and a bespoke
deployment.

**It adapts to the audit type by construction.** The pack decides the objective,
the criteria and the procedure; a privileged-access engagement and a
change-management engagement brief differently because their packs differ, not
because a branch here knows about them.

**It never asserts what it was not given.** An absent profile produces a briefing
that says the profile is absent, because an agent told nothing about the company
must know that it was told nothing — inventing a plausible industry is exactly
the failure the claim taxonomy exists to prevent.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from sqlalchemy import desc, select

from .db.models import Engagement, OrganizationFact, OrganizationProfile
from .db.session import Database

#: What a completed task of each type has to contain, in the reviewer's terms.
#:
#: Keyed on the task type the pack declares, so a new pack that uses an existing
#: type inherits the contract, and an unknown type falls back to the generic
#: entry rather than silently briefing nothing. These are deliberately about the
#: *shape of the answer* — the professional standards live in the agent's own
#: system prompt and are not repeated here.
DEFINITION_OF_DONE: dict[str, tuple[str, ...]] = {
    "scoping": (
        "Name the population boundary in terms a reviewer can re-derive: which "
        "systems, which period, and what is excluded.",
        "State the materiality basis as a number with the input it came from.",
        "An exclusion with no stated reason is a scope gap, not a scope decision.",
    ),
    "connector_collection": (
        "Register every object collected as evidence with its source locator and "
        "digest. Collection is not complete because a connector returned success.",
        "Report the count seen against the count collected. A silent difference "
        "between them is the population defect that invalidates everything after.",
        "Do not interpret what you collected. Custody is the task; conclusions "
        "belong to the steps that follow.",
    ),
    "control_test": (
        "Execute the signed procedure this step is pinned to. Do not compute the "
        "result yourself, and do not reason from a sample it did not draw.",
        "Reconcile the population: the count tested against the count expected "
        "for the period, and say which records the difference is.",
        "Classify each exception. An exception is not yet a finding.",
        "If the procedure cannot run, that is a technical failure and it is "
        "reported as one. It is never a conclusion about the control.",
    ),
    "reporting": (
        "Every material sentence must resolve to accepted evidence through a "
        "claim. A sentence that cannot is removed, not softened.",
        "Carry the limitations and the uncovered scope into the report. A report "
        "that reads as complete when the plan says it is not is a misstatement.",
    ),
    "agent": (
        "Cite accepted evidence for every material statement.",
        "State what you looked for and did not find, not only what you found.",
    ),
}

#: Additions keyed on the agent role, for steps whose contract is about
#: independence or challenge rather than about the artefact produced.
ROLE_ADDENDA: dict[str, tuple[str, ...]] = {
    "skeptic": (
        "Your task is to fail the exception, not to confirm it. Search for the "
        "approved exception, the period or timezone boundary, the superseded "
        "document and the compensating control before you agree anything stands.",
        "An exception you could not break is worth more than one you did not try "
        "to. Record what you tried, including the attempts that found nothing.",
    ),
    "quality-reviewer": (
        "Review the work, not the conclusion. Support, population reconciliation, "
        "criteria citation, disclosure of contradictory evidence, and whether the "
        "severity meets its computed materiality floor.",
        "Agreeing with a conclusion that is not supported is the failure this "
        "step exists to prevent.",
    ),
    "retest-verification": (
        "You must be independent of the identity that raised the finding and of "
        "the remediation owner. Retest on fresh evidence; evidence collected "
        "before the remediation cannot show that it worked.",
    ),
    "finding-adjudicator": (
        "Condition, criteria, cause, consequence and limitation, each separately "
        "and each cited. A finding that merges cause into condition cannot be "
        "argued with by the person who has to fix it.",
    ),
    "scope-materiality": (
        "Materiality is a number with a derivation, not an adjective. Show the "
        "input, the basis and the threshold.",
    ),
}

#: Fact keys whose values are worth putting in front of an agent, in the order a
#: briefing reads best. Anything else in the profile stays available through
#: ``organization.context.read`` rather than being pushed into every prompt.
_PROFILE_FACT_ORDER = (
    "industry.primary",
    "public.industry",
    "public.operating_locations",
    "public.operates_customer_facing_platform",
    "public.processes_personal_data",
    "public.security_commitments",
    "public.cloud_provider",
)


@dataclass(frozen=True)
class OrganizationFactView:
    """One attributed fact about the company, with the class of claim it is."""

    key: str
    value: Any
    claim_type: str
    source_type: str | None = None
    confidence: float | None = None

    def rendered(self) -> str:
        value = self.value
        if isinstance(value, (list, tuple)):
            value = ", ".join(str(item) for item in value)
        elif isinstance(value, bool):
            value = "yes" if value else "no"
        return f"{self.key} = {value}  [{self.claim_type}]"


@dataclass(frozen=True)
class OrganizationBrief:
    """The canonical company profile, reduced to what an agent needs stated."""

    legal_name: str
    industry: str | None = None
    headquarters_country: str | None = None
    primary_domain: str | None = None
    status: str = "unknown"
    facts: tuple[OrganizationFactView, ...] = ()
    corrections: tuple[str, ...] = ()

    @property
    def inference_keys(self) -> tuple[str, ...]:
        return tuple(item.key for item in self.facts if item.claim_type == "inference")


@dataclass(frozen=True)
class TaskBrief:
    """Everything the briefing is composed from. No field is invented here."""

    task_key: str
    task_type: str
    agent_role: str
    engagement_id: str
    engagement_code: str | None = None
    engagement_title: str | None = None
    period: tuple[date | None, date | None] | None = None
    in_scope_systems: tuple[str, ...] = ()
    organization: OrganizationBrief | None = None
    objective: str | None = None
    action: str | None = None
    control: Mapping[str, Any] | None = None
    criteria: tuple[Mapping[str, Any], ...] = ()
    quality_rules: tuple[str, ...] = ()
    control_test: str | None = None
    pack_reference: str | None = None
    human_gate: str | None = None
    depends_on: tuple[str, ...] = ()
    notes: tuple[str, ...] = field(default_factory=tuple)


def _bullets(lines: Sequence[str]) -> str:
    return "\n".join(f"- {line}" for line in lines if line)


def _period_text(period: tuple[date | None, date | None] | None) -> str | None:
    if not period:
        return None
    start, end = period
    if not start or not end:
        return None
    return f"{start.isoformat()} to {end.isoformat()} inclusive"


def render_briefing(brief: TaskBrief) -> str:
    """Compose the task instruction. Pure, so it can be tested without a database."""

    sections: list[str] = []

    headline = brief.action or f"Complete the {brief.task_key} step."
    sections.append(f"YOUR TASK\n{headline.strip()}")

    where: list[str] = []
    if brief.engagement_code or brief.engagement_title:
        label = " · ".join(
            part for part in (brief.engagement_code, brief.engagement_title) if part
        )
        where.append(f"Engagement: {label}")
    if brief.objective:
        where.append(f"What the engagement is for: {brief.objective.strip()}")
    if brief.pack_reference:
        where.append(f"Methodology: {brief.pack_reference} (signed, version-pinned)")
    if period := _period_text(brief.period):
        where.append(f"Audit period: {period}. Evidence outside it is out of scope.")
    if brief.in_scope_systems:
        where.append(f"Systems in scope: {', '.join(brief.in_scope_systems)}")
    if brief.depends_on:
        where.append(
            "This step follows: "
            + ", ".join(brief.depends_on)
            + ". Their outputs are canonical; do not redo their work."
        )
    if where:
        sections.append("THE ENGAGEMENT\n" + _bullets(where))

    if brief.organization is not None:
        sections.append("THE ORGANISATION\n" + _organization_section(brief.organization))
    else:
        sections.append(
            "THE ORGANISATION\n"
            "- No approved organization profile exists in this tenant. You have not "
            "been told what this company does. Do not assume an industry, a "
            "regulatory regime or a system landscape; where the answer depends on "
            "one, that is a scope limitation and you must say so."
        )

    if brief.control:
        control_lines = [f"Control {brief.control.get('control_id') or ''}".strip()]
        if risk := brief.control.get("risk"):
            control_lines.append(f"Risk it exists to address: {str(risk).strip()}")
        if expected := brief.control.get("expected"):
            control_lines.append(f"What operating effectively means: {str(expected).strip()}")
        sections.append("THE CONTROL UNDER TEST\n" + _bullets(control_lines))

    if brief.criteria:
        rendered = []
        for item in brief.criteria:
            code = str(item.get("criteria_id") or item.get("code") or "").strip()
            text = str(item.get("text") or "").strip()
            citation = str(item.get("citation") or "").strip()
            line = f"{code}: {text}" if text else code
            if citation:
                line = f"{line}  [{citation}]"
            rendered.append(line)
        sections.append(
            "THE CRITERIA YOU ARE TESTING AGAINST\n"
            + _bullets(rendered)
            + "\nCite these identifiers. Do not paraphrase a criterion into a "
            "standard you were not given."
        )

    if brief.control_test:
        sections.append(
            "THE SIGNED PROCEDURE\n"
            f"- Execute {brief.control_test} through the tests.execute tool.\n"
            "- The release declares the population. You cannot choose it, and a "
            "conclusion reasoned out instead of run is inadmissible."
        )

    done = list(DEFINITION_OF_DONE.get(brief.task_type, DEFINITION_OF_DONE["agent"]))
    done.extend(ROLE_ADDENDA.get(brief.agent_role, ()))
    sections.append(f"WHAT DONE LOOKS LIKE ON A {brief.task_type.upper()} STEP\n" + _bullets(done))

    if brief.quality_rules:
        sections.append(
            "HOW THIS WILL BE REVIEWED\n"
            + _bullets(brief.quality_rules)
            + "\nThe review is performed by a different agent identity, against "
            "your output and the evidence — not by you, and not on trust."
        )

    if brief.human_gate:
        sections.append(
            "WHERE YOU STOP\n"
            f"- This task carries the human gate '{brief.human_gate.replace('_', ' ')}'. "
            "Produce the material for that decision and stop. Do not record it as "
            "made, and do not act as though it has been."
        )

    if brief.notes:
        sections.append("ALSO\n" + _bullets(brief.notes))

    return "\n\n".join(sections)


def _organization_section(organization: OrganizationBrief) -> str:
    lines = [f"{organization.legal_name} — the canonical entity for this engagement."]
    descriptors = [
        organization.industry,
        f"headquartered in {organization.headquarters_country}"
        if organization.headquarters_country
        else None,
        organization.primary_domain,
    ]
    if any(descriptors):
        lines.append(", ".join(item for item in descriptors if item))
    for fact in organization.facts:
        lines.append(fact.rendered())
    if organization.corrections:
        lines.extend(
            f"A reviewer overruled a proposed fact: {item}" for item in organization.corrections
        )
    lines.append(
        "Facts marked [inference] are this platform's reading of a public source, "
        "not something the company stated. Do not cite one as a management "
        "assertion, and do not let one carry a material conclusion on its own."
    )
    return _bullets(lines)


# -- loading from canonical state ------------------------------------------------


def organization_brief(database: Database, tenant_id: str) -> OrganizationBrief | None:
    """Read the canonical profile, or None when the tenant has not onboarded."""

    with database.read_session() as session:
        profile = session.scalar(
            select(OrganizationProfile)
            .where(OrganizationProfile.tenant_id == tenant_id)
            .order_by(desc(OrganizationProfile.version))
            .limit(1)
        )
        if profile is None:
            return None
        rows = list(
            session.scalars(
                select(OrganizationFact).where(
                    OrganizationFact.tenant_id == tenant_id,
                    OrganizationFact.profile_id == profile.profile_id,
                )
            )
        )

    order = {key: index for index, key in enumerate(_PROFILE_FACT_ORDER)}
    accepted = [row for row in rows if row.status == "accepted" and row.fact_key in order]
    accepted.sort(key=lambda row: order[row.fact_key])
    corrections = [
        f"{row.fact_key} was proposed as {row.value_json!r} and rejected"
        for row in rows
        if row.status == "corrected"
    ]
    return OrganizationBrief(
        legal_name=profile.legal_name,
        industry=profile.industry,
        headquarters_country=profile.headquarters_country,
        primary_domain=profile.primary_domain,
        status=profile.status,
        facts=tuple(
            OrganizationFactView(
                key=row.fact_key,
                value=row.value_json,
                claim_type=row.claim_type,
                source_type=row.source_type,
                confidence=row.confidence,
            )
            for row in accepted
        ),
        corrections=tuple(corrections),
    )


def brief_from_lease(
    lease: Any,
    *,
    organization: OrganizationBrief | None = None,
    engagement: Mapping[str, Any] | None = None,
) -> TaskBrief:
    """Assemble the brief from a task lease and the state around it.

    ``lease`` is duck-typed rather than imported so this module does not depend
    on the orchestrator, which lets the composition be tested with a plain object.
    """

    policy: Mapping[str, Any] = getattr(lease, "execution_policy", None) or {}
    engagement = engagement or {}
    period = engagement.get("period")
    return TaskBrief(
        task_key=getattr(lease, "task_key", "") or "",
        task_type=str(getattr(lease, "task_type", "") or "agent"),
        agent_role=str(getattr(lease, "assigned_agent_role", "") or ""),
        engagement_id=getattr(lease, "engagement_id", "") or "",
        engagement_code=engagement.get("code"),
        engagement_title=engagement.get("title"),
        period=period if isinstance(period, tuple) else None,
        in_scope_systems=tuple(engagement.get("in_scope_systems") or ()),
        organization=organization,
        objective=policy.get("objective"),
        action=policy.get("action"),
        control=policy.get("control") if isinstance(policy.get("control"), Mapping) else None,
        criteria=tuple(
            item for item in (policy.get("criteria_detail") or ()) if isinstance(item, Mapping)
        )
        or tuple({"criteria_id": code} for code in (policy.get("criteria") or ())),
        quality_rules=tuple(str(item) for item in (policy.get("quality_rules") or ())),
        control_test=policy.get("control_test"),
        pack_reference=policy.get("pack_reference"),
        human_gate=getattr(lease, "human_gate", None),
        depends_on=tuple(str(item) for item in (policy.get("depends_on") or ())),
    )


class DatabaseInstructionLoader:
    """The governed worker's instruction loader, reading canonical state.

    Registered on :class:`~assuranceos.governance.worker.GovernedAgentTaskHandler`,
    this is what turns a lease into a briefing. The organization profile is
    cached per tenant for the life of the loader because it is canonical state
    that changes at profile-version boundaries, not within a run; the engagement
    is read per task because its period and scope are what the task is bounded by.
    """

    def __init__(self, database: Database):
        self.database = database
        self._organizations: dict[str, OrganizationBrief | None] = {}

    def __call__(self, lease: Any) -> str:
        tenant_id = getattr(lease, "tenant_id", "")
        if tenant_id not in self._organizations:
            self._organizations[tenant_id] = organization_brief(self.database, tenant_id)
        return render_briefing(
            brief_from_lease(
                lease,
                organization=self._organizations[tenant_id],
                engagement=self._engagement(tenant_id, getattr(lease, "engagement_id", "")),
            )
        )

    def _engagement(self, tenant_id: str, engagement_id: str) -> dict[str, Any]:
        if not engagement_id:
            return {}
        with self.database.read_session() as session:
            row = session.scalar(
                select(Engagement).where(
                    Engagement.tenant_id == tenant_id,
                    Engagement.engagement_id == engagement_id,
                )
            )
            if row is None:
                return {}
            return {
                "code": row.code,
                "title": row.title,
                "period": (row.period_start, row.period_end),
                "in_scope_systems": list(row.scope_json.get("in_scope_systems") or ())
                if isinstance(row.scope_json, dict)
                else [],
            }
