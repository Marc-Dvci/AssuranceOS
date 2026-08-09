"""Security, governance, and telemetry for the AssuranceOS agent fleet.

Four subsystems, composed rather than layered ad hoc:

* :mod:`identity` — zero-trust workload identity. Short-lived Ed25519 credentials
  bound to one tenant, engagement, task, and attempt.
* :mod:`gateway` — the single enforcement point for every agent call. Routing,
  policy, separation of duties, human gates, and budgets.
* :mod:`armor` — inline guardrails against prompt injection, tool poisoning, and
  personal-data or secret leaks.
* :mod:`telemetry` — OpenTelemetry-compliant traces, audit log records, and
  reconstructable reasoning chains.

:mod:`worker` mounts the composed runtime on the durable orchestration task path.
Registering its handler is what makes the enforcement point unavoidable: an agent
task has no other route to execution.

Two further Google models sit beside the reasoning model, each behind the same
transport contract and each deliberately non-authoritative:

* :mod:`embeddings` — EmbeddingGemma. A semantic index over canonical evidence
  that returns candidates for a person to check, never support for a claim.
* :mod:`speech` — Chirp 3. Walkthrough interviews become transcripts, and
  transcripts become assertions to be tested rather than facts to be reported.
"""

from .armor import ArmorFinding, ArmorResult, ModelArmor
from .embeddings import (
    DeterministicEmbeddingClient,
    EmbeddingBatch,
    EmbeddingClient,
    EmbeddingError,
    EvidenceCandidate,
    IndexedDocument,
    LocalEmbeddingClient,
    SemanticEvidenceIndex,
    VertexEmbeddingClient,
    build_embedding_client,
    cosine_similarity,
)
from .gateway import AgentGateway, BoundedTool, GatewayDecision, GatewayDenied, TaskBudget
from .identity import (
    AgentIdentity,
    AgentIdentityError,
    AgentIdentityIssuer,
    AgentIdentityVerifier,
    InMemoryRevocationList,
    SignedAgentIdentity,
    derive_granted_authority,
    generate_agent_identity_keypair,
    workload_uri,
)
from .managed_armor import (
    GoogleManagedModelArmor,
    build_model_armor,
    verify_model_armor_template,
)
from .speech import (
    Chirp3Client,
    ScriptedTranscriptionClient,
    Transcript,
    TranscriptionClient,
    TranscriptionError,
    TranscriptSegment,
    WalkthroughAssertion,
    build_transcription_client,
    extract_assertions,
)
from .telemetry import (
    AgentTracer,
    ReasoningChain,
    RecordedSpan,
    TelemetryConfig,
    audit_log_record,
    configure_telemetry,
    genai_attributes,
    new_span_id,
    new_trace_id,
    summarize_chains,
)
from .worker import (
    GovernedAgentTaskHandler,
    envelope_from_lease,
    evidence_from_records,
)

__all__ = [
    "AgentGateway",
    "AgentIdentity",
    "AgentIdentityError",
    "AgentIdentityIssuer",
    "AgentIdentityVerifier",
    "AgentTracer",
    "ArmorFinding",
    "ArmorResult",
    "BoundedTool",
    "Chirp3Client",
    "DeterministicEmbeddingClient",
    "EmbeddingBatch",
    "EmbeddingClient",
    "EmbeddingError",
    "EvidenceCandidate",
    "GatewayDecision",
    "GatewayDenied",
    "GovernedAgentTaskHandler",
    "GoogleManagedModelArmor",
    "InMemoryRevocationList",
    "IndexedDocument",
    "LocalEmbeddingClient",
    "ModelArmor",
    "ReasoningChain",
    "RecordedSpan",
    "ScriptedTranscriptionClient",
    "SemanticEvidenceIndex",
    "SignedAgentIdentity",
    "TaskBudget",
    "TelemetryConfig",
    "Transcript",
    "TranscriptSegment",
    "TranscriptionClient",
    "TranscriptionError",
    "VertexEmbeddingClient",
    "WalkthroughAssertion",
    "audit_log_record",
    "build_embedding_client",
    "build_model_armor",
    "verify_model_armor_template",
    "build_transcription_client",
    "configure_telemetry",
    "cosine_similarity",
    "derive_granted_authority",
    "envelope_from_lease",
    "evidence_from_records",
    "extract_assertions",
    "genai_attributes",
    "generate_agent_identity_keypair",
    "new_span_id",
    "new_trace_id",
    "summarize_chains",
    "workload_uri",
]
