"""The fail-closed report renderer.

One rule, stated once: **a material claim either resolves to admissible evidence,
or it carries a stated limitation, or the report does not render.**

Everything else in this module is that rule made precise. What counts as
admissible, what counts as a limitation, and what happens when the evidence exists
but was gathered for something else.

The renderer is pure: evidence is passed in as views, the clock is passed in as a
date, and nothing here reads a database. That is what allows the same function to
answer "would this report render" without producing one.

Rendering refuses with **every** unresolved issue rather than the first. An author
who fixes one missing citation and is then told about the next has been given a
worse experience than one handed the list — and, more importantly, a partial list
invites the belief that the last fix was the last problem.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from datetime import date, datetime, timezone
from typing import Any

from .definitions import (
    ClaimInput,
    ClaimIssue,
    ClaimType,
    EvidencePolicy,
    EvidenceView,
    RenderedClaim,
    ReportRequest,
    ReuseJustification,
)
from .exceptions import UnsupportedClaimError


def _as_utc(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def check_claim(
    claim: ClaimInput,
    *,
    evidence: Mapping[str, EvidenceView],
    policy: EvidencePolicy,
    period: tuple[date, date],
    as_at: date,
    engagement_id: str,
    reuse: Mapping[str, ReuseJustification],
) -> list[ClaimIssue]:
    """Every reason this claim cannot be rendered, or an empty list.

    Non-material claims are checked for referential integrity — a citation that
    points at nothing is an error whatever the claim's weight — but are not gated
    on support.
    """
    issues: list[ClaimIssue] = []
    cited = (
        list(claim.supporting_evidence_ids)
        + list(claim.contradicting_evidence_ids)
        + list(claim.qualifying_evidence_ids)
    )

    for evidence_id in cited:
        if evidence_id not in evidence:
            issues.append(
                ClaimIssue(
                    claim_key=claim.key,
                    code="unknown_evidence",
                    detail=(
                        f"cites {evidence_id!r}, which is not a retrievable evidence "
                        "record for this tenant"
                    ),
                    evidence_id=evidence_id,
                )
            )

    if not claim.material:
        return issues

    admissible: list[EvidenceView] = []
    for evidence_id in claim.supporting_evidence_ids:
        record = evidence.get(evidence_id)
        if record is None:
            continue
        if not record.is_admissible():
            issues.append(
                ClaimIssue(
                    claim_key=claim.key,
                    code="inadmissible_evidence",
                    detail=(
                        f"{evidence_id} is not admissible: accepted={record.accepted}, "
                        f"deleted={record.deleted}, integrity={record.integrity_status}"
                    ),
                    evidence_id=evidence_id,
                )
            )
            continue

        # Reuse. Evidence collected for another engagement, or outside the audit
        # period, was collected under a different scope. Carrying it across is
        # normal and needs to be said, so a justification is required unless the
        # policy explicitly permits silence.
        justified = evidence_id in reuse
        if (
            record.engagement_id is not None
            and record.engagement_id != engagement_id
            and not policy.allow_cross_engagement
            and not justified
        ):
            issues.append(
                ClaimIssue(
                    claim_key=claim.key,
                    code="cross_engagement_reuse",
                    detail=(
                        f"{evidence_id} was collected for engagement "
                        f"{record.engagement_id}; reusing it here needs a recorded "
                        "justification"
                    ),
                    evidence_id=evidence_id,
                )
            )
            continue

        moment = _as_utc(record.source_time or record.collected_at).date()
        if not (period[0] <= moment <= period[1]):
            if not policy.allow_out_of_period and not justified:
                issues.append(
                    ClaimIssue(
                        claim_key=claim.key,
                        code="out_of_period_evidence",
                        detail=(
                            f"{evidence_id} is dated {moment.isoformat()}, outside the "
                            f"audit period {period[0].isoformat()} to "
                            f"{period[1].isoformat()}; using it needs a recorded "
                            "justification"
                        ),
                        evidence_id=evidence_id,
                    )
                )
                continue

        age = (as_at - _as_utc(record.collected_at).date()).days
        if age > policy.freshness_days and not claim.limitations:
            issues.append(
                ClaimIssue(
                    claim_key=claim.key,
                    code="stale_evidence",
                    detail=(
                        f"{evidence_id} was collected {age} days ago, beyond the "
                        f"{policy.freshness_days}-day freshness window, and the claim "
                        "states no limitation"
                    ),
                    evidence_id=evidence_id,
                )
            )
            continue

        admissible.append(record)

    # The rule. Support, or a stated limitation, or no report.
    if not admissible and not claim.limitations:
        issues.append(
            ClaimIssue(
                claim_key=claim.key,
                code="unsupported_material_claim",
                detail=(
                    "a material claim resolves to no admissible supporting evidence "
                    "and states no limitation"
                ),
            )
        )

    # Tainted evidence is what a guardrail flagged — a document carrying an
    # injection, a source that failed inspection. It may appear in a report; it
    # may not be the only thing a material claim rests on.
    if (
        admissible
        and all(record.tainted for record in admissible)
        and not policy.allow_tainted_sole_support
    ):
        issues.append(
            ClaimIssue(
                claim_key=claim.key,
                code="tainted_sole_support",
                detail=(
                    "every admissible supporting record is tainted; a material claim "
                    "may not rest solely on evidence a guardrail flagged"
                ),
            )
        )

    # Contradictions that were found and not disclosed are the failure mode this
    # whole component exists to prevent. Finding them is good work; publishing
    # without them is what makes the report untrue.
    if claim.contradicting_evidence_ids and not claim.limitations:
        issues.append(
            ClaimIssue(
                claim_key=claim.key,
                code="undisclosed_contradiction",
                detail=(
                    f"{len(claim.contradicting_evidence_ids)} contradicting record(s) are "
                    "linked to this claim and no limitation discloses them"
                ),
            )
        )

    return issues


def render(
    request: ReportRequest,
    *,
    tenant_id: str,
    engagement_id: str,
    evidence: Sequence[EvidenceView],
    signer: Any | None = None,
) -> tuple[dict[str, Any], list[ClaimIssue]]:
    """Render a report, or return the issues that stop it.

    Returns ``(document, issues)``. When ``issues`` is non-empty the document is
    ``{}`` — there is no partially rendered report, because a report missing its
    unsupportable paragraphs is a different and more dangerous document than one
    that failed to render.
    """
    index = {record.evidence_id: record for record in evidence}
    reuse = {item.evidence_id: item for item in request.reuse_justifications}
    period = (request.period_start, request.period_end)

    issues: list[ClaimIssue] = []
    for claim in request.claims:
        issues.extend(
            check_claim(
                claim,
                evidence=index,
                policy=request.policy,
                period=period,
                as_at=request.as_at,
                engagement_id=engagement_id,
                reuse=reuse,
            )
        )

    by_key = {claim.key: claim for claim in request.claims}
    for section in request.template.sections:
        if section.key in request.template.required_sections and not section.claim_keys:
            issues.append(
                ClaimIssue(
                    claim_key=section.key,
                    code="empty_required_section",
                    detail=(
                        f"section {section.key!r} is required by template "
                        f"{request.template.reference} and carries no claims"
                    ),
                )
            )

    if issues:
        return {}, issues

    rendered: list[RenderedClaim] = []
    for claim in request.claims:
        rendered.append(
            RenderedClaim(
                key=claim.key,
                claim_type=claim.claim_type,
                statement=claim.statement,
                material=claim.material,
                confidence=claim.confidence,
                supporting_evidence=[
                    _evidence_entry(index[item])
                    for item in claim.supporting_evidence_ids
                    if item in index
                ],
                contradicting_evidence=[
                    _evidence_entry(index[item])
                    for item in claim.contradicting_evidence_ids
                    if item in index
                ],
                limitations=list(claim.limitations),
                finding_id=claim.finding_id,
            )
        )

    sections = [
        {
            "key": section.key,
            "heading": section.heading,
            "narrative": section.narrative,
            "claims": [
                by_key[key].key for key in section.claim_keys if key in by_key
            ],
        }
        for section in request.template.sections
    ]

    # Limitations are collected to the front of the report as well as staying on
    # their claims. A reader who reads only the summary still meets them.
    limitations = sorted(
        {
            limitation
            for claim in request.claims
            for limitation in claim.limitations
        }
        | {
            claim.statement
            for claim in request.claims
            if claim.claim_type is ClaimType.LIMITATION
        }
    )

    cited_ids = sorted(
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
    evidence_index = [_evidence_entry(index[item]) for item in cited_ids if item in index]

    document: dict[str, Any] = {
        "schema": "assurance.report.v1",
        "template": request.template.reference,
        "report_type": request.template.report_type.value,
        "title": request.template.title,
        "tenant_id": tenant_id,
        "engagement_id": engagement_id,
        "period_start": request.period_start.isoformat(),
        "period_end": request.period_end.isoformat(),
        "as_at": request.as_at.isoformat(),
        "prepared_by": request.prepared_by,
        "sections": sections,
        "claims": [item.model_dump(mode="json") for item in rendered],
        "limitations": limitations,
        "evidence_index": evidence_index,
    }
    document["document_sha256"] = document_digest(document)
    if signer is not None:
        from assuranceos.vault.signing import signature_document

        document["signature"] = signature_document(
            signer=signer, payload=canonical_bytes(document)
        )
    return document, []


def _evidence_entry(record: EvidenceView) -> dict[str, Any]:
    """One row of the evidence index.

    Carries the digest, so a reader holding the export can confirm the bytes they
    were given are the bytes the report was written against.
    """
    return {
        "evidence_id": record.evidence_id,
        "source_type": record.source_type,
        "source_locator": record.source_locator,
        "classification": record.classification,
        "collected_at": _as_utc(record.collected_at).isoformat(),
        "content_sha256": record.content_sha256,
        "tainted": record.tainted,
        "integrity_status": record.integrity_status,
    }


def canonical_bytes(document: Mapping[str, Any]) -> bytes:
    """The document's canonical serialisation, excluding its own digest.

    The digest and the signature are excluded from what is hashed, because a
    document that includes its own hash cannot be verified.
    """
    payload = {
        key: value
        for key, value in document.items()
        if key not in {"document_sha256", "signature"}
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")


def document_digest(document: Mapping[str, Any]) -> str:
    return hashlib.sha256(canonical_bytes(document)).hexdigest()


def require_rendered(
    document: Mapping[str, Any], issues: Sequence[ClaimIssue]
) -> dict[str, Any]:
    """Return the document, or raise with every issue that stopped it."""
    if issues:
        lines = "; ".join(f"{item.claim_key}: {item.code} - {item.detail}" for item in issues)
        raise UnsupportedClaimError(
            f"the report cannot be issued: {len(issues)} unresolved issue(s): {lines}"
        )
    return dict(document)
