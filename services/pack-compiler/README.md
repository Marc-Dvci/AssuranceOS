# pack-compiler

**Implementation status:** `contract_defined`  
**Current implementation or contract:** `audit-packs/schemas/audit_pack.schema.json`

Audit Pack schema exists; general graph compilation beyond the SCM pack is tracked.

This directory is an architectural deployment boundary, not a second copy of the code. The
monolith keeps transaction boundaries explicit for the hackathon; extraction is permitted only
when the same schemas, tenant authorization, audit events, idempotency, and outbox semantics are
preserved. Capabilities marked `contract_defined` are not presented as implemented.
