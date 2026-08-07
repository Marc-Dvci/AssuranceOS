"""Types for the claim graph and evidence-grounded reporting.

A report is the only artefact most people ever see. Everything upstream — the
signed pack, the governed agent, the deterministic test, the independent retest —
exists so that the sentences in it are true. This module's job is to make the link
between a sentence and the evidence under it a *structure* rather than a
convention, so a sentence with nothing under it cannot be rendered by accident.

The central distinction is between a **material** claim and an incidental one. An
audit report contains a great deal of context, framing and description that nobody
needs to evidence. It also contains a small number of statements the reader will
act on. Only the second kind is gated, and the gate is absolute: a material claim
either resolves to accepted evidence, or it carries a stated limitation, or the
report does not render.
"""

from __future__ import annotations

from datetime import date, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ClaimType(StrEnum):
    """What kind of statement a claim is.

    The distinction between an observation and an opinion is not cosmetic: they
    require different support. An observation must resolve to evidence of the
    thing observed; an opinion must resolve to the observations it rests on.
    """

    OBSERVATION = "observation"
    CONCLUSION = "conclusion"
    OPINION = "opinion"
    RECOMMENDATION = "recommendation"
    CONTEXT = "context"
    LIMITATION = "limitation"


class EvidenceRelationship(StrEnum):
    SUPPORTS = "supports"
    CONTRADICTS = "contradicts"
    QUALIFIES = "qualifies"


class ReportType(StrEnum):
    ENGAGEMENT_REPORT = "engagement_report"
    EXECUTIVE_SUMMARY = "executive_summary"
    AUDIT_COMMITTEE_SUMMARY = "audit_committee_summary"
    FINDINGS_REGISTER = "findings_register"
    REMEDIATION_DASHBOARD = "remediation_dashboard"
    COVERAGE_AND_LIMITATIONS = "coverage_and_limitations"
    TECHNICAL_EVIDENCE_PACKAGE = "technical_evidence_package"


class ClaimInput(BaseModel):
    """A statement the report will make, and what it rests on.

    ``material`` defaults to True. The safe direction: a claim whose materiality
    nobody considered is treated as one the reader will act on, so forgetting to
    mark something material produces a refusal rather than an unsupported
    sentence.
    """

    model_config = ConfigDict(extra="forbid")

    key: str = Field(min_length=1, max_length=128, pattern=r"^[a-z0-9][a-z0-9._-]*$")
    claim_type: ClaimType
    statement: str = Field(min_length=10)
    material: bool = True
    confidence: float = Field(default=0.0, ge=0, le=1)
    supporting_evidence_ids: list[str] = Field(default_factory=list)
    contradicting_evidence_ids: list[str] = Field(default_factory=list)
    qualifying_evidence_ids: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    finding_id: str | None = None
    task_id: str | None = None

    @model_validator(mode="after")
    def a_limitation_claim_is_not_gated_on_evidence(self) -> "ClaimInput":
        """A stated limitation is the disclosure, not a claim needing support.

        Requiring evidence for "we could not test the archived population" would
        make disclosure harder than silence, which is the wrong incentive.
        """
        if self.claim_type is ClaimType.LIMITATION and self.material:
            object.__setattr__(self, "material", False)
        return self


class ReuseJustification(BaseModel):
    """Why evidence gathered for one purpose may support another.

    Reuse is normal and is not free. Evidence collected for a different engagement
    or a different period was collected under a different scope, and carrying it
    across without saying so is how a conclusion about this year quietly rests on
    last year's sample.
    """

    model_config = ConfigDict(extra="forbid")

    evidence_id: str
    rationale: str = Field(min_length=20, max_length=2000)
    approved_by: str = Field(min_length=1, max_length=128)


class EvidencePolicy(BaseModel):
    """When evidence is current enough to support a claim."""

    model_config = ConfigDict(extra="forbid")

    #: Evidence collected more than this long before the report stops being
    #: current on its own and needs a limitation.
    freshness_days: int = Field(default=400, ge=1)
    #: Whether evidence collected outside the audit period may support a claim
    #: about that period without a recorded reuse justification.
    allow_out_of_period: bool = False
    #: Whether evidence from a different engagement may be reused without one.
    allow_cross_engagement: bool = False
    #: Whether tainted evidence — anything a guardrail flagged — may be the sole
    #: support for a material claim. Never, by default.
    allow_tainted_sole_support: bool = False


class EvidenceView(BaseModel):
    """What retrieval knows about one evidence record."""

    evidence_id: str
    engagement_id: str | None
    source_type: str
    source_locator: str
    classification: str
    accepted: bool
    tainted: bool
    integrity_status: str
    deleted: bool
    collected_at: datetime
    source_time: datetime | None
    content_sha256: str

    def is_admissible(self) -> bool:
        """Whether this record can support anything at all.

        Deleted, unaccepted, or integrity-failed evidence supports nothing. This
        is not a judgement about relevance — it is about whether the record still
        stands for the thing it claims to be.
        """
        return (
            self.accepted
            and not self.deleted
            and self.integrity_status in {"verified", "unverified"}
        )


class ClaimIssue(BaseModel):
    """One reason a claim cannot be rendered as it stands."""

    claim_key: str
    code: str
    detail: str
    evidence_id: str | None = None


class RenderedClaim(BaseModel):
    """A claim as it appears in a rendered report, with its resolved support."""

    key: str
    claim_type: ClaimType
    statement: str
    material: bool
    confidence: float
    supporting_evidence: list[dict[str, Any]] = Field(default_factory=list)
    contradicting_evidence: list[dict[str, Any]] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    finding_id: str | None = None


class ReportSection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str = Field(min_length=1, max_length=128)
    heading: str = Field(min_length=1, max_length=255)
    claim_keys: list[str] = Field(default_factory=list)
    narrative: str = ""


class ReportTemplate(BaseModel):
    """The shape of a report, and which claim kinds each section may carry.

    Versioned, because a report issued under one template and one issued under
    another are not comparable, and an audit committee comparing them needs to
    know which it is looking at.
    """

    model_config = ConfigDict(extra="forbid")

    template_id: str = Field(min_length=1, max_length=64)
    version: str = Field(min_length=1, max_length=32)
    report_type: ReportType
    title: str = Field(min_length=1, max_length=255)
    sections: list[ReportSection] = Field(min_length=1)
    #: Sections that must contain at least one claim. A findings register with no
    #: findings section is a template error, not an empty report.
    required_sections: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def required_sections_must_exist(self) -> "ReportTemplate":
        keys = {section.key for section in self.sections}
        missing = sorted(set(self.required_sections) - keys)
        if missing:
            raise ValueError(f"required sections are not defined: {', '.join(missing)}")
        if len(keys) != len(self.sections):
            raise ValueError("section keys are not unique")
        return self

    @property
    def reference(self) -> str:
        return f"{self.template_id}@{self.version}"


class ReportRequest(BaseModel):
    """Everything a render depends on, supplied rather than looked up."""

    model_config = ConfigDict(extra="forbid")

    template: ReportTemplate
    claims: list[ClaimInput] = Field(min_length=1)
    period_start: date
    period_end: date
    as_at: date
    policy: EvidencePolicy = Field(default_factory=EvidencePolicy)
    reuse_justifications: list[ReuseJustification] = Field(default_factory=list)
    prepared_by: str = Field(min_length=1, max_length=128)

    @model_validator(mode="after")
    def claims_must_cover_the_template(self) -> "ReportRequest":
        supplied = {claim.key for claim in self.claims}
        if len(supplied) != len(self.claims):
            raise ValueError("claim keys are not unique")
        referenced = {
            key for section in self.template.sections for key in section.claim_keys
        }
        missing = sorted(referenced - supplied)
        if missing:
            raise ValueError(
                f"the template references claims that were not supplied: {', '.join(missing)}"
            )
        return self


class RenderedReport(BaseModel):
    """A report that passed every gate, and the digest that identifies it."""

    report_id: str
    template: str
    report_type: ReportType
    title: str
    tenant_id: str
    engagement_id: str
    period_start: date
    period_end: date
    as_at: date
    prepared_by: str
    sections: list[dict[str, Any]]
    claims: list[RenderedClaim]
    limitations: list[str]
    evidence_index: list[dict[str, Any]]
    document_sha256: str
    signature: dict[str, str] | None = None
