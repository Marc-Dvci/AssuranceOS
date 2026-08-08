"""Compiling a signed Audit Pack into an engagement, and every way it can refuse.

The demonstration this component owes is not "a pack compiled". It is that the
engagement which runs is a function of a signed artefact, that recompiling the
same inputs produces the same graph, and that each way a pack can be wrong
produces a distinct, attributable refusal rather than a partially built audit.

So the run below compiles one pack for real and then attempts five compilations
that must fail: an unentitled licensed standard, a criterion that does not cover
the audit period, a pinned control test the platform does not have, a pack that
was never approved, and a second compilation of an engagement that already has
one. Each refusal is reported with its message.
"""

from __future__ import annotations

import shutil
import tempfile
from datetime import date
from pathlib import Path
from typing import Any

import yaml

from ..control_testing import ControlTestRegistry
from ..db.models import Engagement, Tenant
from ..db.repositories import AuditEventRepository, EngagementRepository, TenantRepository
from ..db.session import Database
from .compiler import AuditPackCompiler
from .definitions import OrganizationContext
from .exceptions import (
    CriteriaEffectivityError,
    PackCompatibilityError,
    PackCompilationError,
    PackEntitlementError,
    PackNotReleasedError,
    PackSignatureError,
)
from .packs import AuditPackRegistry
from .service import StandardsService, released_agent_versions, released_test_versions

DEMO_TENANT = "tnt_asteria"
SCM_ENGAGEMENT = "eng_asteria_scm_compiled"
IAM_ENGAGEMENT = "eng_asteria_iam_compiled"
PAM_ENGAGEMENT = "eng_asteria_pam_blocked"
PERIOD = (date(2026, 7, 1), date(2026, 7, 31))


def run_pack_compiler_demo(
    *,
    database: Database,
    repository_root: Path,
    tenant_id: str | None = None,
    reset: bool = True,
) -> dict[str, Any]:
    """Compile packs into engagements and report what canonical state says.

    ``tenant_id`` retargets the demonstration so several demonstrations can
    compose one complete tenant; ``reset`` keeps whatever that tenant already
    holds instead of deleting it first.
    """
    tenant = tenant_id or DEMO_TENANT
    root = Path(repository_root)
    pack_key = (root / "security/release-keys/audit-pack-release-public.pem").read_bytes()
    test_key = (root / "security/release-keys/control-test-release-public.pem").read_bytes()

    registry = AuditPackRegistry(root / "audit-packs", trusted_public_key=pack_key).load()
    tests = ControlTestRegistry(root / "tests-library", trusted_public_key=test_key).load()
    compiler = AuditPackCompiler(
        released_tests=released_test_versions(tests),
        released_agents=released_agent_versions(root / "agents"),
    )
    service = StandardsService(database, registry=registry, compiler=compiler)

    _reset_and_seed(database, tenant, reset=reset)

    # -- 1. admit the packs ----------------------------------------------------
    registered = {}
    for pack in registry.list():
        registered[pack.reference] = service.register_pack(
            pack=pack, registered_by="standards-team@asteria.example"
        )
    for reference in ("software-change-management@2.0.0", "identity-access@1.0.0"):
        pack_id, version = reference.split("@")
        service.approve_pack(
            pack_id=pack_id,
            version=version,
            approved_by="dana.director@asteria.example",
            reason="Methodology reviewed against the current policy version.",
        )

    context = OrganizationContext(
        tenant_id=tenant,
        entity_name="Asteria Systems DemoCo",
        period_start=PERIOD[0],
        period_end=PERIOD[1],
        in_scope_systems=["github://asteria/api", "jira://CHG"],
        entitlements=service.effective_entitlements(tenant_id=tenant),
        profile_version=1,
    )

    # -- 2. compile a real engagement -----------------------------------------
    compiled = service.compile_engagement(
        pack_id="software-change-management",
        version="2.0.0",
        context=context,
        engagement_id=SCM_ENGAGEMENT,
        compiled_by="engagement-director@asteria.example",
    )

    # A second engagement from a different pack, to show the graph follows the
    # pack rather than a template the platform holds.
    iam_compiled = service.compile_engagement(
        pack_id="identity-access",
        version="1.0.0",
        context=context,
        engagement_id=IAM_ENGAGEMENT,
        compiled_by="engagement-director@asteria.example",
    )

    # -- 3. determinism: the same inputs produce the same graph ----------------
    replay_workflow, replay_pins = compiler.compile(
        registry.get("software-change-management", "2.0.0"), context
    )
    from .compiler import pins_digest

    deterministic = pins_digest(replay_pins) == compiled["pins_digest"] and [
        task.key for task in replay_workflow.tasks
    ] == compiled["task_keys"]

    # -- 4. the refusals -------------------------------------------------------
    # Three are refused by the compiler, before any state is touched; two by the
    # service, which knows about approval and about engagements; one by the
    # registry, which never lets a modified artefact reach either.
    refusals = {
        "unentitled_standard": _refusal(
            lambda: compiler.compile(registry.get("privileged-access", "1.0.0"), context),
            PackEntitlementError,
        ),
        "criteria_not_effective_for_period": _refusal(
            lambda: compiler.compile(
                registry.get("software-change-management", "2.0.0"),
                context.model_copy(
                    update={
                        "period_start": date(2025, 1, 1),
                        "period_end": date(2025, 12, 31),
                    }
                ),
            ),
            CriteriaEffectivityError,
        ),
        "pinned_control_test_missing": _refusal(
            lambda: AuditPackCompiler(
                released_tests={},
                released_agents=released_agent_versions(root / "agents"),
            ).compile(registry.get("software-change-management", "2.0.0"), context),
            PackCompatibilityError,
        ),
        "pack_not_approved": _refusal(
            lambda: service.compile_engagement(
                pack_id="privileged-access",
                version="1.0.0",
                context=context.model_copy(update={"entitlements": ["SYN-PAM-BENCH"]}),
                engagement_id=PAM_ENGAGEMENT,
                compiled_by="engagement-director@asteria.example",
            ),
            PackNotReleasedError,
        ),
        "already_compiled": _refusal(
            lambda: service.compile_engagement(
                pack_id="identity-access",
                version="1.0.0",
                context=context,
                engagement_id=SCM_ENGAGEMENT,
                compiled_by="engagement-director@asteria.example",
            ),
            PackCompilationError,
        ),
        "tampered_pack": _tampered_pack_refusal(root, pack_key),
    }

    # -- 5. report from canonical state ---------------------------------------
    provenance = service.provenance(tenant_id=tenant, engagement_id=SCM_ENGAGEMENT)
    with database.read_session() as session:
        tasks = EngagementRepository(session).list_tasks(tenant, SCM_ENGAGEMENT)
        events = AuditEventRepository(session).list(tenant, SCM_ENGAGEMENT)

    return {
        "tenant_id": tenant,
        "packs_registered": sorted(registered),
        "engagement_id": SCM_ENGAGEMENT,
        "pack": compiled["pack"],
        "task_count": compiled["task_count"],
        "gate_count": compiled["gate_count"],
        "pins_digest": compiled["pins_digest"],
        "compilation_is_deterministic": deterministic,
        # Read back from the database, not from the compiler's return value.
        "tasks_from_canonical_state": [task.task_key for task in tasks],
        "gates_from_canonical_state": sorted(
            {task.human_gate for task in tasks if task.human_gate}
        ),
        "agent_roles_from_canonical_state": sorted(
            {task.assigned_agent_role for task in tasks if task.assigned_agent_role}
        ),
        "second_pack_engagement": iam_compiled["engagement_id"],
        "second_pack_task_count": iam_compiled["task_count"],
        # Two packs, two different graphs. A platform whose engagements all look
        # the same has a template, not a compiler.
        "packs_produce_different_graphs": (
            compiled["task_keys"] != iam_compiled["task_keys"]
        ),
        "provenance": provenance,
        "refusals": refusals,
        "audit_event_types": [event["event_type"] for event in events],
    }


def _refusal(action: Any, expected: Any) -> str:
    """Run something that must fail, and return the refusal it produced."""
    try:
        action()
    except expected as exc:  # type: ignore[misc]
        return str(exc)
    return ""


def _tampered_pack_refusal(root: Path, pack_key: bytes) -> str:
    """Modify a released pack on disk and show the registry refuse it.

    Done on a copy in a temporary directory rather than on the repository's pack,
    because a demonstration that leaves the working tree modified has produced a
    second problem in the course of illustrating the first.
    """
    with tempfile.TemporaryDirectory() as workspace:
        staged = Path(workspace) / "audit-packs"
        staged.mkdir()
        shutil.copytree(
            root / "audit-packs/software-change-management",
            staged / "software-change-management",
        )
        shutil.copytree(root / "audit-packs/schemas", staged / "schemas")

        manifest_path = staged / "software-change-management" / "pack.yaml"
        manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
        # Remove the gate a reader of the signed pack would expect to be there.
        manifest["procedures"] = [
            item for item in manifest["procedures"] if item["key"] != "open-remediation"
        ]
        manifest["procedures"] = [
            {**item, "depends_on": [d for d in item.get("depends_on", []) if d != "open-remediation"]}
            for item in manifest["procedures"]
        ]
        manifest_path.write_text(yaml.safe_dump(manifest, sort_keys=False), encoding="utf-8")

        try:
            AuditPackRegistry(staged, trusted_public_key=pack_key).load()
        except PackSignatureError as exc:
            return str(exc)
    return ""


def _reset_and_seed(database: Database, tenant: str, *, reset: bool = True) -> None:
    if reset:
        with database.transaction() as session:
            existing = TenantRepository(session).get(tenant)
            if existing is not None:
                session.delete(existing)
    with database.transaction() as session:
        repository = TenantRepository(session)
        if repository.get(tenant) is None:
            repository.add(
                Tenant(
                    tenant_id=tenant,
                    slug="asteria",
                    name="Asteria Systems DemoCo",
                    status="active",
                    region="europe-west1",
                )
            )
            session.flush()
        # Composing onto a tenant another demonstration populated must not
        # duplicate the records this one owns.
        if session.get(Engagement, SCM_ENGAGEMENT) is not None:
            return
        session.flush()
        for engagement_id, code, title in (
            (SCM_ENGAGEMENT, "SCM-2026-07", "Software change management"),
            (IAM_ENGAGEMENT, "IAM-2026-07", "Identity and access"),
            (PAM_ENGAGEMENT, "PAM-2026-07", "Privileged access design"),
        ):
            session.add(
                Engagement(
                    engagement_id=engagement_id,
                    tenant_id=tenant,
                    code=code,
                    title=title,
                    status="planned",
                    audit_pack_ref="pending-compilation",
                    period_start=PERIOD[0],
                    period_end=PERIOD[1],
                )
            )
