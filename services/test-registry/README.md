# test-registry

**Implementation status:** `implemented`

The deployment boundary is implemented by `src/assuranceos/control_testing/registry.py`, the signed
packages under `tests-library/`, and the canonical `control_test_releases` table. Release identity is
immutable by `(test_id, version)` and a changed package hash is rejected rather than overwritten.

The current monolith preserves the same schemas, tenant authorization, audit events, idempotency,
and outbox semantics that an extracted service must retain.
