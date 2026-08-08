from __future__ import annotations


from pathlib import Path

from assuranceos.corpus import PERIOD_END, PERIOD_START, AsteriaCorpus
from assuranceos.db.models import Tenant
from assuranceos.db.repositories import TenantRepository
from assuranceos.db.session import Database

from .definitions import ControlTestRunRequest
from .registry import ControlTestRegistry
from .service import ControlTestService

DEMO_TENANT = "tnt_asteria"


def build_service(database: Database, root: Path) -> ControlTestService:
    registry = ControlTestRegistry(
        root / "tests-library",
        trusted_public_key=(root / "security/release-keys/control-test-release-public.pem").read_bytes(),
    ).load()
    service = ControlTestService(database, registry)
    service.synchronize_registry(released_by="demo-release-pipeline")
    return service


def run_control_test_demo(
    database: Database, root: Path, tenant_id: str | None = None
) -> dict:
    """Run every released control test over the collected Asteria corpus.

    The populations are read from the corpus rather than written here. That is
    the point of the exercise: the same signed test, in the same sandbox, over
    the volume a real engagement produces — forty-four merges across six
    repositories and eighteen leavers across two workforce feeds — rather than a
    hand-picked trio that can only produce the answer the demonstration wanted.

    SLA-01 is the case that needs three systems at once. Nothing inside the
    incident process can answer it, because the obligation is written in a
    contract and the incident process has never read one.
    """
    tenant = tenant_id or DEMO_TENANT
    with database.transaction() as session:
        if TenantRepository(session).get(tenant) is None:
            TenantRepository(session).add(
                Tenant(tenant_id=tenant, slug="asteria", name="Asteria Systems DemoCo")
            )
    service = build_service(database, root)
    corpus = AsteriaCorpus(root / "demo/asteria")

    scm_datasets = corpus.scm_datasets()
    scm_population = next(item for item in scm_datasets if item.name == "pull_requests")
    scm = service.run(
        tenant,
        ControlTestRunRequest(
            test_id="SCM-01",
            version="2.0.0",
            purpose="Operating effectiveness of SCM-01 over the July 2026 change population",
            period_start=PERIOD_START,
            period_end=PERIOD_END,
            requested_by="usr_demo_auditor",
            idempotency_key="demo:scm-01:2026-07",
            parameters={
                "expected_population_count": len(scm_population.records),
                "required_approvals": 1,
            },
            datasets=scm_datasets,
        ),
    )

    iam_datasets = corpus.iam_datasets()
    iam_population = next(item for item in iam_datasets if item.name == "terminated_users")
    iam = service.run(
        tenant,
        ControlTestRunRequest(
            test_id="IAM-01",
            version="1.0.0",
            purpose="Operating effectiveness of IAM-01 over the FY2026 leaver population",
            period_start=PERIOD_START,
            period_end=PERIOD_END,
            requested_by="usr_demo_auditor",
            idempotency_key="demo:iam-01:2026-07",
            parameters={"expected_population_count": len(iam_population.records)},
            datasets=iam_datasets,
        ),
    )
    sla_datasets = corpus.sla_datasets()
    sla_population = next(item for item in sla_datasets if item.name == "incidents")
    sla = service.run(
        tenant,
        ControlTestRunRequest(
            test_id="SLA-01",
            version="1.0.0",
            purpose=(
                "Contractual incident response commitments over the July 2026 "
                "P1 incident population"
            ),
            period_start=PERIOD_START,
            period_end=PERIOD_END,
            requested_by="usr_demo_auditor",
            idempotency_key="demo:sla-01:2026-07",
            parameters={
                "expected_population_count": len(sla_population.records),
                "in_scope_priorities": ["P1"],
            },
            datasets=sla_datasets,
        ),
    )

    return {
        "tenant_id": tenant,
        "released_tests": service.list_releases(),
        "corpus": corpus.collection_summary(),
        "runs": [
            scm.model_dump(mode="json"),
            iam.model_dump(mode="json"),
            sla.model_dump(mode="json"),
        ],
        "access_review_observation": corpus.access_review_status(),
        "expected": {
            "SCM-01": {"conclusion": "ineffective", "exception_count": 3},
            "IAM-01": {"conclusion": "ineffective", "exception_count": 1},
            "SLA-01": {"conclusion": "ineffective", "exception_count": 4},
        },
    }
