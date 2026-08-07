# Identity and Access Audit Pack

**Pack ID:** `identity-access`
**Version:** `1.0.0`
**Status:** released and Ed25519 signed

Evaluates whether workforce identities lose access within the approved deadline
after termination, and whether deviations are covered by an exception that was
active at the time.

Ten procedures, pinning `IAM-01@1.0.0`. Three human gates:
`engagement_scope_approval`, `finding_approval`, and `report_issuance`.

The pack exists alongside `software-change-management` to demonstrate that the
engagement graph follows the pack rather than a template the platform holds: the
two compile to different task keys, different gates, and different pins from the
same compiler and the same organisation context.

## Crosswalk

`AST-POL-IAM-01` is registered as a **subset** of the broader "remove access when
it is no longer required" requirement: the standard fixes no deadline, so the
policy is the narrower obligation. The rationale and the asserting party are
recorded with the edge, because an assurance map assembled from unattributed
equivalences is a map of what somebody assumed.

## Limitations

Directory state is evidence of account status only. Application sessions
established before disablement are outside this procedure and need separate
connector coverage; the pack states this as a quality rule so the limitation
reaches the report rather than being discovered by a reader.
