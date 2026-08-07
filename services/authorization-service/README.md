# authorization-service

**Implementation status:** `implemented`  
**Current implementation or contract:** `src/assuranceos/security.py`

JWT verification, role permissions, tenant scope, and actor attribution.

This directory is an architectural deployment boundary, not a second copy of the code. The
monolith keeps transaction boundaries explicit for the hackathon; extraction is permitted only
when the same schemas, tenant authorization, audit events, idempotency, and outbox semantics are
preserved. Capabilities marked `contract_defined` are not presented as implemented.
