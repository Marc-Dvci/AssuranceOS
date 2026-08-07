"""Persist governance records as canonical state.

A gateway denial, a guardrail verdict, and a reasoning chain are evidence about
how the fleet behaved. They are written through the same transaction boundary as
the audit event they belong to, so a decision and its attributable record either
both land or neither does.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Iterable, Sequence
from uuid import uuid4

from sqlalchemy import select

from ..db.models import (
    AgentIdentityRecord,
    GatewayDecisionRecord,
    GuardrailFindingRecord,
    ReasoningSpanRecord,
)
from ..db.repositories import AuditEventRepository
from ..db.session import Database
from .gateway import GatewayDecision
from .identity import SignedAgentIdentity
from .telemetry import ReasoningChain


class GovernanceRecorder:
    """Writes identities, decisions, guardrail findings, and spans to canonical state."""

    def __init__(self, database: Database):
        self.database = database

    # -- identity --------------------------------------------------------------

    def record_identity(self, signed: SignedAgentIdentity, *, release_id: str | None = None) -> str:
        identity = signed.identity
        row_id = f"air_{uuid4().hex[:16]}"
        with self.database.transaction() as session:
            session.add(
                AgentIdentityRecord(
                    identity_row_id=row_id,
                    identity_id=identity.identity_id,
                    tenant_id=identity.tenant_id,
                    workload_uri=identity.workload_uri,
                    agent_role=identity.agent_role,
                    agent_version=identity.agent_version,
                    release_id=release_id or identity.release_id,
                    engagement_id=identity.engagement_id,
                    task_id=identity.task_id,
                    attempt=identity.attempt,
                    lease_owner=identity.lease_owner,
                    granted_tools_json=list(identity.granted_tools),
                    granted_scopes_json=list(identity.granted_evidence_scopes),
                    forbidden_actions_json=list(identity.forbidden_actions),
                    independence_subject=identity.independence_subject,
                    key_id=signed.key_id,
                    issued_at=signed.issued_at,
                    expires_at=signed.expires_at,
                    status="issued",
                )
            )
        return row_id

    def revoke_identity(self, tenant_id: str, identity_id: str, *, reason: str) -> bool:
        with self.database.transaction() as session:
            row = session.scalar(
                select(AgentIdentityRecord).where(
                    AgentIdentityRecord.tenant_id == tenant_id,
                    AgentIdentityRecord.identity_id == identity_id,
                )
            )
            if row is None:
                return False
            row.status = "revoked"
            row.revoked_at = datetime.now(timezone.utc)
            row.revocation_reason = reason
            return True

    def revoked_identity_ids(self, tenant_id: str) -> set[str]:
        with self.database.read_session() as session:
            rows = session.scalars(
                select(AgentIdentityRecord.identity_id).where(
                    AgentIdentityRecord.tenant_id == tenant_id,
                    AgentIdentityRecord.status == "revoked",
                )
            )
            return set(rows)

    # -- decisions and guardrails ---------------------------------------------

    def record_decisions(
        self,
        decisions: Sequence[GatewayDecision],
        *,
        audit_events: Iterable | None = None,
        engagement_id: str | None = None,
    ) -> int:
        """Persist gateway decisions, their guardrail findings, and audit events atomically."""
        written = 0
        with self.database.transaction() as session:
            for decision in decisions:
                session.add(
                    GatewayDecisionRecord(
                        decision_row_id=f"gwr_{uuid4().hex[:16]}",
                        decision_id=decision.decision_id,
                        tenant_id=decision.tenant_id,
                        decision=decision.decision,
                        stage=decision.stage,
                        reason=decision.reason,
                        agent_role=decision.agent_role,
                        tool_name=decision.tool_name,
                        task_id=decision.task_id,
                        engagement_id=engagement_id,
                        identity_id=decision.identity_id,
                        trace_id=decision.trace_id,
                        span_id=decision.span_id,
                        occurred_at=decision.occurred_at,
                        attributes_json=dict(decision.attributes),
                    )
                )
                for result in decision.armor:
                    for finding in result.findings:
                        session.add(
                            GuardrailFindingRecord(
                                finding_row_id=f"grf_{uuid4().hex[:16]}",
                                tenant_id=decision.tenant_id,
                                decision_id=decision.decision_id,
                                trace_id=decision.trace_id,
                                span_id=decision.span_id,
                                direction=result.direction,
                                verdict=result.verdict,
                                detector=finding.detector,
                                category=finding.category,
                                severity=finding.severity,
                                match_count=finding.match_count,
                                excerpt_digest=finding.excerpt_digest,
                                detail=finding.detail,
                                occurred_at=decision.occurred_at,
                            )
                        )
                written += 1
            if audit_events:
                AuditEventRepository(session).append_many(audit_events)
        return written

    # -- reasoning chains ------------------------------------------------------

    def record_chain(
        self,
        chain: ReasoningChain,
        *,
        tenant_id: str,
        engagement_id: str | None = None,
        task_id: str | None = None,
        agent_role: str | None = None,
    ) -> int:
        with self.database.transaction() as session:
            for span in chain.spans:
                session.add(
                    ReasoningSpanRecord(
                        span_row_id=f"rsp_{uuid4().hex[:16]}",
                        tenant_id=tenant_id,
                        trace_id=span.trace_id,
                        span_id=span.span_id,
                        parent_span_id=span.parent_span_id,
                        sequence_no=span.sequence,
                        name=span.name,
                        engagement_id=engagement_id,
                        task_id=task_id,
                        agent_role=agent_role,
                        status=span.status,
                        status_message=span.status_message,
                        started_at=span.started_at,
                        ended_at=span.ended_at,
                        duration_ms=span.duration_ms,
                        otel_exported="otel.span_id" in span.attributes,
                        attributes_json=dict(span.attributes),
                        events_json=list(span.events),
                    )
                )
        return len(chain.spans)

    def load_chain(self, tenant_id: str, trace_id: str) -> ReasoningChain:
        """Rebuild a reasoning chain from canonical state alone."""
        from .telemetry import RecordedSpan

        with self.database.read_session() as session:
            rows = session.scalars(
                select(ReasoningSpanRecord)
                .where(
                    ReasoningSpanRecord.tenant_id == tenant_id,
                    ReasoningSpanRecord.trace_id == trace_id,
                )
                .order_by(ReasoningSpanRecord.sequence_no, ReasoningSpanRecord.started_at)
            )
            chain = ReasoningChain(trace_id=trace_id)
            for row in rows:
                chain.add(
                    RecordedSpan(
                        name=row.name,
                        trace_id=row.trace_id,
                        span_id=row.span_id,
                        parent_span_id=row.parent_span_id,
                        sequence=row.sequence_no,
                        started_at=row.started_at,
                        ended_at=row.ended_at,
                        status=row.status,
                        status_message=row.status_message,
                        attributes=dict(row.attributes_json or {}),
                        events=list(row.events_json or []),
                    )
                )
            return chain

    def list_decisions(
        self, tenant_id: str, *, decision: str | None = None
    ) -> list[GatewayDecisionRecord]:
        with self.database.read_session() as session:
            stmt = select(GatewayDecisionRecord).where(
                GatewayDecisionRecord.tenant_id == tenant_id
            )
            if decision is not None:
                stmt = stmt.where(GatewayDecisionRecord.decision == decision)
            stmt = stmt.order_by(
                GatewayDecisionRecord.occurred_at, GatewayDecisionRecord.decision_id
            )
            return list(session.scalars(stmt))

    def list_guardrail_findings(
        self, tenant_id: str, *, verdict: str | None = None
    ) -> list[GuardrailFindingRecord]:
        with self.database.read_session() as session:
            stmt = select(GuardrailFindingRecord).where(
                GuardrailFindingRecord.tenant_id == tenant_id
            )
            if verdict is not None:
                stmt = stmt.where(GuardrailFindingRecord.verdict == verdict)
            stmt = stmt.order_by(GuardrailFindingRecord.occurred_at)
            return list(session.scalars(stmt))


class DatabaseRevocationChecker:
    """Revocation list backed by canonical state, consulted on every authentication."""

    def __init__(self, recorder: GovernanceRecorder, tenant_id: str):
        self.recorder = recorder
        self.tenant_id = tenant_id

    def is_revoked(self, identity_id: str) -> bool:
        return identity_id in self.recorder.revoked_identity_ids(self.tenant_id)
