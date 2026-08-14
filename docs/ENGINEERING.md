# AssuranceOS engineering guide

The implementation is organized as a modular monolith under src/assuranceos.
Domain packages own their models, services, repositories, and typed definitions;
src/assuranceos/api.py is the authenticated HTTP composition layer.

## Invariants

- Model output never grants authority or becomes canonical evidence directly.
- Agent releases, control tests, Audit Packs, execution envelopes, and exports
  are signed and independently verified.
- Every tenant read and write is scoped before repository access.
- Every consequential transition records its actor and emits an outbox event in
  the same transaction.
- Findings require a human decision; closure requires an independent retest.
- Reports do not render a material unsupported claim.
- Memory Bank receives only approved sessions under a tenant-qualified subject.

## Runtime paths

- Gemini 3.7 Flash through the Google GenAI SDK.
- Google ADK applications deployed to Vertex AI Agent Engine.
- VertexAiMemoryBankService for managed long-term context.
- OpenAI-compatible loopback transport for the local privacy profile.
- Scripted transport only for deterministic test fixtures.

## Verification

Use docs/runbooks/release-checklist.md as the canonical release checklist. Architecture
documents under docs/architecture describe the data model, orchestration,
evidence, standards, governance, review gates, and reporting claim graph.
