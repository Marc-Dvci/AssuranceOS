# Privileged Access Audit Pack

**Pack ID:** `privileged-access`
**Version:** `1.0.0`
**Status:** released and Ed25519 signed

Assesses the **design** of privileged-access management: whether standing
privilege is justified, whether elevation is approved by a second party and
expires, and whether privileged activity is recorded outside the control of the
privileged user.

Two things make this pack different from the other two, and both are deliberate.

## It reproduces a licensed standard

`SYN-PAM-BENCH` is declared `entitlement_required: true`. Compiling this pack for
a tenant that holds no entitlement is refused — reproducing licensed criteria text
for an unlicensed tenant is a legal exposure the platform would be the author of,
so it fails closed rather than warning.

The entitlement is read from canonical state at compile time, never from the
request. A licence a caller can assert is not a licence. It is also checked
against its expiry at read time rather than at grant time, so a licence that ran
out stops compiling rather than remaining valid because it was once granted.

Grant one with:

```
POST /api/v1/tenants/{tenant_id}/standard-entitlements
{"standard_code": "SYN-PAM-BENCH", "licence_ref": "..."}
```

## It pins no deterministic test

Design effectiveness is assessed by inspection and walkthrough, not by a
population test. The pack therefore declares no `requires_control_tests` and has
no `control_test` step. A pack that named a test it did not need would make its
evaluation evidence apply to a procedure nobody ran.

The consequence is stated as a quality rule the report must carry: **design
effectiveness does not support an operating-effectiveness conclusion**, and **a
walkthrough of one path is not a population**.

## Limitations

`SYN-PAM-BENCH` is a synthetic benchmark, not a real published standard. It exists
so the licensing path can be exercised end to end without reproducing anyone's
copyrighted criteria text.
