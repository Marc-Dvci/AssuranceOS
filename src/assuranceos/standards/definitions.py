"""Typed contracts for standards, criteria, and Audit Packs.

An Audit Pack is the unit of methodology in AssuranceOS: what is being tested,
against which criteria, by which procedures, with which gates. Everything in this
module exists so a pack can be *compiled* rather than interpreted — the engagement
that runs is a deterministic function of a signed pack and an organisation
context, and two compilations of the same inputs produce the same graph.

The manifest is parsed into these types rather than passed around as a dict. A
dict makes every consumer responsible for the pack's shape; a typed manifest makes
the pack responsible for it, and moves the failure to load time where it names the
pack instead of to run time where it names a KeyError.
"""

from __future__ import annotations

from datetime import date
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ClaimStrength(StrEnum):
    """How firmly a criterion binds.

    A pack that cannot distinguish a regulatory requirement from an internal
    preference will report both as findings of the same kind, which is how an
    audit function loses the argument about which findings matter.
    """

    MANDATORY = "mandatory"
    RECOMMENDED = "recommended"
    INFORMATIVE = "informative"


class CrosswalkRelation(StrEnum):
    """How one criterion relates to another in a different standard."""

    EQUIVALENT = "equivalent"
    SUBSET = "subset"
    SUPERSET = "superset"
    RELATED = "related"
    CONFLICTS = "conflicts"


class StandardInput(BaseModel):
    """A versioned body of criteria, with its licensing position.

    ``entitlement_required`` is the field that matters operationally. Several
    standards bodies licence their text, and an audit platform that reproduces
    licensed criteria for a tenant with no entitlement has a legal problem, not a
    product feature. It is declared here so the compiler can refuse.
    """

    model_config = ConfigDict(extra="forbid")

    code: str = Field(min_length=2, max_length=64)
    name: str = Field(min_length=2, max_length=255)
    issuer: str = Field(min_length=2, max_length=255)
    version: str = Field(min_length=1, max_length=32)
    jurisdiction: str = Field(default="global", max_length=64)
    licence: str = Field(default="internal", max_length=128)
    entitlement_required: bool = False
    effective_from: date | None = None
    effective_to: date | None = None
    source_url: str | None = Field(default=None, max_length=1024)

    @model_validator(mode="after")
    def window_must_be_ordered(self) -> "StandardInput":
        if self.effective_to and self.effective_from and self.effective_to < self.effective_from:
            raise ValueError("effective_to precedes effective_from")
        return self


class CriterionInput(BaseModel):
    """One requirement inside a standard, with the citation that locates it.

    The citation is required. A finding that says "contrary to policy" without
    saying where in the policy is not a finding anyone can act on, and the place to
    enforce that is where the criterion is defined rather than where it is quoted.
    """

    model_config = ConfigDict(extra="forbid")

    code: str = Field(min_length=2, max_length=64)
    text: str = Field(min_length=10)
    citation: str = Field(min_length=2, max_length=512)
    strength: ClaimStrength = ClaimStrength.MANDATORY
    requirement_ref: str | None = Field(default=None, max_length=128)
    effective_from: date | None = None
    effective_to: date | None = None

    @model_validator(mode="after")
    def window_must_be_ordered(self) -> "CriterionInput":
        if self.effective_to and self.effective_from and self.effective_to < self.effective_from:
            raise ValueError("effective_to precedes effective_from")
        return self

    def effective_over(self, start: date, end: date) -> bool:
        """Whether this criterion covers the whole of an audit period.

        Whole, not partial. A criterion that came into force halfway through the
        period cannot support a conclusion about the whole of it, and quietly
        accepting the overlap is how an audit ends up testing months the rule did
        not apply to.
        """
        if self.effective_from and self.effective_from > start:
            return False
        if self.effective_to and self.effective_to < end:
            return False
        return True


class CrosswalkInput(BaseModel):
    """A mapping between criteria in two standards.

    ``rationale`` is mandatory because a crosswalk is a judgement. "SOC 2 CC8.1 is
    equivalent to ISO 27001 A.8.32" is a claim someone made, and an assurance map
    built from unattributed equivalences is a map of what somebody assumed.
    """

    model_config = ConfigDict(extra="forbid")

    source_criterion: str
    target_criterion: str
    relation: CrosswalkRelation
    rationale: str = Field(min_length=10, max_length=2000)
    asserted_by: str = Field(min_length=1, max_length=128)


class TestReference(BaseModel):
    """A pinned deterministic control test."""

    model_config = ConfigDict(extra="forbid")

    test_id: str = Field(min_length=1, max_length=64)
    version: str = Field(min_length=1, max_length=32)

    def __str__(self) -> str:  # pragma: no cover - trivial
        return f"{self.test_id}@{self.version}"


class PackCompatibility(BaseModel):
    """What a pack needs to exist before it can compile.

    Declared by the pack rather than discovered during execution. A pack that
    names a control test nobody released should fail to compile with that
    sentence, not fail three tasks later with a lookup error.
    """

    model_config = ConfigDict(extra="forbid")

    min_platform_version: str = Field(default="0.8.0", max_length=32)
    requires_control_tests: list[TestReference] = Field(default_factory=list)
    requires_agent_roles: list[str] = Field(default_factory=list)
    requires_connectors: list[str] = Field(default_factory=list)


class PackCriterion(BaseModel):
    model_config = ConfigDict(extra="forbid")

    criteria_id: str = Field(min_length=2, max_length=64)
    text: str = Field(min_length=10)
    citation: str = Field(min_length=2, max_length=512)
    strength: ClaimStrength = ClaimStrength.MANDATORY
    effective_from: date | None = None
    effective_to: date | None = None

    def as_criterion(self) -> CriterionInput:
        return CriterionInput(
            code=self.criteria_id,
            text=self.text,
            citation=self.citation,
            strength=self.strength,
            effective_from=self.effective_from,
            effective_to=self.effective_to,
        )


class PackControl(BaseModel):
    model_config = ConfigDict(extra="forbid")

    control_id: str = Field(min_length=2, max_length=64)
    risk: str = Field(min_length=10)
    expected: str = Field(min_length=10)
    criteria_refs: list[str] = Field(min_length=1)


class PackProcedure(BaseModel):
    """One step of the pack's methodology, and what it compiles into.

    ``key`` is the identity the compiled task carries, so it has to be stable
    across pack versions: a renamed key produces a different task rather than a
    changed one, and any state keyed on the old name is orphaned.
    """

    model_config = ConfigDict(extra="forbid")

    key: str = Field(min_length=1, max_length=128, pattern=r"^[a-z0-9][a-z0-9._-]*$")
    step: int = Field(ge=1)
    agent: str = Field(min_length=1, max_length=128)
    action: str = Field(min_length=5)
    task_type: str = Field(default="agent", min_length=1, max_length=64)
    depends_on: list[str] = Field(default_factory=list)
    control_ref: str | None = None
    test_ref: TestReference | None = None
    human_gate: str | None = Field(default=None, max_length=128)
    model_policy: str | None = Field(default=None, max_length=128)
    tool_policy: str | None = Field(default=None, max_length=128)
    deadline_seconds: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def a_control_test_step_must_pin_a_test(self) -> "PackProcedure":
        """A ``control_test`` step with no pinned test is not deterministic.

        The whole claim of the deterministic engine is that the procedure that ran
        is identifiable by id and version. A step that declares itself a control
        test and names none has opted out of that claim while keeping the label.
        """
        if self.task_type == "control_test" and self.test_ref is None:
            raise ValueError(f"procedure {self.key!r} is a control_test but pins no test_ref")
        if self.task_type != "control_test" and self.test_ref is not None:
            raise ValueError(
                f"procedure {self.key!r} pins a test_ref but is not a control_test step"
            )
        return self


class PackEntitlement(BaseModel):
    model_config = ConfigDict(extra="forbid")

    standard_code: str = Field(min_length=2, max_length=64)
    licence: str = Field(min_length=1, max_length=128)


class PackManifest(BaseModel):
    """A parsed, validated ``pack.yaml``.

    Validation here is about internal coherence — the graph resolves, gates are
    both declared and used, criteria referenced by controls exist. Whether the
    *platform* can satisfy the pack is a separate question answered by the
    compiler, because it depends on what is released rather than on the pack.
    """

    model_config = ConfigDict(extra="forbid")

    pack_id: str = Field(min_length=2, max_length=128)
    version: str = Field(min_length=1, max_length=32)
    status: Literal["draft", "review", "released", "retired"]
    signed: bool
    objective: str = Field(min_length=20)
    release_key_id: str | None = None
    standard: StandardInput
    compatibility: PackCompatibility = Field(default_factory=PackCompatibility)
    criteria: list[PackCriterion] = Field(min_length=1)
    controls: list[PackControl] = Field(min_length=1)
    procedures: list[PackProcedure] = Field(min_length=1)
    human_gates: list[str] = Field(min_length=1)
    quality_rules: list[str] = Field(min_length=1)
    crosswalks: list[CrosswalkInput] = Field(default_factory=list)

    @property
    def reference(self) -> str:
        return f"{self.pack_id}@{self.version}"

    @model_validator(mode="after")
    def the_pack_must_be_internally_coherent(self) -> "PackManifest":
        criteria_ids = {item.criteria_id for item in self.criteria}
        if len(criteria_ids) != len(self.criteria):
            raise ValueError("criteria ids are not unique")

        for control in self.controls:
            missing = sorted(set(control.criteria_refs) - criteria_ids)
            if missing:
                raise ValueError(
                    f"control {control.control_id!r} cites unknown criteria: {', '.join(missing)}"
                )

        control_ids = {item.control_id for item in self.controls}
        keys = [item.key for item in self.procedures]
        if len(set(keys)) != len(keys):
            raise ValueError("procedure keys are not unique")
        known = set(keys)
        for procedure in self.procedures:
            unknown = sorted(set(procedure.depends_on) - known)
            if unknown:
                raise ValueError(
                    f"procedure {procedure.key!r} depends on unknown steps: {', '.join(unknown)}"
                )
            if procedure.key in procedure.depends_on:
                raise ValueError(f"procedure {procedure.key!r} depends on itself")
            if procedure.control_ref and procedure.control_ref not in control_ids:
                raise ValueError(
                    f"procedure {procedure.key!r} cites unknown control {procedure.control_ref!r}"
                )

        declared = set(self.human_gates)
        used = {item.human_gate for item in self.procedures if item.human_gate}
        undeclared = sorted(used - declared)
        if undeclared:
            raise ValueError(
                f"procedures attach gates the pack does not declare: {', '.join(undeclared)}"
            )
        # A declared gate nobody stands behind is worse than no gate: it appears in
        # the methodology, satisfies a reviewer reading the pack, and stops nothing.
        unused = sorted(declared - used)
        if unused:
            raise ValueError(
                f"pack declares human gates no procedure enforces: {', '.join(unused)}"
            )

        crosswalk_targets = {item.source_criterion for item in self.crosswalks}
        unknown_sources = sorted(crosswalk_targets - criteria_ids)
        if unknown_sources:
            raise ValueError(
                f"crosswalks start from criteria this pack does not define: "
                f"{', '.join(unknown_sources)}"
            )
        return self


class OrganizationContext(BaseModel):
    """The tenant-side facts a compilation depends on.

    Supplied explicitly rather than read from wherever it happens to live, because
    the compilation record has to be able to state what the graph was a function
    of. A compilation that silently depended on the state of a table at a moment
    in time is not reproducible.
    """

    model_config = ConfigDict(extra="forbid")

    tenant_id: str
    entity_name: str = Field(min_length=1, max_length=255)
    period_start: date
    period_end: date
    in_scope_systems: list[str] = Field(default_factory=list)
    entitlements: list[str] = Field(default_factory=list)
    profile_version: int | None = None

    @model_validator(mode="after")
    def period_must_be_ordered(self) -> "OrganizationContext":
        if self.period_end < self.period_start:
            raise ValueError("period_end precedes period_start")
        return self


class CompilationPins(BaseModel):
    """Everything the compiled graph is a function of.

    Stored with the compilation so a later pack upgrade can be shown not to have
    changed a historical engagement: the old engagement still points at the old
    digest, and the record says which.
    """

    pack_id: str
    pack_version: str
    package_sha256: str
    release_key_id: str | None
    standard_code: str
    standard_version: str
    criteria: dict[str, str]
    control_tests: dict[str, str]
    agent_roles: dict[str, str]
    platform_version: str
    organization_profile_version: int | None
    compiled_from: str

    @property
    def digest_source(self) -> dict[str, Any]:
        return self.model_dump(mode="json")


class CompilationResult(BaseModel):
    """A compiled engagement graph and the pins that produced it."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    compilation_id: str
    workflow: Any
    pins: CompilationPins
    pins_digest: str
    task_count: int
    gate_count: int
