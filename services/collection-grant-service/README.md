# collection-grant-service

**Implementation status:** `implemented`  
**Current implementation or contract:** `src/assuranceos/connectors/service.py`

Purpose-bound read-only grants, revocation, expiry, scope enforcement, and runs.

This directory is an architectural deployment boundary, not a second copy of the code. The
monolith keeps transaction boundaries explicit for the hackathon; extraction is permitted only
when the same schemas, tenant authorization, audit events, idempotency, and outbox semantics are
preserved. Capabilities marked `contract_defined` are not presented as implemented.
