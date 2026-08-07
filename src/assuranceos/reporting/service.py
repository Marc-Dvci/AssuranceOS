"""Access-aware retrieval, the claim graph, and issuing reports.

Three responsibilities that are really one: making sure a sentence in a report can
be followed back to a record somebody can look at.

**Retrieval is access-aware and returns views, not rows.** It is scoped to a
tenant and, by default, to an engagement. A classification the caller may not see
is excluded rather than redacted — a redacted row still tells the reader that
something exists.

**The claim graph is canonical.** A claim and its evidence links are rows, not a
structure assembled at render time, so the same graph answers "what does this
sentence rest on" and "where else has this record been used".

**Issuance is a decision, and rendering is a gate in front of it.** A report that
does not render cannot be issued; a report that renders is not thereby issued. The
two are separate because they are different people's jobs.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, select

from assuranceos.db.models import (
    Claim,
    ClaimEvidenceLink,
    Engagement,
    EvidenceRecord,
    Finding,
    ReportVersion,
)
from assuranceos.db.repositories import AuditEventRepository, OutboxRepository, new_id
from assuranceos.db.session import Database
from assuranceos.models import AuditEvent

from .definitions import (
    ClaimInput,
    ClaimIssue,
    ClaimType,
    EvidenceRelationship,
    EvidenceView,
    ReportRequest,
)
from .exceptions import ReportNotFoundError, ReportingError
from .renderer import canonical_bytes, render, require_rendered

#: Classifications a caller sees by default. Anything more restricted has to be
#: asked for explicitly, so a report writer does not quote a record they did not
#: know they had.
DEFAULT_VISIBLE_CLASSIFICATIONS = frozenset({"public", "internal", "confidential"})


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class ReportingService:
    """Retrieval over canonical evidence, the claim graph, and report issuance."""

    def __init__(
        self,
        database: Database,
        *,
        signer: Any | None = None,
        agent_role_prefixes: Sequence[str] = ("agent:", "svc:", "system:"),
    ):
        self.database = database
        self.signer = signer
        self.agent_role_prefixes = tuple(agent_role_prefixes)

    # -- retrieval -------------------------------------------------------------

    def retrieve(
        self,
        *,
        tenant_id: str,
        engagement_id: str | None = None,
        visible_classifications: Sequence[str] | None = None,
        include_deleted: bool = False,
        query: str | None = None,
        limit: int = 500,
    ) -> list[EvidenceView]:
        """Evidence this caller may see, as views rather than rows.

        ``query`` is a substring match over the source locator and filename. It is
        deliberately not semantic: a semantic index is a useful way to *find*
        candidates and a terrible thing to let a conclusion rest on, because the
        set it returns is not reproducible. Every claim resolves to explicit
        evidence ids, and this is only how a person finds them.
        """
        visible = frozenset(visible_classifications or DEFAULT_VISIBLE_CLASSIFICATIONS)
        with self.database.read_session() as session:
            statement = select(EvidenceRecord).where(EvidenceRecord.tenant_id == tenant_id)
            if engagement_id is not None:
                statement = statement.where(EvidenceRecord.engagement_id == engagement_id)
            if not include_deleted:
                statement = statement.where(EvidenceRecord.deleted_at.is_(None))
            if query:
                pattern = f"%{query.lower()}%"
                statement = statement.where(
                    func.lower(EvidenceRecord.source_locator).like(pattern)
                    | func.lower(func.coalesce(EvidenceRecord.original_filename, "")).like(
                        pattern
                    )
                )
            statement = statement.order_by(
                EvidenceRecord.collected_at, EvidenceRecord.evidence_id
            ).limit(limit)
            return [
                self._view(record)
                for record in session.scalars(statement)
                if record.classification in visible
            ]

    def resolve(self, *, tenant_id: str, evidence_ids: Sequence[str]) -> list[EvidenceView]:
        """Views for a specific set of ids, whatever their classification.

        Used at render time. A claim that cites a record the writer could see must
        not fail to resolve because the renderer runs under a narrower view; the
        access decision belongs at the point the citation was made.
        """
        if not evidence_ids:
            return []
        with self.database.read_session() as session:
            rows = session.scalars(
                select(EvidenceRecord).where(
                    EvidenceRecord.tenant_id == tenant_id,
                    EvidenceRecord.evidence_id.in_(list(evidence_ids)),
                )
            )
            return [self._view(record) for record in rows]

    @staticmethod
    def _view(record: EvidenceRecord) -> EvidenceView:
        return EvidenceView(
            evidence_id=record.evidence_id,
            engagement_id=record.engagement_id,
            source_type=record.source_type,
            source_locator=record.source_locator,
            classification=record.classification,
            accepted=record.accepted,
            tainted=record.tainted,
            integrity_status=record.integrity_status,
            deleted=record.deleted_at is not None,
            collected_at=record.collected_at,
            source_time=record.source_time,
            content_sha256=record.content_sha256,
        )

    # -- the claim graph -------------------------------------------------------

    def record_claims(
        self, *, tenant_id: str, engagement_id: str, claims: Sequence[ClaimInput]
    ) -> dict[str, str]:
        """Persist claims and their evidence links as canonical rows.

        Returns ``{claim_key: claim_id}``. Links carry their relationship, so a
        contradiction is stored as a contradiction rather than being dropped
        because it was inconvenient — which is the only way the renderer can later
        refuse an undisclosed one.
        """
        created: dict[str, str] = {}
        with self.database.transaction() as session:
            for claim in claims:
                record = Claim(
                    claim_id=new_id("clm"),
                    tenant_id=tenant_id,
                    engagement_id=engagement_id,
                    task_id=claim.task_id,
                    finding_id=claim.finding_id,
                    claim_type=claim.claim_type.value,
                    statement=claim.statement,
                    status="recorded",
                    confidence=claim.confidence,
                    limitations_json=list(claim.limitations),
                )
                session.add(record)
                session.flush()
                created[claim.key] = record.claim_id

                for relationship, ids in (
                    (EvidenceRelationship.SUPPORTS, claim.supporting_evidence_ids),
                    (EvidenceRelationship.CONTRADICTS, claim.contradicting_evidence_ids),
                    (EvidenceRelationship.QUALIFIES, claim.qualifying_evidence_ids),
                ):
                    for evidence_id in ids:
                        session.add(
                            ClaimEvidenceLink(
                                link_id=new_id("cel"),
                                tenant_id=tenant_id,
                                claim_id=record.claim_id,
                                evidence_id=evidence_id,
                                relationship=relationship.value,
                            )
                        )
                session.flush()
        return created

    def evidence_usage(self, *, tenant_id: str, evidence_id: str) -> list[dict[str, Any]]:
        """Every claim this record has been used to support, anywhere.

        The question asked when a record turns out to be wrong. Without the graph
        it is answered by reading reports; with it, it is a query.
        """
        with self.database.read_session() as session:
            rows = session.execute(
                select(Claim, ClaimEvidenceLink)
                .join(ClaimEvidenceLink, ClaimEvidenceLink.claim_id == Claim.claim_id)
                .where(
                    ClaimEvidenceLink.tenant_id == tenant_id,
                    ClaimEvidenceLink.evidence_id == evidence_id,
                )
                .order_by(Claim.engagement_id, Claim.claim_id)
            )
            return [
                {
                    "claim_id": claim.claim_id,
                    "engagement_id": claim.engagement_id,
                    "statement": claim.statement,
                    "relationship": link.relationship,
                    "finding_id": claim.finding_id,
                }
                for claim, link in rows
            ]

    # -- rendering and issuance ------------------------------------------------

    def dry_run(
        self, *, tenant_id: str, engagement_id: str, request: ReportRequest
    ) -> list[ClaimIssue]:
        """What would stop this report, without producing or storing anything.

        The call a report author makes while writing. It is separate from
        ``prepare`` so that "can this be issued" never has the side effect of
        creating a version.
        """
        evidence = self._evidence_for(tenant_id, request)
        _, issues = render(
            request, tenant_id=tenant_id, engagement_id=engagement_id, evidence=evidence
        )
        return issues

    def prepare(
        self, *, tenant_id: str, engagement_id: str, request: ReportRequest
    ) -> dict[str, Any]:
        """Render a report and store it as a draft version.

        Refuses if any material claim is unsupported. The refusal carries every
        issue, and nothing is written: there is no partially rendered report,
        because a report missing its unsupportable paragraphs is a different and
        more dangerous document than one that failed to render.
        """
        evidence = self._evidence_for(tenant_id, request)
        document, issues = render(
            request,
            tenant_id=tenant_id,
            engagement_id=engagement_id,
            evidence=evidence,
            signer=self.signer,
        )
        require_rendered(document, issues)

        with self.database.transaction() as session:
            engagement = session.get(Engagement, engagement_id)
            if engagement is None or engagement.tenant_id != tenant_id:
                raise ReportingError(
                    f"engagement {engagement_id!r} was not found for tenant {tenant_id!r}"
                )
            version = int(
                session.scalar(
                    select(func.max(ReportVersion.version)).where(
                        ReportVersion.tenant_id == tenant_id,
                        ReportVersion.engagement_id == engagement_id,
                        ReportVersion.report_type == request.template.report_type.value,
                    )
                )
                or 0
            ) + 1
            record = ReportVersion(
                report_id=new_id("rpt"),
                tenant_id=tenant_id,
                engagement_id=engagement_id,
                report_type=request.template.report_type.value,
                template_ref=request.template.reference,
                version=version,
                status="draft",
                title=request.template.title,
                document_json=document,
                document_sha256=str(document["document_sha256"]),
                signature_json=dict(document.get("signature") or {}),
                claim_count=len(request.claims),
                material_claim_count=sum(1 for item in request.claims if item.material),
                limitation_count=len(document["limitations"]),
                evidence_count=len(document["evidence_index"]),
                prepared_by=request.prepared_by,
            )
            session.add(record)
            session.flush()
            self._emit(
                session,
                tenant_id=tenant_id,
                engagement_id=engagement_id,
                aggregate_id=record.report_id,
                event_type="report.prepared",
                payload={
                    "report_id": record.report_id,
                    "report_type": record.report_type,
                    "template": record.template_ref,
                    "version": version,
                    "document_sha256": record.document_sha256,
                    "material_claims": record.material_claim_count,
                    "limitations": record.limitation_count,
                    "prepared_by": request.prepared_by,
                },
                idempotency_key=f"report-prepared:{record.report_id}",
            )
            return {
                "report_id": record.report_id,
                "version": version,
                "status": "draft",
                "document_sha256": record.document_sha256,
                "document": document,
            }

    def issue(
        self, *, tenant_id: str, report_id: str, issued_by: str, reason: str
    ) -> dict[str, Any]:
        """Issue a prepared report.

        Refused for automated actors. A report is the organisation speaking, and
        an agent that can issue one has been handed the organisation's voice.

        Refused if the stored document's digest no longer matches its content,
        which is the tamper case: a draft edited in the database between
        preparation and issuance.
        """
        if issued_by.lower().startswith(self.agent_role_prefixes):
            raise ReportingError(
                f"{issued_by!r} is an automated actor; issuing a report requires a "
                "decision attributable to a person"
            )
        with self.database.transaction() as session:
            record = session.get(ReportVersion, report_id)
            if record is None or record.tenant_id != tenant_id:
                raise ReportNotFoundError(f"report {report_id!r} was not found")
            if record.status == "issued":
                return {
                    "report_id": report_id,
                    "status": "issued",
                    "issued_by": record.issued_by,
                    "created": False,
                }
            from .renderer import document_digest

            recomputed = document_digest(record.document_json or {})
            if recomputed != record.document_sha256:
                raise ReportingError(
                    f"report {report_id!r} no longer matches the digest it was prepared "
                    f"under ({record.document_sha256[:12]} vs {recomputed[:12]}); it "
                    "must be re-prepared rather than issued"
                )
            record.status = "issued"
            record.issued_by = issued_by
            record.issued_at = utc_now()
            record.issue_reason = reason
            self._emit(
                session,
                tenant_id=tenant_id,
                engagement_id=record.engagement_id,
                aggregate_id=report_id,
                event_type="report.issued",
                payload={
                    "report_id": report_id,
                    "report_type": record.report_type,
                    "version": record.version,
                    "document_sha256": record.document_sha256,
                    "issued_by": issued_by,
                    "reason": reason,
                },
                idempotency_key=f"report-issued:{report_id}",
            )
            return {
                "report_id": report_id,
                "status": "issued",
                "issued_by": issued_by,
                "created": True,
            }

    def get(self, *, tenant_id: str, report_id: str) -> dict[str, Any]:
        with self.database.read_session() as session:
            record = session.get(ReportVersion, report_id)
            if record is None or record.tenant_id != tenant_id:
                raise ReportNotFoundError(f"report {report_id!r} was not found")
            return {
                "report_id": record.report_id,
                "engagement_id": record.engagement_id,
                "report_type": record.report_type,
                "template": record.template_ref,
                "version": record.version,
                "status": record.status,
                "document_sha256": record.document_sha256,
                "signature": dict(record.signature_json or {}),
                "prepared_by": record.prepared_by,
                "issued_by": record.issued_by,
                "issued_at": record.issued_at.isoformat() if record.issued_at else None,
                "document": dict(record.document_json or {}),
            }

    def verify(
        self, *, tenant_id: str, report_id: str, public_key_pem: bytes | None = None
    ) -> dict[str, Any]:
        """Recompute a stored report's digest and, if a key is supplied, its signature.

        The check a recipient runs. It exists as a first-class operation because
        "the report you were sent is the report we issued" is the claim an export
        makes, and a claim nobody can check is not one.

        ``signature_valid`` is ``None`` rather than ``False`` when no public key
        was supplied. "Not checked" and "checked and wrong" must not collapse into
        the same answer — that collapse is how an unverified export comes to be
        described as a failed one, or worse, the reverse.
        """
        stored = self.get(tenant_id=tenant_id, report_id=report_id)
        document = stored["document"]
        from .renderer import document_digest

        recomputed = document_digest(document)
        result: dict[str, Any] = {
            "report_id": report_id,
            "stored_sha256": stored["document_sha256"],
            "recomputed_sha256": recomputed,
            "digest_matches": recomputed == stored["document_sha256"],
            "signed": bool(stored["signature"]),
            "signature_valid": None,
            "signature_checked": False,
        }
        if stored["signature"] and public_key_pem is not None:
            from assuranceos.vault.signing import verify_signature

            result["signature_checked"] = True
            try:
                verify_signature(
                    payload=canonical_bytes(document),
                    signature=stored["signature"],
                    public_key_pem=public_key_pem,
                )
                result["signature_valid"] = True
            except ValueError:
                result["signature_valid"] = False
        return result

    # -- cross-engagement analytics --------------------------------------------

    def themes(self, *, tenant_id: str) -> list[dict[str, Any]]:
        """Findings whose code recurs across engagements.

        Recurrence is a different signal from severity: a medium finding in its
        third consecutive engagement is a different problem from a medium finding
        seen once, and only a cross-engagement view can say so.
        """
        with self.database.read_session() as session:
            rows = session.scalars(
                select(Finding)
                .where(Finding.tenant_id == tenant_id, Finding.status != "rejected")
                .order_by(Finding.code, Finding.created_at)
            )
            grouped: dict[str, list[Finding]] = {}
            for row in rows:
                grouped.setdefault(row.code, []).append(row)
            return [
                {
                    "code": code,
                    "occurrences": len(items),
                    "engagements": sorted({item.engagement_id for item in items}),
                    "severities": sorted({item.severity for item in items}),
                    "latest_status": items[-1].status,
                    "open": any(
                        item.status
                        not in {"closed_verified", "rejected", "withdrawn", "risk_accepted"}
                        for item in items
                    ),
                }
                for code, items in sorted(grouped.items())
                if len({item.engagement_id for item in items}) > 1
            ]

    # -- internals -------------------------------------------------------------

    def _evidence_for(self, tenant_id: str, request: ReportRequest) -> list[EvidenceView]:
        cited = sorted(
            {
                item
                for claim in request.claims
                for item in (
                    claim.supporting_evidence_ids
                    + claim.contradicting_evidence_ids
                    + claim.qualifying_evidence_ids
                )
            }
        )
        return self.resolve(tenant_id=tenant_id, evidence_ids=cited)

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
    ) -> None:
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
            aggregate_type="report",
            aggregate_id=aggregate_id,
            event_type=event_type,
            payload=dict(payload),
            idempotency_key=idempotency_key,
        )


def claim_from_finding(
    *,
    key: str,
    finding_id: str,
    code: str,
    statement: str,
    evidence_ids: Sequence[str],
    limitations: Sequence[str] = (),
    contradicting_evidence_ids: Sequence[str] = (),
    confidence: float = 0.0,
) -> ClaimInput:
    """Build a report claim from an approved finding.

    The finding's evidence and its recorded contradictions carry over rather than
    being restated. A report that quietly drops the contradictions a finding was
    approved with has published a stronger statement than the one that was
    approved.
    """
    return ClaimInput(
        key=key,
        claim_type=ClaimType.CONCLUSION,
        statement=statement,
        material=True,
        confidence=confidence,
        supporting_evidence_ids=list(evidence_ids),
        contradicting_evidence_ids=list(contradicting_evidence_ids),
        limitations=list(limitations),
        finding_id=finding_id,
    )
