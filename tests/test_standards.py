"""Standards, criteria, and the Audit Pack compiler.

The claim under test is that an engagement's methodology is a compiled artefact:
the task graph is a function of a signed pack and an organisation context, the
same inputs produce the same graph, every version it depended on is pinned, and
each way a pack can be wrong produces its own refusal rather than a partially
built audit.

Most of the cases are refusals. A compiler that has only ever been shown to
compile is not known to be a gate.
"""

from __future__ import annotations

import shutil
from datetime import date
from pathlib import Path

import pytest
import yaml

from assuranceos.control_testing import ControlTestRegistry
from assuranceos.db.models import Engagement, Tenant
from assuranceos.db.repositories import EngagementRepository
from assuranceos.db.session import Database
from assuranceos.standards import (
    AuditPackCompiler,
    AuditPackRegistry,
    CriteriaEffectivityError,
    CriterionInput,
    CrosswalkInput,
    CrosswalkRelation,
    DuplicateStandardError,
    OrganizationContext,
    PackCompatibilityError,
    PackCompilationError,
    PackEntitlementError,
    PackNotFoundError,
    PackNotReleasedError,
    PackSchemaError,
    PackSignatureError,
    StandardInput,
    StandardsService,
    pins_digest,
    released_agent_versions,
    released_test_versions,
)

ROOT = Path(__file__).resolve().parents[1]
TENANT = "tnt_std"
ENGAGEMENT = "eng_std"
SECOND_ENGAGEMENT = "eng_std_2"
PERIOD = (date(2026, 7, 1), date(2026, 7, 31))


@pytest.fixture(scope="session")
def pack_key() -> bytes:
    return (ROOT / "security/release-keys/audit-pack-release-public.pem").read_bytes()


@pytest.fixture(scope="session")
def registry(pack_key) -> AuditPackRegistry:
    return AuditPackRegistry(ROOT / "audit-packs", trusted_public_key=pack_key).load()


@pytest.fixture(scope="session")
def compiler() -> AuditPackCompiler:
    tests = ControlTestRegistry(
        ROOT / "tests-library",
        trusted_public_key=(
            ROOT / "security/release-keys/control-test-release-public.pem"
        ).read_bytes(),
    ).load()
    return AuditPackCompiler(
        released_tests=released_test_versions(tests),
        released_agents=released_agent_versions(ROOT / "agents"),
    )


@pytest.fixture
def database(tmp_path):
    db = Database.from_sqlite_path(tmp_path / "standards.db")
    db.create_schema()
    with db.transaction() as session:
        session.add(Tenant(tenant_id=TENANT, slug="std", name="Standards"))
        session.flush()
        for engagement_id, code in ((ENGAGEMENT, "SCM-STD"), (SECOND_ENGAGEMENT, "IAM-STD")):
            session.add(
                Engagement(
                    engagement_id=engagement_id,
                    tenant_id=TENANT,
                    code=code,
                    title=code,
                    status="planned",
                    audit_pack_ref="pending",
                    period_start=PERIOD[0],
                    period_end=PERIOD[1],
                )
            )
    try:
        yield db
    finally:
        db.dispose()


@pytest.fixture
def service(database, registry, compiler):
    return StandardsService(database, registry=registry, compiler=compiler)


def context(**overrides) -> OrganizationContext:
    defaults = dict(
        tenant_id=TENANT,
        entity_name="Asteria Systems DemoCo",
        period_start=PERIOD[0],
        period_end=PERIOD[1],
        in_scope_systems=["github://asteria/api"],
        entitlements=[],
        profile_version=1,
    )
    defaults.update(overrides)
    return OrganizationContext(**defaults)


def approved(service, pack_id="software-change-management", version="2.0.0", registry=None):
    pack = registry.get(pack_id, version)
    service.register_pack(pack=pack, registered_by="standards-team@asteria.example")
    service.approve_pack(
        pack_id=pack_id,
        version=version,
        approved_by="dana.director@asteria.example",
        reason="Methodology reviewed against the current policy version.",
    )
    return pack


# -- admission -------------------------------------------------------------------


def test_every_released_pack_loads(registry):
    references = {pack.reference for pack in registry.list()}
    assert references == {
        "software-change-management@2.0.0",
        "identity-access@1.0.0",
        "privileged-access@1.0.0",
    }


def test_a_modified_pack_is_refused_before_it_is_parsed(tmp_path, pack_key):
    """The signature check runs first, so the parser is never the attack surface."""
    staged = tmp_path / "audit-packs"
    staged.mkdir()
    shutil.copytree(ROOT / "audit-packs/software-change-management", staged / "scm")
    shutil.copytree(ROOT / "audit-packs/schemas", staged / "schemas")
    (staged / "scm" / "README.md").write_text("modified after release\n", encoding="utf-8")

    with pytest.raises(PackSignatureError, match="file manifest does not match"):
        AuditPackRegistry(staged, trusted_public_key=pack_key).load()


def test_a_pack_signed_by_the_wrong_key_is_refused(tmp_path):
    staged = tmp_path / "audit-packs"
    staged.mkdir()
    shutil.copytree(ROOT / "audit-packs/identity-access", staged / "iam")
    shutil.copytree(ROOT / "audit-packs/schemas", staged / "schemas")
    agent_key = (ROOT / "security/release-keys/agent-release-public.pem").read_bytes()

    with pytest.raises(PackSignatureError, match="fingerprint does not match"):
        AuditPackRegistry(staged, trusted_public_key=agent_key).load()


def test_a_pack_that_declares_a_gate_no_procedure_enforces_is_refused(tmp_path, pack_key):
    """A gate in the methodology that stops nothing is worse than no gate.

    It satisfies a reviewer reading the pack, and it never fires. The manifest
    refuses it, which is why the check is on the typed manifest rather than in the
    JSON schema — the schema cannot express "declared and also used".
    """
    from assuranceos.standards.definitions import PackManifest

    raw = yaml.safe_load(
        (ROOT / "audit-packs/software-change-management/pack.yaml").read_text(encoding="utf-8")
    )
    raw["human_gates"].append("board_ratification")
    with pytest.raises(ValueError, match="no procedure enforces"):
        PackManifest.model_validate(raw)


def test_a_pack_whose_procedure_graph_does_not_resolve_is_refused():
    from assuranceos.standards.definitions import PackManifest

    raw = yaml.safe_load(
        (ROOT / "audit-packs/identity-access/pack.yaml").read_text(encoding="utf-8")
    )
    raw["procedures"][2]["depends_on"] = ["a-step-that-does-not-exist"]
    with pytest.raises(ValueError, match="depends on unknown steps"):
        PackManifest.model_validate(raw)


def test_a_control_test_step_must_pin_a_test():
    from assuranceos.standards.definitions import PackProcedure

    with pytest.raises(ValueError, match="pins no test_ref"):
        PackProcedure(
            key="run-test",
            step=1,
            agent="operating-effectiveness",
            action="Execute the population test.",
            task_type="control_test",
        )


def test_a_schema_invalid_pack_names_where_it_failed(tmp_path, pack_key):
    staged = tmp_path / "audit-packs"
    staged.mkdir()
    shutil.copytree(ROOT / "audit-packs/identity-access", staged / "iam")
    shutil.copytree(ROOT / "audit-packs/schemas", staged / "schemas")
    manifest_path = staged / "iam" / "pack.yaml"
    raw = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    raw["version"] = "not-a-version"
    manifest_path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")

    # The digest changed too, so the signature refusal comes first. That ordering
    # is the point: an unverified artefact is never parsed.
    with pytest.raises(PackSignatureError):
        AuditPackRegistry(staged, trusted_public_key=pack_key).load()


def test_the_schema_path_must_exist(tmp_path, pack_key):
    staged = tmp_path / "audit-packs"
    staged.mkdir()
    shutil.copytree(ROOT / "audit-packs/identity-access", staged / "iam")
    with pytest.raises(PackSchemaError, match="schema is missing"):
        AuditPackRegistry(staged, trusted_public_key=pack_key).load()


def test_an_unknown_pack_reports_what_is_available(registry):
    with pytest.raises(PackNotFoundError, match="available:"):
        registry.get("procure-to-pay", "1.0.0")


# -- compilation -----------------------------------------------------------------


def test_a_pack_compiles_into_the_engagement_graph(service, registry, database):
    approved(service, registry=registry)
    result = service.compile_engagement(
        pack_id="software-change-management",
        version="2.0.0",
        context=context(),
        engagement_id=ENGAGEMENT,
        compiled_by="engagement-director@asteria.example",
    )
    assert result["task_count"] == 11
    assert result["gate_count"] == 4

    with database.read_session() as session:
        tasks = EngagementRepository(session).list_tasks(TENANT, ENGAGEMENT)
    assert {task.task_key for task in tasks} == set(result["task_keys"])
    # The graph carries the pack's citations onto the tasks, so a finding raised
    # from one can cite its criteria without a model being asked to remember them.
    test_task = next(task for task in tasks if task.task_key == "execute-population-test")
    assert "control-test:SCM-01@2.0.0" in test_task.input_refs_json
    assert test_task.execution_policy_json["citations"]


def test_compiling_the_same_inputs_twice_produces_the_same_graph(registry, compiler):
    """Determinism is the claim; task ids are deliberately not part of it.

    Two engagements running the same pack are different engagements, so their task
    ids differ. What must not differ is the shape, the gates, or the pins.
    """
    pack = registry.get("software-change-management", "2.0.0")
    first_workflow, first_pins = compiler.compile(pack, context())
    second_workflow, second_pins = compiler.compile(pack, context())

    assert [task.key for task in first_workflow.tasks] == [
        task.key for task in second_workflow.tasks
    ]
    assert pins_digest(first_pins) == pins_digest(second_pins)
    assert first_workflow.metadata == second_workflow.metadata


def test_two_packs_produce_two_different_graphs(service, registry):
    approved(service, registry=registry)
    approved(service, "identity-access", "1.0.0", registry=registry)
    scm = service.compile_engagement(
        pack_id="software-change-management",
        version="2.0.0",
        context=context(),
        engagement_id=ENGAGEMENT,
        compiled_by="director@asteria.example",
    )
    iam = service.compile_engagement(
        pack_id="identity-access",
        version="1.0.0",
        context=context(),
        engagement_id=SECOND_ENGAGEMENT,
        compiled_by="director@asteria.example",
    )
    assert scm["task_keys"] != iam["task_keys"]
    assert scm["pins_digest"] != iam["pins_digest"]


def test_provenance_reports_every_version_the_graph_depended_on(service, registry):
    approved(service, registry=registry)
    service.compile_engagement(
        pack_id="software-change-management",
        version="2.0.0",
        context=context(),
        engagement_id=ENGAGEMENT,
        compiled_by="director@asteria.example",
    )
    provenance = service.provenance(tenant_id=TENANT, engagement_id=ENGAGEMENT)
    assert provenance["standard"] == "AST-SCM-POL@4.0"
    assert provenance["control_tests"] == {"SCM-01": "2.0.0"}
    assert set(provenance["criteria"]) == {
        "AST-POL-SCM-01",
        "AST-POL-SCM-02",
        "AST-POL-SCM-03",
    }
    assert provenance["agent_roles"]["skeptic"]


def test_an_uncompiled_engagement_says_so(service):
    with pytest.raises(PackCompilationError, match="was not compiled"):
        service.provenance(tenant_id=TENANT, engagement_id=ENGAGEMENT)


# -- the refusals ----------------------------------------------------------------


def test_an_unapproved_pack_does_not_compile(service, registry):
    pack = registry.get("identity-access", "1.0.0")
    service.register_pack(pack=pack, registered_by="standards-team@asteria.example")
    with pytest.raises(PackNotReleasedError, match="only from an approved pack"):
        service.compile_engagement(
            pack_id="identity-access",
            version="1.0.0",
            context=context(),
            engagement_id=ENGAGEMENT,
            compiled_by="director@asteria.example",
        )


def test_a_licensed_standard_needs_an_entitlement(registry, compiler):
    with pytest.raises(PackEntitlementError, match="holds no entitlement"):
        compiler.compile(registry.get("privileged-access", "1.0.0"), context())


def test_an_entitlement_admits_the_licensed_pack(service, registry, compiler):
    service.grant_entitlement(
        tenant_id=TENANT,
        standard_code="SYN-PAM-BENCH",
        licence_ref="SUB-2026-0042",
        granted_by="procurement@asteria.example",
        expires_on=date(2027, 1, 1),
    )
    entitlements = service.effective_entitlements(tenant_id=TENANT, on=date(2026, 7, 15))
    workflow, _ = compiler.compile(
        registry.get("privileged-access", "1.0.0"), context(entitlements=entitlements)
    )
    assert workflow.workflow_version == "privileged-access@1.0.0"


def test_an_expired_entitlement_is_not_an_entitlement(service):
    """Filtering at read time rather than at grant time is what makes expiry bite.

    A licence checked only when it was granted is a licence that never expires.
    """
    service.grant_entitlement(
        tenant_id=TENANT,
        standard_code="SYN-PAM-BENCH",
        licence_ref="SUB-2025-0001",
        granted_by="procurement@asteria.example",
        expires_on=date(2026, 1, 31),
    )
    assert service.effective_entitlements(tenant_id=TENANT, on=date(2026, 1, 1)) == [
        "SYN-PAM-BENCH"
    ]
    assert service.effective_entitlements(tenant_id=TENANT, on=date(2026, 7, 1)) == []


def test_criteria_must_cover_the_whole_audit_period(registry, compiler):
    """Partial coverage is a failure, not a warning.

    A rule in force from halfway through the period cannot support a conclusion
    about the period.
    """
    with pytest.raises(CriteriaEffectivityError, match="do not cover the audit period"):
        compiler.compile(
            registry.get("software-change-management", "2.0.0"),
            context(period_start=date(2025, 1, 1), period_end=date(2025, 12, 31)),
        )


def test_a_pinned_control_test_must_be_released(registry):
    bare = AuditPackCompiler(
        released_tests={}, released_agents=released_agent_versions(ROOT / "agents")
    )
    with pytest.raises(PackCompatibilityError, match="SCM-01 is not released"):
        bare.compile(registry.get("software-change-management", "2.0.0"), context())


def test_a_control_test_at_the_wrong_version_is_refused(registry):
    """Pinned exactly, not 'at least'.

    A pack validated against SCM-01@2.0.0 has not been validated against 2.1.0,
    and accepting the newer one would make the pack's evaluation evidence apply to
    a procedure nobody ran it against.
    """
    drifted = AuditPackCompiler(
        released_tests={"SCM-01": "2.1.0"},
        released_agents=released_agent_versions(ROOT / "agents"),
    )
    with pytest.raises(PackCompatibilityError, match="released at 2.1.0, pack pins 2.0.0"):
        drifted.compile(registry.get("software-change-management", "2.0.0"), context())


def test_an_unreleased_agent_role_is_refused(registry):
    partial = AuditPackCompiler(
        released_tests={"SCM-01": "2.0.0"},
        released_agents={"operating-effectiveness": "0.7.0"},
    )
    with pytest.raises(PackCompatibilityError) as excinfo:
        partial.compile(registry.get("software-change-management", "2.0.0"), context())
    assert "has no released package" in str(excinfo.value)


def test_every_unmet_requirement_is_reported_at_once(registry):
    """One message with the list, not one refusal per fix-and-retry cycle."""
    empty = AuditPackCompiler(released_tests={}, released_agents={})
    with pytest.raises(PackCompatibilityError) as excinfo:
        empty.compile(registry.get("software-change-management", "2.0.0"), context())
    message = str(excinfo.value)
    assert "control test SCM-01 is not released" in message
    assert message.count(";") >= 3


def test_a_platform_below_the_pack_floor_is_refused(registry):
    old = AuditPackCompiler(
        released_tests={"SCM-01": "2.0.0"},
        released_agents=released_agent_versions(ROOT / "agents"),
        platform_version="0.5.0",
    )
    with pytest.raises(PackCompatibilityError, match="requires platform >= 0.8.0"):
        old.compile(registry.get("software-change-management", "2.0.0"), context())


def test_an_engagement_compiles_once(service, registry):
    approved(service, registry=registry)
    approved(service, "identity-access", "1.0.0", registry=registry)
    service.compile_engagement(
        pack_id="software-change-management",
        version="2.0.0",
        context=context(),
        engagement_id=ENGAGEMENT,
        compiled_by="director@asteria.example",
    )
    with pytest.raises(PackCompilationError, match="already compiled"):
        service.compile_engagement(
            pack_id="identity-access",
            version="1.0.0",
            context=context(),
            engagement_id=ENGAGEMENT,
            compiled_by="director@asteria.example",
        )


def test_registration_is_idempotent_on_the_digest(service, registry):
    pack = registry.get("identity-access", "1.0.0")
    first = service.register_pack(pack=pack, registered_by="standards@asteria.example")
    second = service.register_pack(pack=pack, registered_by="standards@asteria.example")
    assert first == second


def test_a_different_artefact_cannot_reuse_a_registered_version(service, registry):
    """A pack version whose content can change is not a version."""
    from assuranceos.standards.packs import LoadedAuditPack

    pack = registry.get("identity-access", "1.0.0")
    service.register_pack(pack=pack, registered_by="standards@asteria.example")
    impostor = LoadedAuditPack(
        pack_dir=pack.pack_dir,
        manifest=pack.manifest,
        release_document={**pack.release_document, "package_sha256": "0" * 64},
    )
    with pytest.raises(PackNotReleasedError, match="a pack version is immutable"):
        service.register_pack(pack=impostor, registered_by="x@y.example")


def test_an_approved_pack_whose_artefact_changed_does_not_compile(service, registry, database):
    """The tamper case, said in its own sentence.

    The registration recorded a digest; the artefact on disk has another. That is
    not a signature failure — both may be validly signed — it is the approved
    methodology and the present one being different documents.
    """
    approved(service, registry=registry)
    with database.transaction() as session:
        from assuranceos.standards.repository import StandardsRepository

        record = StandardsRepository(session).get_registration(
            "software-change-management", "2.0.0"
        )
        record.package_sha256 = "1" * 64

    with pytest.raises(PackNotReleasedError, match="was approved"):
        service.compile_engagement(
            pack_id="software-change-management",
            version="2.0.0",
            context=context(),
            engagement_id=ENGAGEMENT,
            compiled_by="director@asteria.example",
        )


# -- standards, crosswalks, and change impact ------------------------------------


def scm_standard() -> StandardInput:
    return StandardInput(
        code="AST-SCM-POL",
        name="Asteria change-management policy",
        issuer="Asteria Systems DemoCo",
        version="4.0",
        effective_from=date(2026, 1, 1),
    )


def soc2_standard() -> StandardInput:
    return StandardInput(
        code="SYN-SOC",
        name="Synthetic service-organisation criteria",
        issuer="Synthetic Benchmark Consortium",
        version="2017",
        licence="subscriber-only",
        entitlement_required=True,
    )


def test_a_standard_version_is_immutable(service):
    service.register_standard(standard=scm_standard())
    with pytest.raises(DuplicateStandardError, match="immutable"):
        service.register_standard(standard=scm_standard())


def test_change_impact_walks_mappings_and_crosswalks_in_both_directions(service):
    """The question a standards team asks before adopting a framework version.

    A criterion that is the *target* of an equivalence is affected when its source
    is revised just as much as the other way round, so the walk is undirected.
    """
    service.register_standard(
        standard=scm_standard(),
        criteria=[
            CriterionInput(
                code="AST-POL-SCM-01",
                text="Every production change must be linked to an approved ticket.",
                citation="Asteria change policy v4, section 3.2",
            )
        ],
    )
    service.register_standard(
        standard=soc2_standard(),
        criteria=[
            CriterionInput(
                code="CC8.1",
                text="The entity authorises, designs, and implements changes to infrastructure.",
                citation="Synthetic service-organisation criteria, CC8.1",
            )
        ],
    )
    service.map_criterion(
        standard_code="AST-SCM-POL",
        standard_version="4.0",
        criterion_code="AST-POL-SCM-01",
        target_type="control",
        target_ref="SCM-01",
        coverage="full",
    )
    service.map_criterion(
        standard_code="AST-SCM-POL",
        standard_version="4.0",
        criterion_code="AST-POL-SCM-01",
        target_type="test",
        target_ref="SCM-01@2.0.0",
    )
    service.add_crosswalk(
        source=("SYN-SOC", "2017", "CC8.1"),
        target=("AST-SCM-POL", "4.0", "AST-POL-SCM-01"),
        crosswalk=CrosswalkInput(
            source_criterion="CC8.1",
            target_criterion="AST-POL-SCM-01",
            relation=CrosswalkRelation.SUPERSET,
            rationale="The policy requirement is one specific way of satisfying CC8.1.",
            asserted_by="standards-team@asteria.example",
        ),
    )

    impact = service.change_impact(
        standard_code="AST-SCM-POL",
        standard_version="4.0",
        criterion_code="AST-POL-SCM-01",
    )
    assert {item["target_ref"] for item in impact["mapped_targets"]} == {
        "SCM-01",
        "SCM-01@2.0.0",
    }
    # The crosswalk was asserted from CC8.1 towards this criterion, so it is found
    # as an inbound edge rather than missed.
    assert impact["linked_criteria"] == [
        {
            "criterion": "CC8.1",
            "standard": "SYN-SOC@2017",
            "relation": "superset",
            "direction": "inbound",
            "asserted_by": "standards-team@asteria.example",
        }
    ]
    assert impact["impact_count"] == 3


def test_a_crosswalk_needs_both_criteria_to_exist(service):
    from assuranceos.standards import CriterionNotFoundError

    service.register_standard(standard=scm_standard())
    service.register_standard(standard=soc2_standard())
    with pytest.raises(CriterionNotFoundError):
        service.add_crosswalk(
            source=("SYN-SOC", "2017", "CC8.1"),
            target=("AST-SCM-POL", "4.0", "AST-POL-SCM-01"),
            crosswalk=CrosswalkInput(
                source_criterion="CC8.1",
                target_criterion="AST-POL-SCM-01",
                relation=CrosswalkRelation.RELATED,
                rationale="Both concern change authorisation in production.",
                asserted_by="standards-team@asteria.example",
            ),
        )


def test_a_mapping_target_type_is_closed(service):
    service.register_standard(
        standard=scm_standard(),
        criteria=[
            CriterionInput(
                code="AST-POL-SCM-01",
                text="Every production change must be linked to an approved ticket.",
                citation="Asteria change policy v4, section 3.2",
            )
        ],
    )
    with pytest.raises(ValueError, match="target_type must be one of"):
        service.map_criterion(
            standard_code="AST-SCM-POL",
            standard_version="4.0",
            criterion_code="AST-POL-SCM-01",
            target_type="vibes",
            target_ref="anything",
        )


# -- pack upgrades ---------------------------------------------------------------


def test_an_upgrade_leaves_compiled_engagements_pinned(service, registry):
    """A new pack version creates new engagements; it does not mutate old ones.

    Reported as a list of the engagements that keep their pinned digest, so the
    claim is checkable rather than asserted.
    """
    approved(service, registry=registry)
    service.compile_engagement(
        pack_id="software-change-management",
        version="2.0.0",
        context=context(),
        engagement_id=ENGAGEMENT,
        compiled_by="director@asteria.example",
    )
    impact = service.upgrade_impact(
        pack_id="software-change-management",
        from_version="2.0.0",
        to_version="2.0.0",
    )
    assert impact["procedures_added"] == []
    assert impact["engagements_unaffected"] == [
        {
            "engagement_id": ENGAGEMENT,
            "pinned_version": "2.0.0",
            "pinned_digest": registry.get("software-change-management", "2.0.0").package_sha256[
                :12
            ],
        }
    ]
