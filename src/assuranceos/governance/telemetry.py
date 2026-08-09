"""Agent Observability — OpenTelemetry traces and reasoning-chain reconstruction.

An audit platform has to explain how a conclusion was reached, so the reasoning
chain is a canonical record, not a telemetry side effect. That drives the central
design decision here: spans are always recorded in-process, and OpenTelemetry
export is a bridge layered on top. Uninstalling the optional ``otel`` extra costs
you the dashboard, never the audit trail.

Identifiers are W3C Trace Context compliant (128-bit trace id, 64-bit span id,
lowercase hex). The canonical chain and the OpenTelemetry exporter keep separate
id spaces on purpose: OpenTelemetry begins a new trace at every root span, so a
chain with several roots would otherwise fragment across unrelated trace ids.
Every exported span therefore carries ``assuranceos.trace_id`` and
``assuranceos.span_id``, and those attributes are the documented join key between
a Cloud Trace span and the canonical chain. Attribute names otherwise follow the
OpenTelemetry semantic conventions, including the GenAI conventions for model calls.
"""

from __future__ import annotations

import os
import secrets
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterator, Sequence

SCHEMA_URL = "https://opentelemetry.io/schemas/1.27.0"
INSTRUMENTATION_NAME = "assuranceos.governance"

# Span names for the governed agent path. Keeping them constant makes a trace
# comparable across runs and lets Judge Mode assert on chain shape.
SPAN_AGENT_TASK = "assuranceos.agent.task"
SPAN_IDENTITY = "assuranceos.identity.authenticate"
SPAN_POLICY = "assuranceos.gateway.authorize"
SPAN_ARMOR = "assuranceos.armor.inspect"
SPAN_TOOL = "assuranceos.tool.invoke"
SPAN_MODEL = "assuranceos.model.generate"
SPAN_REASONING = "assuranceos.agent.reasoning_step"


def _hex(n_bytes: int) -> str:
    return secrets.token_hex(n_bytes)


def new_trace_id() -> str:
    """128-bit W3C trace id."""
    return _hex(16)


def new_span_id() -> str:
    """64-bit W3C span id."""
    return _hex(8)


@dataclass
class RecordedSpan:
    """One span in the canonical reasoning chain."""

    name: str
    trace_id: str
    span_id: str
    parent_span_id: str | None
    started_at: datetime
    # Creation order within the chain. Wall-clock timestamps collide at sub-
    # microsecond resolution, so ordering by time alone shuffles the steps of a
    # reasoning chain when it is rebuilt from storage.
    sequence: int = 0
    ended_at: datetime | None = None
    status: str = "unset"
    status_message: str = ""
    attributes: dict[str, Any] = field(default_factory=dict)
    events: list[dict[str, Any]] = field(default_factory=list)

    @property
    def duration_ms(self) -> float | None:
        if self.ended_at is None:
            return None
        return (self.ended_at - self.started_at).total_seconds() * 1000.0

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "trace_id": self.trace_id,
            "span_id": self.span_id,
            "parent_span_id": self.parent_span_id,
            "sequence": self.sequence,
            "started_at": self.started_at.isoformat(),
            "ended_at": self.ended_at.isoformat() if self.ended_at else None,
            "duration_ms": self.duration_ms,
            "status": self.status,
            "status_message": self.status_message,
            "attributes": dict(self.attributes),
            "events": list(self.events),
        }

    def traceparent(self) -> str:
        """W3C traceparent header for propagation to downstream services."""
        return f"00-{self.trace_id}-{self.span_id}-01"


@dataclass
class ReasoningChain:
    """The ordered, reconstructable record of one agent task."""

    trace_id: str
    spans: list[RecordedSpan] = field(default_factory=list)

    def add(self, span: RecordedSpan) -> None:
        self.spans.append(span)

    def roots(self) -> list[RecordedSpan]:
        known = {span.span_id for span in self.spans}
        return sorted(
            (s for s in self.spans if s.parent_span_id is None or s.parent_span_id not in known),
            key=lambda s: s.sequence,
        )

    def children_of(self, span_id: str) -> list[RecordedSpan]:
        return sorted(
            (s for s in self.spans if s.parent_span_id == span_id),
            key=lambda s: s.sequence,
        )

    def is_well_formed(self) -> bool:
        """Every span terminates and every non-root parent resolves."""
        if not self.spans:
            return False
        known = {span.span_id for span in self.spans}
        if any(span.ended_at is None for span in self.spans):
            return False
        if len({span.trace_id for span in self.spans}) != 1:
            return False
        non_root = [s for s in self.spans if s.parent_span_id is not None]
        return all(s.parent_span_id in known for s in non_root)

    def denials(self) -> list[RecordedSpan]:
        return [s for s in self.spans if s.attributes.get("assuranceos.decision") == "deny"]

    def as_dict(self) -> dict[str, Any]:
        return {
            "trace_id": self.trace_id,
            "span_count": len(self.spans),
            "well_formed": self.is_well_formed(),
            "denial_count": len(self.denials()),
            "spans": [span.as_dict() for span in self.spans],
        }

    def render(self) -> str:
        """Indented view of the chain, used by demonstrations and Judge Mode."""
        lines: list[str] = []

        def walk(span: RecordedSpan, depth: int) -> None:
            marker = {"ok": "+", "error": "x", "unset": "?"}.get(span.status, "?")
            decision = span.attributes.get("assuranceos.decision")
            suffix = f" [{decision}]" if decision else ""
            duration = f" {span.duration_ms:.1f}ms" if span.duration_ms is not None else ""
            lines.append(f"{'  ' * depth}{marker} {span.name}{suffix}{duration}")
            for child in self.children_of(span.span_id):
                walk(child, depth + 1)

        for root in self.roots():
            walk(root, 0)
        return "\n".join(lines)


def configure_telemetry(config: "TelemetryConfig | None" = None) -> bool:
    """Install a TracerProvider for the process. Call once, from the application.

    Deliberately explicit. Creating a tracer must not reconfigure global state as a
    side effect: OpenTelemetry only honours the first provider set, so a library
    that installs one silently wins the race against the application and against
    any test that needs its own exporter.

    Returns True when a provider was installed by this call.
    """
    config = config or TelemetryConfig()
    try:
        from opentelemetry import trace
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
    except ImportError:
        return False

    if isinstance(trace.get_tracer_provider(), TracerProvider):
        return False  # already configured; do not clobber it

    provider = TracerProvider(resource=Resource.create(config.resource_attributes()))
    endpoint = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT")
    if endpoint:
        try:
            from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
            from opentelemetry.sdk.trace.export import BatchSpanProcessor

            provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint)))
        except ImportError:
            pass
    elif project_id := os.getenv("GOOGLE_CLOUD_PROJECT"):
        try:
            from opentelemetry.exporter.cloud_trace import CloudTraceSpanExporter
            from opentelemetry.sdk.trace.export import BatchSpanProcessor

            provider.add_span_processor(
                BatchSpanProcessor(CloudTraceSpanExporter(project_id=project_id))
            )
        except ImportError:
            pass
    trace.set_tracer_provider(provider)
    return True


class _OtelBridge:
    """Mirrors recorded spans into whatever TracerProvider the application installed."""

    def __init__(self, resource_attributes: dict[str, Any]):  # noqa: ARG002 - kept for symmetry
        self._tracer = None
        try:
            from opentelemetry import trace
        except ImportError:
            return
        self._trace = trace
        self._tracer = trace.get_tracer(INSTRUMENTATION_NAME, schema_url=SCHEMA_URL)

    @property
    def enabled(self) -> bool:
        """True only when a real SDK provider is installed and spans are recorded."""
        if self._tracer is None:
            return False
        try:
            from opentelemetry.sdk.trace import TracerProvider
        except ImportError:
            return False
        return isinstance(self._trace.get_tracer_provider(), TracerProvider)

    @contextmanager
    def span(self, name: str, attributes: dict[str, Any]) -> Iterator[Any]:
        if self._tracer is None:
            yield None
            return
        with self._tracer.start_as_current_span(name) as span:
            for key, value in attributes.items():
                if isinstance(value, (str, bool, int, float)):
                    span.set_attribute(key, value)
            yield span


@dataclass
class TelemetryConfig:
    service_name: str = "assuranceos"
    service_version: str = "0.9.0"
    environment: str = "local"
    cloud_region: str | None = None
    cloud_project: str | None = None

    def resource_attributes(self) -> dict[str, Any]:
        attributes: dict[str, Any] = {
            "service.name": self.service_name,
            "service.version": self.service_version,
            "deployment.environment.name": self.environment,
        }
        if self.cloud_region:
            attributes["cloud.region"] = self.cloud_region
        if self.cloud_project:
            attributes["cloud.account.id"] = self.cloud_project
            attributes["cloud.provider"] = "gcp"
            attributes["cloud.platform"] = "gcp_cloud_run"
        return attributes


class AgentTracer:
    """Records the canonical reasoning chain and mirrors it to OpenTelemetry."""

    def __init__(
        self,
        config: TelemetryConfig | None = None,
        *,
        trace_id: str | None = None,
        bridge_to_otel: bool = True,
    ):
        self.config = config or TelemetryConfig()
        self.chain = ReasoningChain(trace_id=trace_id or new_trace_id())
        self._stack: list[RecordedSpan] = []
        self._bridge = _OtelBridge(self.config.resource_attributes()) if bridge_to_otel else None

    @property
    def trace_id(self) -> str:
        return self.chain.trace_id

    @property
    def otel_enabled(self) -> bool:
        return bool(self._bridge and self._bridge.enabled)

    @property
    def current_span(self) -> RecordedSpan | None:
        return self._stack[-1] if self._stack else None

    @contextmanager
    def span(self, name: str, **attributes: Any) -> Iterator[RecordedSpan]:
        parent = self.current_span
        recorded = RecordedSpan(
            name=name,
            trace_id=self.chain.trace_id,
            span_id=new_span_id(),
            parent_span_id=parent.span_id if parent else None,
            started_at=datetime.now(timezone.utc),
            sequence=len(self.chain.spans),
            attributes={k: v for k, v in attributes.items() if v is not None},
        )
        self.chain.add(recorded)
        self._stack.append(recorded)
        started = time.perf_counter()
        # The canonical ids travel as attributes so an exported span can always be
        # joined back to the chain that produced it.
        recorded.attributes.setdefault("assuranceos.trace_id", recorded.trace_id)
        recorded.attributes.setdefault("assuranceos.span_id", recorded.span_id)
        if recorded.parent_span_id:
            recorded.attributes.setdefault("assuranceos.parent_span_id", recorded.parent_span_id)
        bridge_ctx = (
            self._bridge.span(name, recorded.attributes)
            if self._bridge
            else _null_context()
        )
        try:
            with bridge_ctx as otel_span:
                self._record_exported_ids(recorded, otel_span)
                yield recorded
        except Exception as exc:
            recorded.status = "error"
            recorded.status_message = f"{type(exc).__name__}: {exc}"
            raise
        else:
            if recorded.status == "unset":
                recorded.status = "ok"
        finally:
            recorded.ended_at = datetime.now(timezone.utc)
            recorded.attributes.setdefault(
                "assuranceos.duration_ms", round((time.perf_counter() - started) * 1000, 3)
            )
            self._stack.pop()

    @staticmethod
    def _record_exported_ids(recorded: RecordedSpan, otel_span: Any) -> None:
        """Record the exporter's own identifiers alongside the canonical ones.

        The two id spaces are deliberately kept separate. OpenTelemetry starts a
        new trace for every root span, so adopting its ids would fragment a chain
        that legitimately has several roots. Instead each exported span carries
        ``assuranceos.trace_id``, and that attribute is the documented join key
        between a Cloud Trace span and the canonical reasoning chain.
        """
        if otel_span is None:
            return
        getter = getattr(otel_span, "get_span_context", None)
        if getter is None:
            return
        span_context = getter()
        if not getattr(span_context, "is_valid", False):
            return
        recorded.attributes["otel.trace_id"] = format(span_context.trace_id, "032x")
        recorded.attributes["otel.span_id"] = format(span_context.span_id, "016x")

    def event(self, name: str, **attributes: Any) -> None:
        span = self.current_span
        if span is None:
            return
        span.events.append(
            {
                "name": name,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "attributes": {k: v for k, v in attributes.items() if v is not None},
            }
        )

    def deny(self, reason: str) -> None:
        span = self.current_span
        if span is None:
            return
        span.attributes["assuranceos.decision"] = "deny"
        span.attributes["assuranceos.denial_reason"] = reason
        span.status = "error"
        span.status_message = reason

    def allow(self) -> None:
        span = self.current_span
        if span is not None:
            span.attributes["assuranceos.decision"] = "allow"


@contextmanager
def _null_context() -> Iterator[None]:
    yield None


def genai_attributes(
    *,
    model: str,
    operation: str = "generate_content",
    system: str = "gcp.gemini",
    input_tokens: int | None = None,
    output_tokens: int | None = None,
    temperature: float | None = None,
) -> dict[str, Any]:
    """GenAI semantic-convention attributes for a model call span."""
    attributes: dict[str, Any] = {
        "gen_ai.system": system,
        "gen_ai.operation.name": operation,
        "gen_ai.request.model": model,
    }
    if input_tokens is not None:
        attributes["gen_ai.usage.input_tokens"] = input_tokens
    if output_tokens is not None:
        attributes["gen_ai.usage.output_tokens"] = output_tokens
    if temperature is not None:
        attributes["gen_ai.request.temperature"] = temperature
    return attributes


def audit_log_record(
    *,
    trace_id: str,
    span_id: str,
    tenant_id: str,
    actor: str,
    action: str,
    outcome: str,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """An OpenTelemetry-shaped log record correlated to the emitting span.

    Trace and span ids are carried explicitly so an exported log and the canonical
    audit event resolve to the same trace without relying on ambient context.
    """
    record = {
        "Timestamp": datetime.now(timezone.utc).isoformat(),
        "TraceId": trace_id,
        "SpanId": span_id,
        "SeverityText": "ERROR" if outcome == "deny" else "INFO",
        "SeverityNumber": 17 if outcome == "deny" else 9,
        "Body": f"{action} {outcome}",
        "Attributes": {
            "enduser.id": actor,
            "assuranceos.tenant_id": tenant_id,
            "assuranceos.action": action,
            "assuranceos.outcome": outcome,
            **(extra or {}),
        },
    }
    return record


def summarize_chains(chains: Sequence[ReasoningChain]) -> dict[str, Any]:
    return {
        "chain_count": len(chains),
        "span_count": sum(len(chain.spans) for chain in chains),
        "denial_count": sum(len(chain.denials()) for chain in chains),
        "all_well_formed": all(chain.is_well_formed() for chain in chains),
    }
