# Implementation status and scope preservation

The plan remains the scope authority. This release does not delete or silently downgrade capabilities
that have not yet been implemented. Every repository boundary is classified as either executable,
contract-defined, or dependent on external validation in
[`capability-status.yaml`](capability-status.yaml).

A `contract_defined` boundary is deliberately not shown as working product functionality. It records
the schemas, security invariants, and release criteria needed for the later implementation while
preventing empty directories from being mistaken for completed services.
