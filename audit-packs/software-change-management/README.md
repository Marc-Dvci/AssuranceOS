# Software Change Management Audit Pack

**Pack ID:** `software-change-management`
**Version:** `2.0.0`
**Status:** released and Ed25519 signed

The pack that drives the Asteria golden engagement. It declares the audit
objective, the standard and criteria the conclusion is measured against, the
SCM-01 control expectation, and an eleven-step procedure graph that compiles
directly into the engagement's tasks — scoping, evidence capture, the released
deterministic population test, contradiction search, materiality, proposal,
quality review, remediation, independent retest, closure, and report issuance.

Four human gates are declared, and each is attached to the step it authorises:
`engagement_scope_approval`, `finding_approval`, `finding_closure_approval`, and
`report_issuance`. A pack that declares a gate no procedure enforces is refused
at load, so the gate list here is a description of what actually stops.

## Compilation

The pack pins `SCM-01@2.0.0` exactly rather than "at least". A pack validated
against one version of a deterministic test has not been validated against the
next, and the compiler refuses a version drift rather than accepting it.

Compile it with:

```bash
python scripts/run_pack_compiler_demo.py
```

## Release

`release.json` holds the immutable file manifest and package digest.
`release.signature.json` is verified with
`security/release-keys/audit-pack-release-public.pem` — Audit Packs carry their
own release key, separate from the agent-release key, so compromising one review
path does not let anyone publish through the other. The private half is not in
the repository.

## Limitations

The criteria are Asteria-specific synthetic policy content. Applying this pack to
a real organisation requires an approved mapping to that organisation's
authoritative policy, registered through the standards service and crosswalked to
these criteria.
