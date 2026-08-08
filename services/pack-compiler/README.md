# pack-compiler

**Implementation status:** `implemented`
**Current implementation or contract:** `src/assuranceos/standards/compiler.py`

Signed Audit Packs compile into compatibility-checked engagement graphs.

This directory is an architectural deployment boundary, not a second copy of the code. The
monolith keeps transaction boundaries explicit for the hackathon; extraction is permitted only
when the same schemas, tenant authorization, audit events, idempotency, and outbox semantics are
preserved. Optional provider extensions use explicit, versioned contracts so the release boundary
remains clear without weakening the production runtime.
