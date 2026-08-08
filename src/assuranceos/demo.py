from __future__ import annotations

from datetime import date
from pathlib import Path

from .db.models import Engagement, EvidenceRecord, Finding, Tenant
from .db.repositories import (
    AuditEventRepository,
    EngagementRepository,
    EvidenceRepository,
    FindingRepository,
    OutboxRepository,
    TenantRepository,
)
from .deterministic import run_scm_population_test
from .evidence import capture_file
from .ledger import AuditLedger
from .models import AuditEvent


TENANT_ID = "tnt_asteria_demo"
ENGAGEMENT_ID = "eng_asteria_scm_2026h2"

MIME_TYPES = {
    ".json": "application/json",
    ".md": "text/markdown",
    ".csv": "text/csv",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".xml": "application/xml",
    ".yaml": "application/yaml",
}


# The four sources the SCM-01 conclusion actually rests on. Everything else in
# the corpus is collected as evidence, but a finding cites the records that
# support it rather than the whole collection — an evidence list that includes
# the marketing site is not a chain of custody, it is a directory listing.
SUPPORTING_SOURCES = (
    "sources/github/pull_requests.json",
    "sources/jira/change_tickets.json",
    "sources/governance/approved_exceptions.json",
    "sources/confluence/change_management_policy.md",
)


# Each corpus directory replays one connected system, so the evidence citation
# names that system rather than the directory it was replayed from.
SOURCE_SCHEMES = {
    "cloud": "gcp",
    "confluence": "confluence",
    "finance": "netsuite",
    "github": "github",
    "governance": "governance",
    "hr": "workday",
    "identity": "entra",
    "jira": "jira",
    "legal": "ironclad",
    "public": "https",
}


def source_locator(path: Path, demo_root: Path) -> str:
    """Name the source system a collected file came from."""
    relative = path.resolve().relative_to((demo_root / "sources").resolve()).as_posix()
    system, _, remainder = relative.partition("/")
    scheme = SOURCE_SCHEMES.get(system, system)
    if scheme == "https":
        return f"https://asteria-demo.invalid/{remainder}"
    return f"{scheme}://asteria/{remainder}"


def _digest_matches(path: Path, expected: str) -> bool:
    """Re-read the stored object and confirm it still hashes to what was recorded."""
    import hashlib

    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest() == expected


def collect_corpus(demo_root: Path) -> list[Path]:
    """Every file the connected source systems expose, in a stable order."""
    root = demo_root / "sources"
    return sorted(path for path in root.rglob("*") if path.is_file())


def run_golden_engagement(demo_root: Path, ledger: AuditLedger, *, reset: bool = True) -> dict:
    """Collect the whole Asteria corpus and run the SCM-01 population test.

    ``reset`` clears the entire demonstration tenant first, which is what a
    standalone run wants. Pass ``False`` to replace only this engagement, so the
    golden audit can be re-run inside a tenant other demonstrations also
    populated without deleting their plan, report, or trace records.
    """
    if reset:
        ledger.reset_tenant(TENANT_ID)
    else:
        _reset_engagement(ledger)
    # Fieldwork collects the whole corpus; the population test reads the subset
    # the control is defined over.
    evidence_paths = collect_corpus(demo_root)
    evidence = [capture_file(path, source_type=path.parts[-2]) for path in evidence_paths]
    supporting = [
        item
        for item in evidence
        if any(item.source_locator.replace("\\", "/").endswith(name) for name in SUPPORTING_SOURCES)
    ]
    test_result = run_scm_population_test(demo_root)

    prompt_attack = {
        "evidence_id": next(
            item.evidence_id
            for item in evidence
            if item.source_locator.endswith("change_management_policy.md")
        ),
        "classification": "prompt_injection",
        "action": "tainted_and_denied",
        "attempted_action": "expand scope and retrieve credentials",
        "canonical_state_mutated": False,
    }
    exceptions = test_result["exceptions"]
    finding_payload = {
        "finding_id": "fnd_scm_001",
        "status": "proposed",
        "title": "Production change merged without required approval",
        "criteria": (
            "SCM-01 requires an approved change ticket and at least one independent "
            "approval before merge."
        ),
        "affected_population": len(exceptions),
        "severity": "high",
        "human_gate": "finding_approval",
        "evidence_ids": [item.evidence_id for item in supporting],
    }

    events = [
        AuditEvent(
            event_type="schedule.occurrence.created",
            tenant_id=TENANT_ID,
            engagement_id=ENGAGEMENT_ID,
            payload={"schedule_id": "sch_scm_semiannual", "nominal_due": "2026-08-01"},
        ),
        AuditEvent(
            event_type="engagement.started",
            tenant_id=TENANT_ID,
            engagement_id=ENGAGEMENT_ID,
            payload={
                "audit_pack": "software-change-management@1.0.0",
                "period": ["2026-07-01", "2026-07-31"],
            },
        ),
        *[
            AuditEvent(
                event_type="evidence.captured",
                tenant_id=TENANT_ID,
                engagement_id=ENGAGEMENT_ID,
                payload=item.model_dump(mode="json"),
            )
            for item in evidence
        ],
        AuditEvent(
            event_type="test.completed",
            tenant_id=TENANT_ID,
            engagement_id=ENGAGEMENT_ID,
            payload=test_result,
        ),
        AuditEvent(
            event_type="security.prompt_injection.denied",
            tenant_id=TENANT_ID,
            engagement_id=ENGAGEMENT_ID,
            payload=prompt_attack,
        ),
        AuditEvent(
            event_type="finding.proposed",
            tenant_id=TENANT_ID,
            engagement_id=ENGAGEMENT_ID,
            payload=finding_payload,
        ),
    ]

    # Canonical records, audit events, and the integration outbox commit together.
    with ledger.database.transaction() as session:
        tenants = TenantRepository(session)
        if tenants.get(TENANT_ID) is None:
            tenants.add(
                Tenant(
                    tenant_id=TENANT_ID,
                    slug="asteria-demo",
                    name="Asteria Systems DemoCo",
                    status="active",
                    region="europe-west1",
                )
            )
            session.flush()
        EngagementRepository(session).add(
            Engagement(
                engagement_id=ENGAGEMENT_ID,
                tenant_id=TENANT_ID,
                code="AST-SCM-2026-H2",
                title="Software Change Management Audit",
                status="fieldwork",
                audit_pack_ref="software-change-management@1.0.0",
                period_start=date(2026, 7, 1),
                period_end=date(2026, 7, 31),
                scope_json={
                    "repositories": sorted(
                        {item["repository"] for item in test_result["all_results"]}
                    )
                },
            )
        )
        evidence_repository = EvidenceRepository(session)
        for item in evidence:
            source_path = Path(item.source_locator)
            evidence_repository.add(
                EvidenceRecord(
                    evidence_id=item.evidence_id,
                    tenant_id=TENANT_ID,
                    engagement_id=ENGAGEMENT_ID,
                    source_type=item.source_type,
                    # The locator names the system the record came from. The
                    # filesystem path is where the demonstration keeps the
                    # replayed export, and it belongs in object_uri with the
                    # rest of the storage detail, not in the citation an auditor
                    # reads.
                    source_locator=source_locator(source_path, demo_root),
                    content_sha256=item.sha256,
                    object_uri=source_path.resolve().as_uri(),
                    mime_type=MIME_TYPES.get(source_path.suffix, "application/octet-stream"),
                    size_bytes=source_path.stat().st_size,
                    classification=item.classification,
                    collected_at=item.collected_at,
                    accepted=item.accepted,
                    tainted=item.evidence_id == prompt_attack["evidence_id"],
                    # Re-read from the stored object and compare, rather than
                    # trusting the digest computed a moment earlier during
                    # capture. A record whose integrity nobody checked should say
                    # so, and one that was checked should say that instead.
                    integrity_status=(
                        "verified" if _digest_matches(source_path, item.sha256) else "failed"
                    ),
                    metadata_json={"synthetic": True},
                )
            )
        FindingRepository(session).add(
            Finding(
                finding_id=finding_payload["finding_id"],
                tenant_id=TENANT_ID,
                engagement_id=ENGAGEMENT_ID,
                code="SCM-001",
                version=1,
                title=finding_payload["title"],
                status="proposed",
                severity="high",
                confidence=0.95,
                risk_statement="Unauthorized or unreviewed production changes may be deployed.",
                criteria=finding_payload["criteria"],
                # Written from the population rather than asserted, so the
                # condition cannot drift away from the evidence behind it.
                observed_condition=(
                    f"{test_result['exception_count']} of {test_result['population_count']} "
                    "in-period production changes lacked an approved change ticket or the "
                    "required independent approval: "
                    + ", ".join(item["pull_request_id"] for item in exceptions)
                    + "."
                ),
                affected_population_json={
                    "population": test_result["population_count"],
                    "exceptions": test_result["exception_count"],
                },
                evidence_ids_json=finding_payload["evidence_ids"],
                exception_keys_json=[
                    str(item.get("pull_request_id") or item.get("subject_ref") or "")
                    for item in exceptions
                    if item.get("pull_request_id") or item.get("subject_ref")
                ],
            )
        )
        AuditEventRepository(session).append_many(events)
        OutboxRepository(session).add(
            tenant_id=TENANT_ID,
            aggregate_type="finding",
            aggregate_id=finding_payload["finding_id"],
            event_type="finding.proposed",
            payload=finding_payload,
            idempotency_key=f"finding.proposed:{finding_payload['finding_id']}:v1",
        )

    return {
        "tenant_id": TENANT_ID,
        "engagement_id": ENGAGEMENT_ID,
        "test_result": test_result,
        "finding": finding_payload,
        "security_event": prompt_attack,
        "event_count": len(events),
    }


def _reset_engagement(ledger: AuditLedger) -> None:
    """Remove only this engagement, leaving the rest of the tenant intact.

    Every record the golden audit writes hangs off the engagement row, so
    deleting it cascades. The outbox is keyed by tenant rather than engagement,
    so its rows are removed by idempotency key instead.
    """
    from sqlalchemy import select

    from .db.models import OutboxEvent

    with ledger.database.transaction() as session:
        engagement = session.get(Engagement, ENGAGEMENT_ID)
        if engagement is not None:
            session.delete(engagement)
        for message in session.scalars(
            select(OutboxEvent).where(
                OutboxEvent.tenant_id == TENANT_ID,
                OutboxEvent.aggregate_id == "fnd_scm_001",
            )
        ):
            session.delete(message)
