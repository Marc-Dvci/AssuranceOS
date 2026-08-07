"""Compiling a signed Audit Pack into an engagement task graph.

This is the step that makes the methodology auditable rather than merely
documented. Before it, an engagement's workflow was hand-authored: someone wrote a
task list that they believed matched the pack. After it, the workflow *is* the
pack — a deterministic function of a signed artefact and an organisation context,
with every version it depended on pinned in the record.

The determinism claim is worth being precise about. Compiling the same pack digest
with the same context twice produces the same task keys, the same dependency
edges, the same gates, and the same pins digest. It does not produce the same task
*ids*: those are minted per compilation, because two engagements running the same
pack are different engagements.

Refusals live here rather than in the pack loader because they are about what the
platform can currently satisfy, which is a property of the deployment and not of
the artefact. A pack that compiles today and not tomorrow — because a control test
was withdrawn — is behaving correctly.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone

from assuranceos.orchestration.definitions import (
    DependencyDefinition,
    TaskDefinition,
    WorkflowDefinition,
)

from .definitions import (
    CompilationPins,
    OrganizationContext,
    PackManifest,
)
from .exceptions import (
    CriteriaEffectivityError,
    PackCompatibilityError,
    PackCompilationError,
    PackEntitlementError,
)
from .packs import LoadedAuditPack

#: The platform version a pack's ``min_platform_version`` is compared against.
PLATFORM_VERSION = "0.8.0"


def _version_tuple(value: str) -> tuple[int, ...]:
    """Parse a dotted version for comparison, tolerating suffixes.

    ``1.2.0-rc1`` compares as ``(1, 2, 0)``. A pre-release that claims a version
    is treated as that version rather than as unparseable, because refusing to
    compile against a release candidate is a policy decision and not a parsing one.
    """
    parts: list[int] = []
    for chunk in value.split("."):
        digits = ""
        for character in chunk:
            if not character.isdigit():
                break
            digits += character
        parts.append(int(digits) if digits else 0)
    return tuple(parts)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class AuditPackCompiler:
    """Turns a released pack plus an organisation context into a workflow.

    ``released_tests`` and ``released_agents`` are the platform's current
    inventory, passed in rather than looked up, so the compiler stays a pure
    function of its arguments and the same call can be made against a hypothetical
    inventory to answer "would this pack compile if we withdrew that test".
    """

    def __init__(
        self,
        *,
        released_tests: Mapping[str, str] | None = None,
        released_agents: Mapping[str, str] | None = None,
        available_connectors: Sequence[str] = (),
        platform_version: str = PLATFORM_VERSION,
    ):
        #: ``{test_id: version}`` for every released deterministic test.
        self.released_tests = dict(released_tests or {})
        #: ``{agent_role: version}`` for every released agent package.
        self.released_agents = dict(released_agents or {})
        self.available_connectors = set(available_connectors)
        self.platform_version = platform_version

    # -- admission -------------------------------------------------------------

    def check_compatibility(self, manifest: PackManifest) -> None:
        """Refuse a pack the platform cannot currently satisfy.

        Every unmet requirement is collected before raising. An operator who fixes
        one missing artefact only to be told about the next has been given a worse
        message than one who is handed the list.
        """
        problems: list[str] = []

        if _version_tuple(self.platform_version) < _version_tuple(
            manifest.compatibility.min_platform_version
        ):
            problems.append(
                f"pack requires platform >= {manifest.compatibility.min_platform_version}, "
                f"this is {self.platform_version}"
            )

        for reference in manifest.compatibility.requires_control_tests:
            released = self.released_tests.get(reference.test_id)
            if released is None:
                problems.append(f"control test {reference.test_id} is not released")
            elif released != reference.version:
                # Pinned exactly, not "at least". A pack validated against
                # SCM-01@2.0.0 has not been validated against 2.1.0, and silently
                # accepting the newer one would make the pack's evaluation
                # evidence apply to a procedure nobody ran it against.
                problems.append(
                    f"control test {reference.test_id} is released at {released}, "
                    f"pack pins {reference.version}"
                )

        for role in manifest.compatibility.requires_agent_roles:
            if role not in self.released_agents:
                problems.append(f"agent role {role!r} has no released package")

        missing_connectors = sorted(
            set(manifest.compatibility.requires_connectors) - self.available_connectors
        )
        for connector in missing_connectors:
            problems.append(f"connector type {connector!r} is not available")

        # Every procedure's agent must be released too, not only the ones the pack
        # remembered to list under compatibility.
        for procedure in manifest.procedures:
            if procedure.agent not in self.released_agents:
                problems.append(
                    f"procedure {procedure.key!r} is assigned to {procedure.agent!r}, "
                    "which has no released package"
                )

        if problems:
            raise PackCompatibilityError(
                f"{manifest.reference} cannot compile here: " + "; ".join(sorted(set(problems)))
            )

    @staticmethod
    def check_entitlement(manifest: PackManifest, context: OrganizationContext) -> None:
        """Refuse to reproduce licensed criteria for a tenant with no licence."""
        if not manifest.standard.entitlement_required:
            return
        if manifest.standard.code not in set(context.entitlements):
            raise PackEntitlementError(
                f"{manifest.reference} reproduces {manifest.standard.code} "
                f"({manifest.standard.licence}), and tenant {context.tenant_id} holds no "
                "entitlement for it"
            )

    @staticmethod
    def check_effectivity(manifest: PackManifest, context: OrganizationContext) -> None:
        """Refuse criteria that do not cover the whole audit period.

        Partial coverage is a failure, not a warning. A criterion in force from
        the middle of the period cannot support a conclusion about the period, and
        an audit that tests months the rule did not apply to has produced findings
        against nothing.
        """
        stale = [
            criterion.criteria_id
            for criterion in manifest.criteria
            if not criterion.as_criterion().effective_over(
                context.period_start, context.period_end
            )
        ]
        if stale:
            raise CriteriaEffectivityError(
                f"{manifest.reference}: criteria {', '.join(sorted(stale))} do not cover the "
                f"audit period {context.period_start.isoformat()} to "
                f"{context.period_end.isoformat()}"
            )

    # -- compilation -----------------------------------------------------------

    def compile(
        self, pack: LoadedAuditPack, context: OrganizationContext
    ) -> tuple[WorkflowDefinition, CompilationPins]:
        """Produce the workflow and the pins, or refuse with a reason."""
        manifest = pack.manifest
        self.check_compatibility(manifest)
        self.check_entitlement(manifest, context)
        self.check_effectivity(manifest, context)

        criteria_by_id = {item.criteria_id: item for item in manifest.criteria}
        controls_by_id = {item.control_id: item for item in manifest.controls}

        # Ordered by declared step, then by key. Step ordering is what the pack
        # author reads; the key tiebreak makes two procedures at the same step
        # compile in a stable order instead of in whatever order YAML produced.
        procedures = sorted(manifest.procedures, key=lambda item: (item.step, item.key))

        tasks: list[TaskDefinition] = []
        for procedure in procedures:
            control = controls_by_id.get(procedure.control_ref) if procedure.control_ref else None
            criteria_refs = list(control.criteria_refs) if control else []

            # The references a task carries are what lets a finding raised from it
            # cite its criteria without a model being asked to remember them.
            input_refs = [f"pack:{manifest.reference}"]
            if control:
                input_refs.append(f"control:{control.control_id}")
            input_refs.extend(f"criterion:{code}" for code in criteria_refs)
            if procedure.test_ref:
                input_refs.append(f"control-test:{procedure.test_ref}")

            tasks.append(
                TaskDefinition(
                    key=procedure.key,
                    task_type=procedure.task_type,
                    definition_version=manifest.version,
                    dependencies=[
                        DependencyDefinition(task_key=parent)
                        for parent in sorted(procedure.depends_on)
                    ],
                    assigned_agent_role=procedure.agent,
                    input_refs=input_refs,
                    execution_policy={
                        "pack_reference": manifest.reference,
                        "package_sha256": pack.package_sha256,
                        "action": procedure.action,
                        "criteria": criteria_refs,
                        "citations": [
                            criteria_by_id[code].citation
                            for code in criteria_refs
                            if code in criteria_by_id
                        ],
                        "quality_rules": list(manifest.quality_rules),
                        "control_test": str(procedure.test_ref) if procedure.test_ref else None,
                    },
                    model_policy=procedure.model_policy,
                    tool_policy=procedure.tool_policy,
                    # Priority follows declared step order so a scheduler with
                    # spare capacity still runs the methodology in the order the
                    # pack sets out, rather than in dependency-satisfied order.
                    priority=100 + procedure.step,
                    deadline_seconds=procedure.deadline_seconds,
                    human_gate=procedure.human_gate,
                )
            )

        workflow = WorkflowDefinition(
            workflow_version=manifest.reference,
            tasks=tasks,
            metadata={
                "pack_id": manifest.pack_id,
                "pack_version": manifest.version,
                "package_sha256": pack.package_sha256,
                "objective": manifest.objective,
                "standard": f"{manifest.standard.code}@{manifest.standard.version}",
                "human_gates": list(manifest.human_gates),
                "quality_rules": list(manifest.quality_rules),
                "entity": context.entity_name,
                "period": [
                    context.period_start.isoformat(),
                    context.period_end.isoformat(),
                ],
                "in_scope_systems": list(context.in_scope_systems),
            },
        )

        # The orchestrator's own validator is the authority on graph shape. Calling
        # it here means a pack cannot produce a graph the orchestrator would later
        # reject, and the failure names the pack rather than the engagement.
        from assuranceos.orchestration.compiler import WorkflowCompiler
        from assuranceos.orchestration.exceptions import WorkflowValidationError

        try:
            WorkflowCompiler().validate(workflow)
        except WorkflowValidationError as exc:
            raise PackCompilationError(f"{manifest.reference}: {exc}") from exc

        pins = CompilationPins(
            pack_id=manifest.pack_id,
            pack_version=manifest.version,
            package_sha256=pack.package_sha256,
            release_key_id=manifest.release_key_id,
            standard_code=manifest.standard.code,
            standard_version=manifest.standard.version,
            criteria={item.criteria_id: item.citation for item in manifest.criteria},
            control_tests={
                reference.test_id: reference.version
                for reference in manifest.compatibility.requires_control_tests
            },
            agent_roles={
                role: self.released_agents[role]
                for role in sorted({item.agent for item in manifest.procedures})
            },
            platform_version=self.platform_version,
            organization_profile_version=context.profile_version,
            compiled_from=str(pack.pack_dir.name),
        )
        return workflow, pins


def pins_digest(pins: CompilationPins) -> str:
    """A stable digest over everything the compiled graph depended on.

    Comparing two engagements' digests answers "did these run the same
    methodology" without diffing two task graphs, which is the question an audit
    committee actually asks when two units report different results.
    """
    return hashlib.sha256(
        json.dumps(pins.digest_source, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
