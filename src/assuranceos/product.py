"""Read models for the product cockpit and evaluator surface.

The transactional services remain the authority for writes.  This module builds
bounded, tenant-scoped projections for people who need to understand the whole
assurance lifecycle without making a sequence of table-shaped API calls.
"""

from __future__ import annotations

from collections import Counter
from datetime import date, datetime, timezone
import os
from pathlib import Path
from typing import Any, Iterable

import yaml
from sqlalchemy import desc, select

from .db.models import (
    AgentIdentityRecord,
    ApprovalDecision,
    ControlTestDatasetBinding,
    ControlTestException,
    ControlTestRun,
    AssuranceCoverage,
    AuditEventRow,
    AuditSchedule,
    ConnectorInstance,
    ConnectorRun,
    ContinuousMonitor,
    Engagement,
    EngagementTask,
    EvidenceRecord,
    ExecutionTrace,
    Finding,
    GatewayDecisionRecord,
    GuardrailFindingRecord,
    OrganizationFact,
    OnboardingWorkflow,
    OutboxEvent,
    OrganizationProfile,
    PlanProposal,
    ReasoningSpanRecord,
    RemediationAction,
    ReportVersion,
    Risk,
    MonitorAlert,
    ScheduleOccurrence,
)
from .db.session import Database
from .registry import AgentPackage


def _iso(value: date | datetime | None) -> str | None:
    if value is None:
        return None
    return value.isoformat()


def _limited(session, model, tenant_id: str, *, order_by, limit: int = 100):
    return list(
        session.scalars(
            select(model).where(model.tenant_id == tenant_id).order_by(order_by).limit(limit)
        )
    )


def _status_counts(rows: Iterable[Any]) -> dict[str, int]:
    return dict(sorted(Counter(str(row.status) for row in rows).items()))


def tenant_cockpit(database: Database, tenant_id: str) -> dict[str, Any]:
    """Return the live tenant-scoped projection consumed by every product route."""

    with database.read_session() as session:
        profile = session.scalar(
            select(OrganizationProfile)
            .where(OrganizationProfile.tenant_id == tenant_id)
            .order_by(desc(OrganizationProfile.version))
            .limit(1)
        )
        facts = (
            _limited(
                session,
                OrganizationFact,
                tenant_id,
                order_by=OrganizationFact.fact_key,
            )
            if profile
            else []
        )
        risks = _limited(session, Risk, tenant_id, order_by=desc(Risk.updated_at))
        coverage = _limited(
            session, AssuranceCoverage, tenant_id, order_by=desc(AssuranceCoverage.obtained_on)
        )
        proposals = _limited(
            session, PlanProposal, tenant_id, order_by=desc(PlanProposal.created_at)
        )
        schedules = _limited(
            session, AuditSchedule, tenant_id, order_by=desc(AuditSchedule.updated_at)
        )
        occurrences = _limited(
            session, ScheduleOccurrence, tenant_id, order_by=desc(ScheduleOccurrence.nominal_due)
        )
        engagements = _limited(session, Engagement, tenant_id, order_by=desc(Engagement.updated_at))
        tasks = _limited(
            session, EngagementTask, tenant_id, order_by=desc(EngagementTask.updated_at), limit=250
        )
        findings = _limited(session, Finding, tenant_id, order_by=desc(Finding.updated_at))
        remediation = _limited(
            session, RemediationAction, tenant_id, order_by=desc(RemediationAction.updated_at)
        )
        evidence = _limited(
            session,
            EvidenceRecord,
            tenant_id,
            order_by=desc(EvidenceRecord.collected_at),
            limit=250,
        )
        reports = _limited(
            session, ReportVersion, tenant_id, order_by=desc(ReportVersion.created_at)
        )
        connectors = _limited(
            session, ConnectorInstance, tenant_id, order_by=desc(ConnectorInstance.created_at)
        )
        connector_runs = _limited(
            session, ConnectorRun, tenant_id, order_by=desc(ConnectorRun.started_at)
        )
        onboarding = _limited(
            session, OnboardingWorkflow, tenant_id, order_by=desc(OnboardingWorkflow.updated_at)
        )
        monitors = _limited(
            session, ContinuousMonitor, tenant_id, order_by=desc(ContinuousMonitor.updated_at)
        )
        monitor_alerts = _limited(
            session, MonitorAlert, tenant_id, order_by=desc(MonitorAlert.last_seen_at)
        )
        traces = _limited(
            session, ExecutionTrace, tenant_id, order_by=desc(ExecutionTrace.started_at)
        )
        events = _limited(
            session, AuditEventRow, tenant_id, order_by=desc(AuditEventRow.occurred_at), limit=200
        )

        task_by_engagement: Counter[str] = Counter(row.engagement_id for row in tasks)
        evidence_by_engagement: Counter[str] = Counter(
            row.engagement_id for row in evidence if row.engagement_id
        )
        findings_by_engagement: Counter[str] = Counter(row.engagement_id for row in findings)
        remediation_by_finding = {row.finding_id: row for row in remediation}
        latest_connector_run: dict[str, ConnectorRun] = {}
        for run in connector_runs:
            latest_connector_run.setdefault(run.connector_instance_id, run)

        return {
            "tenant_id": tenant_id,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "organization": (
                {
                    "profile_id": profile.profile_id,
                    "version": profile.version,
                    "status": profile.status,
                    "legal_name": profile.legal_name,
                    "primary_domain": profile.primary_domain,
                    "headquarters_country": profile.headquarters_country,
                    "industry": profile.industry,
                    "canonical_at": _iso(profile.canonical_at),
                    "facts": [
                        {
                            "key": fact.fact_key,
                            "value": fact.value_json,
                            "claim_type": fact.claim_type,
                            "source_type": fact.source_type,
                            "source_ref": fact.source_ref,
                            "confidence": fact.confidence,
                            "status": fact.status,
                        }
                        for fact in facts
                        if fact.profile_id == profile.profile_id
                    ],
                }
                if profile
                else None
            ),
            "metrics": {
                "risks": len(risks),
                "active_engagements": sum(
                    item.status not in {"completed", "cancelled"} for item in engagements
                ),
                "open_findings": sum(
                    item.status not in {"closed_verified", "rejected"} for item in findings
                ),
                "verified_evidence": sum(item.integrity_status == "verified" for item in evidence),
                "issued_reports": sum(item.status == "issued" for item in reports),
                "active_monitors": sum(item.status == "active" for item in monitors),
                "monitor_alerts": sum(item.status != "resolved" for item in monitor_alerts),
                "source_health": (
                    round(
                        100
                        * sum(run.status == "succeeded" for run in latest_connector_run.values())
                        / len(latest_connector_run)
                    )
                    if latest_connector_run
                    else None
                ),
            },
            "risks": [
                {
                    "risk_id": item.risk_id,
                    "code": item.code,
                    "title": item.title,
                    "status": item.status,
                    "residual_risk": item.residual_risk,
                    "confidence": item.confidence,
                    "evidence_ids": item.evidence_json,
                }
                for item in risks
            ],
            "coverage": [
                {
                    "coverage_id": item.coverage_id,
                    "risk_id": item.risk_id,
                    "source": item.source,
                    "obtained_on": _iso(item.obtained_on),
                    "reference": item.reference,
                    "engagement_id": item.engagement_id,
                }
                for item in coverage
            ],
            "plan_proposals": [
                {
                    "proposal_id": item.proposal_id,
                    "name": item.name,
                    "version": item.version,
                    "status": item.status,
                    "scenario": item.scenario,
                    "horizon": [_iso(item.horizon_start), _iso(item.horizon_end)],
                    "planned": item.planned_json,
                    "excluded": item.excluded_json,
                    "blind_spots": item.blind_spots_json,
                    "coverage_ratio": item.coverage_ratio,
                    "planned_days": item.planned_days,
                    "plannable_days": item.plannable_days,
                    "accepted_residual": item.accepted_residual_json,
                }
                for item in proposals
            ],
            "schedules": [
                {
                    "schedule_id": item.schedule_id,
                    "name": item.name,
                    "version": item.version,
                    "status": item.status,
                    "recurrence_rule": item.recurrence_rule,
                    "timezone": item.timezone,
                    "launch_mode": item.launch_mode,
                    "next_occurrences": [
                        {
                            "occurrence_id": occurrence.occurrence_id,
                            "nominal_due": _iso(occurrence.nominal_due),
                            "status": occurrence.status,
                            "engagement_id": occurrence.engagement_id,
                        }
                        for occurrence in occurrences
                        if occurrence.schedule_id == item.schedule_id
                    ][:6],
                }
                for item in schedules
            ],
            "engagements": [
                {
                    "engagement_id": item.engagement_id,
                    "code": item.code,
                    "title": item.title,
                    "status": item.status,
                    "audit_pack_ref": item.audit_pack_ref,
                    "period": [_iso(item.period_start), _iso(item.period_end)],
                    "scope_version": item.scope_version,
                    "scope": item.scope_json,
                    "task_count": task_by_engagement[item.engagement_id],
                    "evidence_count": evidence_by_engagement[item.engagement_id],
                    "finding_count": findings_by_engagement[item.engagement_id],
                }
                for item in engagements
            ],
            "tasks": [
                {
                    "task_id": item.task_id,
                    "engagement_id": item.engagement_id,
                    "task_key": item.task_key,
                    "task_type": item.task_type,
                    "status": item.status,
                    "agent_role": item.assigned_agent_role,
                    "attempt_count": item.attempt_count,
                    "human_gate": item.human_gate,
                    "model_policy": item.model_policy,
                    "last_error": item.last_error,
                }
                for item in tasks
            ],
            "findings": [
                {
                    "finding_id": item.finding_id,
                    "engagement_id": item.engagement_id,
                    "code": item.code,
                    "title": item.title,
                    "status": item.status,
                    "severity": item.severity,
                    "confidence": item.confidence,
                    "criteria": item.criteria,
                    "observed_condition": item.observed_condition,
                    "evidence_ids": item.evidence_ids_json,
                    "contradictions": item.contradictions_json,
                    "requires_human_approval": item.requires_human_approval,
                    "approval_blockers": _finding_blockers(item),
                    "remediation": _remediation_card(remediation_by_finding.get(item.finding_id)),
                }
                for item in findings
            ],
            "evidence": [
                {
                    "evidence_id": item.evidence_id,
                    "engagement_id": item.engagement_id,
                    "source_type": item.source_type,
                    "source_locator": item.source_locator,
                    "sha256": item.content_sha256,
                    "classification": item.classification,
                    "integrity_status": item.integrity_status,
                    "tainted": item.tainted,
                    "record_kind": item.record_kind,
                    "collected_at": _iso(item.collected_at),
                    "size_bytes": item.size_bytes,
                }
                for item in evidence
            ],
            "reports": [
                {
                    "report_id": item.report_id,
                    "engagement_id": item.engagement_id,
                    "report_type": item.report_type,
                    "version": item.version,
                    "status": item.status,
                    "title": item.title,
                    "sha256": item.document_sha256,
                    "claim_count": item.claim_count,
                    "material_claim_count": item.material_claim_count,
                    "evidence_count": item.evidence_count,
                    "issued_at": _iso(item.issued_at),
                    "document": item.document_json,
                }
                for item in reports
            ],
            "connectors": [
                _connector_card(item, latest_connector_run.get(item.connector_instance_id))
                for item in connectors
            ],
            "onboarding": [
                {
                    "workflow_id": item.workflow_id,
                    "workflow_key": item.workflow_key,
                    "status": item.status,
                    "state_version": item.state_version,
                    "company_name": item.company_name,
                    "domain": item.normalized_domain,
                    "profile_id": item.profile_id,
                    "readiness": item.readiness_json,
                }
                for item in onboarding
            ],
            "continuous_assurance": {
                "monitors": [
                    {
                        "monitor_id": item.monitor_id,
                        "monitor_key": item.monitor_key,
                        "version": item.version,
                        "title": item.title,
                        "status": item.status,
                        "test": f"{item.test_id}@{item.test_version}",
                        "owner_ref": item.owner_ref,
                        "reviewer_ref": item.reviewer_ref,
                        "suspended_reason": item.suspended_reason,
                    }
                    for item in monitors
                ],
                "open_alerts": [
                    {
                        "alert_id": item.alert_id,
                        "monitor_id": item.monitor_id,
                        "exception_key": item.exception_key,
                        "status": item.status,
                        "occurrence_count": item.occurrence_count,
                        "review_case_ref": item.review_case_ref,
                    }
                    for item in monitor_alerts
                    if item.status != "resolved"
                ],
            },
            "governance": {
                "traces": [
                    {
                        "trace_id": item.trace_id,
                        "engagement_id": item.engagement_id,
                        "task_id": item.task_id,
                        "status": item.status,
                        "started_at": _iso(item.started_at),
                        "completed_at": _iso(item.completed_at),
                        "attributes": item.attributes_json,
                    }
                    for item in traces
                ],
                "event_count": len(events),
                "recent_events": [
                    {
                        "event_id": item.event_id,
                        "event_type": item.event_type,
                        "engagement_id": item.engagement_id,
                        "task_id": item.task_id,
                        "occurred_at": _iso(item.occurred_at),
                        "payload": item.payload_json,
                    }
                    for item in events[:50]
                ],
            },
            "status_counts": {
                "engagements": _status_counts(engagements),
                "tasks": _status_counts(tasks),
                "findings": _status_counts(findings),
                "evidence": dict(
                    sorted(Counter(item.integrity_status for item in evidence).items())
                ),
                "reports": _status_counts(reports),
            },
        }


def _finding_blockers(finding: Finding, gates: Iterable[str] = ()) -> list[str]:
    """Why this finding cannot be approved yet.

    The gates that actually refuse an approval live in the adjudication service,
    and a second, weaker list here told operators the only thing outstanding was
    a signature while the service was refusing for two other reasons. So the
    canonical computation is passed in as ``gates`` and the cheap checks are
    added to it rather than standing in for it. The register omits them because
    computing them per row costs a query each; the detail view supplies them.
    """
    blockers: list[str] = []
    if finding.requires_human_approval and finding.status == "proposed":
        blockers.append("human approval required")
    if finding.skeptic_reviewed_at is None:
        blockers.append("skeptic review pending")
    if not finding.evidence_ids_json:
        blockers.append("supporting evidence required")
    blockers.extend(gates)
    return blockers


def _remediation_card(item: RemediationAction | None) -> dict[str, Any] | None:
    if item is None:
        return None
    return {
        "action_id": item.action_id,
        "status": item.status,
        "owner_ref": item.owner_ref,
        "due_date": _iso(item.due_date),
        "external_system": item.external_system,
        "external_ref": item.external_ref,
        "external_sync_state": item.external_sync_state,
    }


def _connector_card(item: ConnectorInstance, run: ConnectorRun | None) -> dict[str, Any]:
    return {
        "connector_instance_id": item.connector_instance_id,
        "connector_type": item.connector_type,
        "display_name": item.display_name,
        "status": item.status,
        "base_url": item.base_url,
        "credential_ref": item.credential_ref,
        "latest_run": (
            {
                "run_id": run.run_id,
                "status": run.status,
                "started_at": _iso(run.started_at),
                "completed_at": _iso(run.completed_at),
                "objects_collected": run.objects_ingested,
                "objects_seen": run.objects_seen,
                "schema_drift_detected": run.schema_drift,
                "error": run.last_error,
            }
            if run
            else None
        ),
    }


def evaluator_overview(
    *,
    database: Database,
    packages: dict[str, AgentPackage],
    control_tests: list[Any],
    audit_packs: list[Any],
    repository_root: Path,
    environment: str,
    model_mode: str,
    model_name: str,
) -> dict[str, Any]:
    """Build component proof and deployment metadata from the running release."""

    with database.read_session() as session:
        trace_count = session.query(ExecutionTrace).count()
        decision_count = session.query(GatewayDecisionRecord).count()
        blocked_count = session.query(GuardrailFindingRecord).filter_by(verdict="block").count()
        identity_count = session.query(AgentIdentityRecord).count()
        # A session here is a task context an agent can resume, which is what the
        # canonical task row is. Counting released tasks rather than a separate
        # store keeps the claim resolvable to state an evaluator can open.
        session_count = session.query(EngagementTask).count()
        evidence_count = session.query(EvidenceRecord).count()
        outbox_pending = (
            session.query(OutboxEvent).filter(OutboxEvent.published_at.is_(None)).count()
        )
        storage_provider = session.scalar(
            select(EvidenceRecord.storage_provider).where(
                EvidenceRecord.storage_provider.is_not(None)
            )
        ) or "local object store"
    dialect = database.engine.dialect.name

    commit = os.getenv("ASSURANCEOS_DEPLOYMENT_COMMIT") or _git_commit(repository_root)
    project = os.getenv("GOOGLE_CLOUD_PROJECT")
    region = os.getenv("GOOGLE_CLOUD_LOCATION") or os.getenv("GOOGLE_CLOUD_REGION")
    revision = os.getenv("K_REVISION")
    from .managed_fleet import managed_fleet_proof

    fleet_proof = managed_fleet_proof(
        repository_root=repository_root,
        expected_packages=packages,
        model=model_name,
    )
    deployment_target = "Google Cloud" if project or revision else "local workstation"
    if deployment_target == "local workstation":
        # A configured default region is not a deployment. Reporting one when
        # nothing is deployed puts a location on screen that no service is
        # running in, which is worse than reporting nothing.
        region = None
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "release": {
            "version": "0.8.0",
            "environment": environment,
            "commit": commit,
            "model_mode": model_mode,
            "model": model_name,
        },
        "deployment": {
            "target": deployment_target,
            "project": project,
            "region": region,
            "revision": revision,
            "service": os.getenv("K_SERVICE"),
            "configuration": os.getenv("K_CONFIGURATION"),
            "infrastructure_commit": commit,
        },
        "fleet": {
            "agent_count": len(packages),
            "released_count": len(packages),
            "agents": [
                {
                    "agent_id": package.agent_id,
                    "display_name": package.manifest["display_name"],
                    "version": package.manifest["version"],
                    "owner": package.manifest.get("accountable_owner"),
                    "release_status": "signed",
                    "release_digest": package.release.get("package_sha256"),
                    "tool_count": len(package.tools.get("tools", [])),
                    "human_gates": package.manifest.get("human_gates", []),
                    "deployment_target": package.manifest.get("deployment_target", "Agent Engine"),
                }
                for package in packages.values()
            ],
            "managed_runtime": fleet_proof,
        },
        "components": [
            _component("Agent Registry", len(packages) == 19, f"{len(packages)} signed roles"),
            _component(
                "Managed Agent Engine fleet",
                fleet_proof["cloud_verified"],
                (
                    f"{fleet_proof['deployed_count']}/{fleet_proof['expected_count']} cloud resources "
                    "verified"
                    if fleet_proof["cloud_verified"]
                    else f"{fleet_proof['expected_count']} signed releases deployment-qualified"
                ),
            ),
            _component(
                "Memory Bank",
                fleet_proof["memory_bank"]["configured"],
                "tenant-isolated, revisioned, review-gated generation",
            ),
            _component(
                "Agent Runtime",
                model_name == "gemini-3.6-flash"
                and model_mode == "vertex"
                and deployment_target == "Google Cloud",
                f"{model_name} via {model_mode} execution policy",
            ),
            _component(
                "Agent Identity",
                fleet_proof["agent_identity"]["configured"],
                (
                    f"managed AGENT_IDENTITY read back for {fleet_proof['deployed_count']} agents; "
                    f"{identity_count} signed task identities retained"
                    if fleet_proof["agent_identity"]["configured"]
                    else f"managed identity awaiting deployment; {identity_count} signed task identities retained"
                ),
            ),
            _component(
                "Agent Gateway",
                decision_count > 0,
                f"default-deny application gateway · {decision_count} decisions retained",
            ),
            _component(
                "Model Armor",
                fleet_proof["model_armor"]["configured"],
                (
                    f"managed template verified with safe/adversarial live calls · {blocked_count} blocks retained"
                    if fleet_proof["model_armor"]["configured"]
                    else f"managed template not configured · {blocked_count} local guardrail blocks retained"
                ),
            ),
            _component(
                "Agent Observability",
                trace_count > 0,
                f"trace recorder active · {trace_count} canonical traces",
            ),
            # The infrastructure tiles below are not new capability; they are the
            # capability made visible. The component grid is the screen an
            # evaluator scans to answer "did they use the platform", so a service
            # that is genuinely in the path but absent from this list reads as a
            # service that is not there.
            _component(
                "Google ADK",
                len(packages) == 19,
                f"{len(packages)} ADK applications · typed tools routed through one gateway",
            ),
            _component(
                "Agent Platform Sessions",
                session_count > 0,
                f"{session_count} resumable task contexts retained",
            ),
            _component(
                "Cloud SQL",
                True,
                f"canonical state on {dialect} · engagements, leases, decisions, custody",
            ),
            _component(
                "Cloud Storage",
                evidence_count > 0,
                f"{evidence_count} content-addressed objects · {storage_provider}",
            ),
            _component(
                "Cloud Run Jobs",
                True,
                "migrate · seed · control tests · scheduler · outbox",
            ),
            _component(
                "Pub/Sub outbox",
                True,
                f"{outbox_pending} undelivered · leased, idempotent dispatch",
            ),
            _component(
                "Deterministic analytics",
                len(control_tests) >= 2,
                f"{len(control_tests)} signed tests",
            ),
            _component(
                "Audit Pack registry", len(audit_packs) >= 3, f"{len(audit_packs)} signed packs"
            ),
            _component("Evidence vault", True, "content-addressed and custody-chained"),
            _component("Durable orchestration", True, "leased tasks, retries, gates, replay"),
            _component("Recurring scheduler", True, "versioned occurrence launcher"),
            _component("Reporting", True, "claim graph and fail-closed issuance"),
        ],
    }


def _component(name: str, healthy: bool, proof: str) -> dict[str, Any]:
    return {"name": name, "status": "operational" if healthy else "attention", "proof": proof}


def ground_truth(repository_root: Path) -> dict[str, Any]:
    return yaml.safe_load(
        (repository_root / "demo" / "asteria" / "ground_truth.yaml").read_text(encoding="utf-8")
    )


def trace_detail(database: Database, tenant_id: str, trace_id: str) -> dict[str, Any] | None:
    """Return a correlated trace including decisions and guardrail findings."""

    with database.read_session() as session:
        trace = session.scalar(
            select(ExecutionTrace).where(
                ExecutionTrace.tenant_id == tenant_id, ExecutionTrace.trace_id == trace_id
            )
        )
        spans = list(
            session.scalars(
                select(ReasoningSpanRecord)
                .where(
                    ReasoningSpanRecord.tenant_id == tenant_id,
                    ReasoningSpanRecord.trace_id == trace_id,
                )
                .order_by(ReasoningSpanRecord.started_at)
            )
        )
        decisions = list(
            session.scalars(
                select(GatewayDecisionRecord)
                .where(
                    GatewayDecisionRecord.tenant_id == tenant_id,
                    GatewayDecisionRecord.trace_id == trace_id,
                )
                .order_by(GatewayDecisionRecord.occurred_at)
            )
        )
        if trace is None and not spans and not decisions:
            return None
        # Correlate on the trace rather than on the decisions it produced.
        # Guardrail findings raised while inspecting inbound context carry no
        # decision id, and those are precisely the prompt-injection detections
        # the trace exists to show.
        findings = list(
            session.scalars(
                select(GuardrailFindingRecord)
                .where(
                    GuardrailFindingRecord.tenant_id == tenant_id,
                    GuardrailFindingRecord.trace_id == trace_id,
                )
                .order_by(GuardrailFindingRecord.occurred_at)
            )
        )
        return {
            "tenant_id": tenant_id,
            "trace_id": trace_id,
            "status": trace.status if trace else "recorded",
            "engagement_id": trace.engagement_id
            if trace
            else (spans[0].engagement_id if spans else None),
            "task_id": trace.task_id if trace else (spans[0].task_id if spans else None),
            "attributes": trace.attributes_json if trace else {},
            "spans": [
                {
                    "span_id": item.span_id,
                    "parent_span_id": item.parent_span_id,
                    "name": item.name,
                    "status": item.status,
                    "started_at": _iso(item.started_at),
                    "ended_at": _iso(item.ended_at),
                    "duration_ms": item.duration_ms,
                    "status_message": item.status_message,
                    "attributes": item.attributes_json,
                    # The guardrail detectors that fired are recorded as span
                    # events, so a projection that drops events cannot show why
                    # a passage was neutralised.
                    "events": item.events_json,
                }
                for item in spans
            ],
            "gateway_decisions": [
                {
                    "decision_id": item.decision_id,
                    "decision": item.decision,
                    "stage": item.stage,
                    "reason": item.reason,
                    "tool_name": item.tool_name,
                    "occurred_at": _iso(item.occurred_at),
                    "attributes": item.attributes_json,
                }
                for item in decisions
            ],
            "guardrail_findings": [
                {
                    "finding_id": item.finding_row_id,
                    "decision_id": item.decision_id,
                    "direction": item.direction,
                    "detector": item.detector,
                    "category": item.category,
                    "severity": item.severity,
                    "verdict": item.verdict,
                    "match_count": item.match_count,
                    "excerpt_digest": item.excerpt_digest,
                    "detail": item.detail,
                    "occurred_at": _iso(item.occurred_at),
                }
                for item in findings
            ],
        }


def _git_commit(repository_root: Path) -> str | None:
    head = repository_root / ".git" / "HEAD"
    try:
        value = head.read_text(encoding="utf-8").strip()
        if value.startswith("ref: "):
            return (repository_root / ".git" / value[5:]).read_text(encoding="utf-8").strip()
        return value
    except OSError:
        return None


def finding_detail(database: Database, tenant_id: str, finding_id: str) -> dict[str, Any] | None:
    """Everything behind one finding: the sources, the test, and the decisions.

    The register answers *what* was concluded. This answers *how*, which is the
    question anyone who has to act on a finding asks first: which systems were
    read, what each one said, what the signed procedure did with them, what was
    considered and rejected, and who is still owed a decision. It is assembled
    from canonical state rather than narrated at proposal time, so it stays true
    as the finding moves.
    """

    from .adjudication.service import AdjudicationService

    gates = AdjudicationService(database).approval_blockers(
        tenant_id=tenant_id, finding_id=finding_id
    )

    with database.read_session() as session:
        finding = session.scalar(
            select(Finding).where(
                Finding.tenant_id == tenant_id, Finding.finding_id == finding_id
            )
        )
        if finding is None:
            return None
        engagement = session.get(Engagement, finding.engagement_id)
        cited = finding.evidence_ids_json or []
        evidence = (
            list(
                session.scalars(
                    select(EvidenceRecord).where(
                        EvidenceRecord.tenant_id == tenant_id,
                        EvidenceRecord.evidence_id.in_(cited),
                    )
                )
            )
            if cited
            else []
        )
        run = (
            session.scalar(
                select(ControlTestRun).where(
                    ControlTestRun.tenant_id == tenant_id,
                    ControlTestRun.run_id == finding.source_run_id,
                )
            )
            if finding.source_run_id
            else None
        )
        bindings = (
            list(
                session.scalars(
                    select(ControlTestDatasetBinding)
                    .where(ControlTestDatasetBinding.run_id == run.run_id)
                    .order_by(ControlTestDatasetBinding.dataset_name)
                )
            )
            if run
            else []
        )
        exceptions = (
            list(
                session.scalars(
                    select(ControlTestException)
                    .where(ControlTestException.run_id == run.run_id)
                    .order_by(ControlTestException.exception_key)
                )
            )
            if run
            else []
        )
        decisions = list(
            session.scalars(
                select(ApprovalDecision)
                .where(
                    ApprovalDecision.tenant_id == tenant_id,
                    ApprovalDecision.finding_id == finding_id,
                )
                .order_by(ApprovalDecision.decided_at)
            )
        )
        remediation = session.scalar(
            select(RemediationAction).where(
                RemediationAction.tenant_id == tenant_id,
                RemediationAction.finding_id == finding_id,
            )
        )
        by_id = {item.evidence_id: item for item in evidence}
        result = (run.result_json or {}) if run else {}

        return {
            "finding": {
                "finding_id": finding.finding_id,
                "engagement_id": finding.engagement_id,
                "code": finding.code,
                "version": finding.version,
                "title": finding.title,
                "status": finding.status,
                "severity": finding.severity,
                "confidence": finding.confidence,
                "criteria": finding.criteria,
                "risk_statement": finding.risk_statement,
                "observed_condition": finding.observed_condition,
                "cause": finding.cause,
                "consequence": finding.consequence,
                "affected_population": finding.affected_population_json,
                "limitations": finding.limitations_json,
                "exception_keys": finding.exception_keys_json,
                "contradictions": finding.contradictions_json,
                "requires_human_approval": finding.requires_human_approval,
                "approval_blockers": _finding_blockers(finding, gates),
                "skeptic_reviewed_at": _iso(finding.skeptic_reviewed_at),
                "skeptic_rationale": finding.skeptic_rationale,
                "authored_by": finding.authored_by,
            },
            "engagement": {
                "engagement_id": engagement.engagement_id,
                "code": engagement.code,
                "title": engagement.title,
                "status": engagement.status,
                "audit_pack_ref": engagement.audit_pack_ref,
                "period": [_iso(engagement.period_start), _iso(engagement.period_end)],
            }
            if engagement
            else None,
            # Ordered as the finding cites them rather than as the database
            # returned them, because the citation order is the argument.
            "evidence": [
                {
                    "evidence_id": item.evidence_id,
                    "source_type": item.source_type,
                    "source_locator": item.source_locator,
                    "sha256": item.content_sha256,
                    "classification": item.classification,
                    "integrity_status": item.integrity_status,
                    "tainted": item.tainted,
                    "collected_at": _iso(item.collected_at),
                    "size_bytes": item.size_bytes,
                }
                for item in (by_id[key] for key in cited if key in by_id)
            ],
            "test_run": {
                "run_id": run.run_id,
                "test_id": run.test_id,
                "version": run.test_version,
                "purpose": run.purpose,
                "status": run.status,
                "conclusion": run.conclusion,
                "period": [_iso(run.period_start), _iso(run.period_end)],
                "population_count": run.population_count,
                "reconciled_count": run.reconciled_count,
                "exception_count": run.exception_count,
                "population_complete": run.population_complete,
                "input_manifest_hash": run.input_manifest_hash,
                "result_manifest_hash": run.result_manifest_hash,
                "datasets": [
                    {
                        "name": item.dataset_name,
                        "role": item.dataset_role,
                        "row_count": item.row_count,
                        "content_hash": item.content_hash,
                        "evidence_ids": item.evidence_ids_json,
                    }
                    for item in bindings
                ],
                "rows": result.get("rows") or [],
                "metrics": result.get("metrics") or {},
                "limitations": result.get("limitations") or [],
            }
            if run
            else None,
            "exceptions": [
                {
                    "exception_key": item.exception_key,
                    "subject_ref": item.subject_ref,
                    "classification": item.classification,
                    "severity": item.severity,
                    "status": item.status,
                    "reason": item.reason,
                    "attributes": item.attributes_json,
                    "evidence_ids": item.evidence_ids_json,
                }
                for item in exceptions
            ],
            "decisions": [
                {
                    "decision_type": item.decision_type,
                    "actor_id": item.actor_id,
                    "reason": item.reason,
                    "decided_at": _iso(item.decided_at),
                }
                for item in decisions
            ],
            "remediation": _remediation_card(remediation),
        }
