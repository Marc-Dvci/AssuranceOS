# local-model-gateway

**Implementation status:** `contract_defined`  
**Current implementation or contract:** `edge/local-runtime/README.md`

Loopback-only local model enforcement remains a distinct deployment profile.

This directory is an architectural deployment boundary, not a second copy of the code. The
monolith keeps transaction boundaries explicit for the hackathon; extraction is permitted only
when the same schemas, tenant authorization, audit events, idempotency, and outbox semantics are
preserved. Capabilities marked `contract_defined` are not presented as implemented.
