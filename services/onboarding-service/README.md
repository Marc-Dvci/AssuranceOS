# onboarding-service

**Implementation status:** `implemented`
**Current implementation or contract:** `src/assuranceos/onboarding.py`

Durable onboarding captures, reviews, corrects, and approves source-backed facts.

This directory is an architectural deployment boundary, not a second copy of the code. The
monolith keeps transaction boundaries explicit for the hackathon; extraction is permitted only
when the same schemas, tenant authorization, audit events, idempotency, and outbox semantics are
preserved. Optional provider extensions use explicit, versioned contracts so the release boundary
remains clear without weakening the production runtime.
