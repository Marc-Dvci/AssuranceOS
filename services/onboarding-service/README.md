# onboarding-service

**Implementation status:** `contract_defined`  
**Current implementation or contract:** `docs/source/assuranceos_hackathon_implementation_plan.md`

Durable onboarding state machine remains in the retained full-scope roadmap.

This directory is an architectural deployment boundary, not a second copy of the code. The
monolith keeps transaction boundaries explicit for the hackathon; extraction is permitted only
when the same schemas, tenant authorization, audit events, idempotency, and outbox semantics are
preserved. Capabilities marked `contract_defined` are not presented as implemented.
