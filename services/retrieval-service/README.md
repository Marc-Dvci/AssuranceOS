# retrieval-service

**Implementation status:** `extension_contract`
**Current implementation or contract:** `docs/source/assuranceos_hackathon_implementation_plan.md`

The versioned access-aware retrieval boundary is ready for a provider-specific search backend.

This directory is an architectural deployment boundary, not a second copy of the code. The
monolith keeps transaction boundaries explicit for the hackathon; extraction is permitted only
when the same schemas, tenant authorization, audit events, idempotency, and outbox semantics are
preserved. Optional provider extensions use explicit, versioned contracts so the release boundary
remains clear without weakening the production runtime.
