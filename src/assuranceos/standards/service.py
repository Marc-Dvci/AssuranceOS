"""The standards service and the Audit Pack lifecycle.

Two responsibilities, kept in one place because they are the same idea seen from
two sides. The standards half owns *what an audit tests against* — versioned
criteria, their citations, their licensing position, and how they map across
frameworks. The pack half owns *how it is tested* — the signed methodology that
compiles into an engagement.

The connection is that a compiled engagement pins both. Reading a
:class:`~assuranceos.db.models.standards.PackCompilation` tells you which pack
digest ran, against which standard version, citing which criteria, using which
control-test versions and which agent releases. That record is what makes an
engagement re-explainable a year later, when the pack has moved on twice and the
standard has been revised.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date, datetime, timezone
from typing import Any

from assuranceos.db.models import (
    AuditPackRegistration,
    CriteriaCrosswalk,
    CriteriaMapping,
    Criterion,
    Engagement,
    PackCompilation,
    Standard,
    StandardEntitlement,
)
from assuranceos.db.repositories import AuditEventRepository, OutboxRepository, new_id
from assuranceos.db.session import Database
from assuranceos.models import AuditEvent
from assuranceos.orchestration.compiler import WorkflowCompiler

from .compiler import AuditPackCompiler, pins_digest
from .definitions import (
    CriterionInput,
    CrosswalkInput,
    OrganizationContext,
    StandardInput,
)
from .exceptions import (
    CriterionNotFoundError,
    DuplicateStandardError,
    PackCompilationError,
    PackNotFoundError,
    PackNotReleasedError,
    StandardNotFoundError,
)
from .packs import AuditPackRegistry, LoadedAuditPack
from .repository import StandardsRepository


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class StandardsService:
    """Standards, criteria, crosswalks, pack registration, and compilation."""

    def __init__(
        self,
        database: Database,
        *,
        registry: AuditPackRegistry | None = None,
        compiler: AuditPackCompiler | None = None,
    ):
        self.database = database
        self.registry = registry
        self.compiler = compiler or AuditPackCompiler()

    # -- standards -------------------------------------------------------------

    def register_standard(
        self,
        *,
        standard: StandardInput,
        criteria: Sequence[CriterionInput] = (),
        tenant_id: str | None = None,
    ) -> str:
        """Record a version of a standard and the criteria it contains.

        Refuses a re-registration of the same code and version. A standard version
        is immutable by definition — the issuer publishes a new version rather than
        editing the old one — and permitting an overwrite would let a criterion
        change under engagements that already cite it.
        """
        with self.database.transaction() as session:
            repository = StandardsRepository(session)
            if repository.get_standard(standard.code, standard.version) is not None:
                raise DuplicateStandardError(
                    f"{standard.code}@{standard.version} is already registered; "
                    "a standard version is immutable, publish a new version instead"
                )
            record = Standard(
                standard_id=new_id("std"),
                tenant_id=tenant_id,
                code=standard.code,
                name=standard.name,
                issuer=standard.issuer,
                version=standard.version,
                jurisdiction=standard.jurisdiction,
                licence=standard.licence,
                entitlement_required=standard.entitlement_required,
                effective_from=standard.effective_from,
                effective_to=standard.effective_to,
                source_url=standard.source_url,
            )
            repository.add_standard(record)
            for criterion in criteria:
                repository.add_criterion(
                    Criterion(
                        criterion_id=new_id("crt"),
                        standard_id=record.standard_id,
                        code=criterion.code,
                        text=criterion.text,
                        citation=criterion.citation,
                        strength=criterion.strength.value,
                        requirement_ref=criterion.requirement_ref,
                        effective_from=criterion.effective_from,
                        effective_to=criterion.effective_to,
                    )
                )
            return record.standard_id

    def add_crosswalk(
        self,
        *,
        source: tuple[str, str, str],
        target: tuple[str, str, str],
        crosswalk: CrosswalkInput,
    ) -> str:
        """Assert a relationship between criteria in two standards.

        ``source`` and ``target`` are ``(standard_code, standard_version,
        criterion_code)``. Naming the standard version is not optional: an
        equivalence asserted against ISO 27001:2013 is not evidence about
        ISO 27001:2022, and a crosswalk that omits the version silently becomes one.
        """
        with self.database.transaction() as session:
            repository = StandardsRepository(session)
            source_row = self._require_criterion(repository, *source)
            target_row = self._require_criterion(repository, *target)
            record = CriteriaCrosswalk(
                crosswalk_id=new_id("cwk"),
                source_criterion_id=source_row.criterion_id,
                target_criterion_id=target_row.criterion_id,
                relation=crosswalk.relation.value,
                rationale=crosswalk.rationale,
                asserted_by=crosswalk.asserted_by,
                change_impact_json={
                    "source": f"{source[0]}@{source[1]}:{source[2]}",
                    "target": f"{target[0]}@{target[1]}:{target[2]}",
                },
            )
            repository.add_crosswalk(record)
            return record.crosswalk_id

    def map_criterion(
        self,
        *,
        standard_code: str,
        standard_version: str,
        criterion_code: str,
        target_type: str,
        target_ref: str,
        coverage: str = "partial",
        rationale: str | None = None,
        tenant_id: str | None = None,
    ) -> str:
        """Link a criterion to a risk, control, procedure, or deterministic test."""
        if target_type not in {"risk", "control", "procedure", "test"}:
            raise ValueError(
                "target_type must be one of risk, control, procedure, test; "
                f"got {target_type!r}"
            )
        with self.database.transaction() as session:
            repository = StandardsRepository(session)
            criterion = self._require_criterion(
                repository, standard_code, standard_version, criterion_code
            )
            record = CriteriaMapping(
                mapping_id=new_id("cmp"),
                tenant_id=tenant_id,
                criterion_id=criterion.criterion_id,
                target_type=target_type,
                target_ref=target_ref,
                coverage=coverage,
                rationale=rationale,
            )
            repository.add_mapping(record)
            return record.mapping_id

    def change_impact(
        self, *, standard_code: str, standard_version: str, criterion_code: str
    ) -> dict[str, Any]:
        """Everything a revision of this criterion would touch.

        The question a standards team asks before accepting a new framework
        version, and the one nobody can answer from a spreadsheet of mappings:
        which controls, which tests, and which other frameworks' criteria are
        downstream of this one.
        """
        with self.database.read_session() as session:
            repository = StandardsRepository(session)
            criterion = self._require_criterion(
                repository, standard_code, standard_version, criterion_code
            )
            mappings = repository.mappings_for(criterion.criterion_id)
            crosswalks = repository.crosswalks_touching(criterion.criterion_id)
            linked: list[dict[str, str]] = []
            for edge in crosswalks:
                other_id = (
                    edge.target_criterion_id
                    if edge.source_criterion_id == criterion.criterion_id
                    else edge.source_criterion_id
                )
                other = session.get(Criterion, other_id)
                standard = session.get(Standard, other.standard_id) if other else None
                linked.append(
                    {
                        "criterion": other.code if other else other_id,
                        "standard": (
                            f"{standard.code}@{standard.version}" if standard else "unknown"
                        ),
                        "relation": edge.relation,
                        "direction": (
                            "outbound"
                            if edge.source_criterion_id == criterion.criterion_id
                            else "inbound"
                        ),
                        "asserted_by": edge.asserted_by,
                    }
                )
            return {
                "criterion": f"{standard_code}@{standard_version}:{criterion_code}",
                "citation": criterion.citation,
                "mapped_targets": [
                    {
                        "target_type": item.target_type,
                        "target_ref": item.target_ref,
                        "coverage": item.coverage,
                    }
                    for item in mappings
                ],
                "linked_criteria": linked,
                "impact_count": len(mappings) + len(linked),
            }

    def grant_entitlement(
        self,
        *,
        tenant_id: str,
        standard_code: str,
        licence_ref: str,
        granted_by: str,
        expires_on: date | None = None,
    ) -> str:
        with self.database.transaction() as session:
            record = StandardEntitlement(
                entitlement_id=new_id("ent"),
                tenant_id=tenant_id,
                standard_code=standard_code,
                licence_ref=licence_ref,
                granted_by=granted_by,
                expires_on=expires_on,
            )
            StandardsRepository(session).add_entitlement(record)
            return record.entitlement_id

    def effective_entitlements(self, *, tenant_id: str, on: date | None = None) -> list[str]:
        """Standard codes this tenant may have reproduced, as at a date.

        An expired licence is not an entitlement. Filtering here rather than at
        the grant means a compilation run after expiry fails, which is the
        behaviour the licence actually requires.
        """
        as_at = on or utc_now().date()
        with self.database.read_session() as session:
            return sorted(
                item.standard_code
                for item in StandardsRepository(session).entitlements(tenant_id)
                if item.expires_on is None or item.expires_on >= as_at
            )

    # -- pack registration -----------------------------------------------------

    def register_pack(self, *, pack: LoadedAuditPack, registered_by: str) -> str:
        """Admit a verified pack to the platform.

        Registration is idempotent on the digest. Re-registering the identical
        artefact returns the existing record; re-registering a *different* artefact
        under the same version is refused, because a pack version whose content can
        change is not a version.

        No audit event is emitted. A pack is a platform-wide artefact and the audit
        event stream is per-tenant by design, so the registration row carries the
        record instead — who registered it, when, at which digest — as canonical
        state rather than as a log line. Compilation, which *is* tenant-scoped,
        emits normally.
        """
        manifest = pack.manifest
        with self.database.transaction() as session:
            repository = StandardsRepository(session)
            existing = repository.get_registration(manifest.pack_id, manifest.version)
            if existing is not None:
                if existing.package_sha256 != pack.package_sha256:
                    raise PackNotReleasedError(
                        f"{manifest.reference} is already registered at digest "
                        f"{existing.package_sha256[:12]}, and this artefact is "
                        f"{pack.package_sha256[:12]}; a pack version is immutable"
                    )
                return existing.registration_id

            record = AuditPackRegistration(
                registration_id=new_id("apk"),
                pack_id=manifest.pack_id,
                version=manifest.version,
                status="registered",
                package_sha256=pack.package_sha256,
                release_key_id=manifest.release_key_id,
                standard_code=manifest.standard.code,
                standard_version=manifest.standard.version,
                manifest_json=manifest.model_dump(mode="json"),
                compatibility_json=manifest.compatibility.model_dump(mode="json"),
                registered_by=registered_by,
            )
            repository.add_registration(record)
            return record.registration_id

    def approve_pack(
        self, *, pack_id: str, version: str, approved_by: str, reason: str
    ) -> str:
        """Release a registered pack for use in engagements.

        Registration says the artefact is genuine. Approval says the organisation
        has reviewed the methodology and will stand behind conclusions produced by
        it. Only an approved pack compiles.

        The approver, the time, and the stated reason are written to the
        registration row, which is where a reviewer asking "who signed off on this
        methodology" should find them.
        """
        with self.database.transaction() as session:
            repository = StandardsRepository(session)
            record = repository.get_registration(pack_id, version)
            if record is None:
                raise PackNotFoundError(f"Audit Pack {pack_id}@{version} is not registered")
            if record.status == "approved":
                return record.registration_id
            record.status = "approved"
            record.approved_at = utc_now()
            record.approved_by = approved_by
            record.approval_reason = reason
            return record.registration_id

    # -- compilation -----------------------------------------------------------

    def compile_engagement(
        self,
        *,
        pack_id: str,
        version: str,
        context: OrganizationContext,
        engagement_id: str,
        compiled_by: str,
    ) -> dict[str, Any]:
        """Turn an approved pack into an engagement's task graph.

        The engagement's task graph, its pins, and the audit record of the
        compilation are written in one transaction. A compilation that produced
        tasks but no pins would be an engagement nobody can explain afterwards,
        which is the failure mode this component exists to remove.
        """
        if self.registry is None:
            raise PackNotFoundError("no Audit Pack registry is configured")
        pack = self.registry.get(pack_id, version)

        with self.database.transaction() as session:
            repository = StandardsRepository(session)
            registration = repository.get_registration(pack_id, version)
            if registration is None:
                raise PackNotFoundError(
                    f"Audit Pack {pack_id}@{version} is not registered on this platform"
                )
            if registration.status != "approved":
                raise PackNotReleasedError(
                    f"Audit Pack {pack_id}@{version} is {registration.status!r}; "
                    "an engagement compiles only from an approved pack"
                )
            if registration.package_sha256 != pack.package_sha256:
                # The artefact on disk is not the one that was approved. This is
                # the tamper case, and it is worth its own sentence rather than
                # being folded into a signature error.
                raise PackNotReleasedError(
                    f"Audit Pack {pack_id}@{version} on disk has digest "
                    f"{pack.package_sha256[:12]} but {registration.package_sha256[:12]} "
                    "was approved"
                )

            engagement = session.get(Engagement, engagement_id)
            if engagement is None or engagement.tenant_id != context.tenant_id:
                raise PackCompilationError(
                    f"engagement {engagement_id!r} was not found for tenant "
                    f"{context.tenant_id!r}"
                )
            if repository.compilation_for(context.tenant_id, engagement_id) is not None:
                raise PackCompilationError(
                    f"engagement {engagement_id!r} is already compiled from a pack; "
                    "recompiling would replace a methodology an engagement is running under"
                )

            workflow, pins = self.compiler.compile(pack, context)
            digest = pins_digest(pins)

            WorkflowCompiler().compile(
                session,
                tenant_id=context.tenant_id,
                engagement_id=engagement_id,
                workflow=workflow,
                compiled_at=utc_now(),
            )
            # The engagement now points at the pack it was compiled from, so the
            # link survives independently of the compilation record.
            engagement.audit_pack_ref = pack.reference

            compilation = PackCompilation(
                compilation_id=new_id("cmpl"),
                tenant_id=context.tenant_id,
                engagement_id=engagement_id,
                registration_id=registration.registration_id,
                pack_id=pack_id,
                pack_version=version,
                package_sha256=pack.package_sha256,
                workflow_version=workflow.workflow_version,
                pins_json=pins.model_dump(mode="json"),
                pins_digest=digest,
                organization_context_json=context.model_dump(mode="json"),
                task_count=len(workflow.tasks),
                gate_count=sum(1 for task in workflow.tasks if task.human_gate),
                compiled_by=compiled_by,
            )
            repository.add_compilation(compilation)

            self._emit(
                session,
                tenant_id=context.tenant_id,
                engagement_id=engagement_id,
                aggregate_id=compilation.compilation_id,
                event_type="audit_pack.compiled",
                payload={
                    "compilation_id": compilation.compilation_id,
                    "engagement_id": engagement_id,
                    "pack": pack.reference,
                    "package_sha256": pack.package_sha256,
                    "workflow_version": workflow.workflow_version,
                    "pins_digest": digest,
                    "task_count": compilation.task_count,
                    "gate_count": compilation.gate_count,
                    "compiled_by": compiled_by,
                },
                idempotency_key=f"pack-compiled:{compilation.compilation_id}",
                aggregate_type="pack_compilation",
            )
            return {
                "compilation_id": compilation.compilation_id,
                "engagement_id": engagement_id,
                "pack": pack.reference,
                "workflow_version": workflow.workflow_version,
                "task_count": compilation.task_count,
                "gate_count": compilation.gate_count,
                "pins_digest": digest,
                "pins": pins.model_dump(mode="json"),
                "task_keys": [task.key for task in workflow.tasks],
            }

    def provenance(self, *, tenant_id: str, engagement_id: str) -> dict[str, Any]:
        """What this engagement was compiled from, and against which criteria.

        The reply is deliberately flat and complete. It is what a reviewer needs
        in order to check a finding's citation without holding the pack open in
        another window.
        """
        with self.database.read_session() as session:
            compilation = StandardsRepository(session).compilation_for(
                tenant_id, engagement_id
            )
            if compilation is None:
                raise PackCompilationError(
                    f"engagement {engagement_id!r} was not compiled from an Audit Pack"
                )
            pins = dict(compilation.pins_json or {})
            return {
                "engagement_id": engagement_id,
                "pack": f"{compilation.pack_id}@{compilation.pack_version}",
                "package_sha256": compilation.package_sha256,
                "workflow_version": compilation.workflow_version,
                "pins_digest": compilation.pins_digest,
                "standard": f"{pins.get('standard_code')}@{pins.get('standard_version')}",
                "criteria": pins.get("criteria", {}),
                "control_tests": pins.get("control_tests", {}),
                "agent_roles": pins.get("agent_roles", {}),
                "platform_version": pins.get("platform_version"),
                "compiled_at": compilation.compiled_at.isoformat(),
                "compiled_by": compilation.compiled_by,
            }

    def upgrade_impact(self, *, pack_id: str, from_version: str, to_version: str) -> dict[str, Any]:
        """What changes if a pack version is adopted, and what does not.

        The "does not" half is the point. Engagements already compiled keep their
        pinned digest; a pack upgrade creates new engagements rather than mutating
        old ones, and this reports which existing engagements are unaffected so
        that claim is checkable rather than asserted.
        """
        if self.registry is None:
            raise PackNotFoundError("no Audit Pack registry is configured")
        old = self.registry.get(pack_id, from_version).manifest
        new = self.registry.get(pack_id, to_version).manifest

        old_steps = {item.key for item in old.procedures}
        new_steps = {item.key for item in new.procedures}
        old_criteria = {item.criteria_id for item in old.criteria}
        new_criteria = {item.criteria_id for item in new.criteria}

        with self.database.read_session() as session:
            existing = StandardsRepository(session).compilations_of(pack_id)
            frozen = [
                {
                    "engagement_id": item.engagement_id,
                    "pinned_version": item.pack_version,
                    "pinned_digest": item.package_sha256[:12],
                }
                for item in existing
            ]

        return {
            "pack_id": pack_id,
            "from_version": from_version,
            "to_version": to_version,
            "procedures_added": sorted(new_steps - old_steps),
            "procedures_removed": sorted(old_steps - new_steps),
            "criteria_added": sorted(new_criteria - old_criteria),
            "criteria_removed": sorted(old_criteria - new_criteria),
            "gates_added": sorted(set(new.human_gates) - set(old.human_gates)),
            "gates_removed": sorted(set(old.human_gates) - set(new.human_gates)),
            # Every engagement listed here keeps running the version it pinned.
            "engagements_unaffected": frozen,
        }

    # -- internals -------------------------------------------------------------

    @staticmethod
    def _require_criterion(
        repository: StandardsRepository, code: str, version: str, criterion_code: str
    ) -> Criterion:
        standard = repository.get_standard(code, version)
        if standard is None:
            raise StandardNotFoundError(f"standard {code}@{version} is not registered")
        criterion = repository.get_criterion(standard.standard_id, criterion_code)
        if criterion is None:
            raise CriterionNotFoundError(
                f"criterion {criterion_code!r} is not defined in {code}@{version}"
            )
        return criterion

    def _emit(
        self,
        session: Any,
        *,
        tenant_id: str,
        engagement_id: str | None,
        aggregate_id: str,
        event_type: str,
        payload: Mapping[str, Any],
        idempotency_key: str,
        aggregate_type: str = "audit_pack",
    ) -> None:
        """Write the audit event and the outbox event in the caller's transaction."""
        AuditEventRepository(session).append(
            AuditEvent(
                event_type=event_type,
                tenant_id=tenant_id,
                engagement_id=engagement_id,
                occurred_at=utc_now(),
                payload=dict(payload),
            )
        )
        OutboxRepository(session).add(
            tenant_id=tenant_id,
            aggregate_type=aggregate_type,
            aggregate_id=aggregate_id,
            event_type=event_type,
            payload=dict(payload),
            idempotency_key=idempotency_key,
        )


def released_agent_versions(agent_root: Any) -> dict[str, str]:
    """The released agent roles and versions, for the compiler's inventory."""
    from assuranceos.registry import AgentRegistry

    return {
        role: str(package.manifest["version"])
        for role, package in AgentRegistry(agent_root).load().items()
    }


def released_test_versions(registry: Any) -> dict[str, str]:
    """``{test_id: version}`` for every released deterministic test."""
    return {item.manifest.test_id: item.manifest.version for item in registry.list()}
