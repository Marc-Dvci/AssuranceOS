# Engagement Director Agent

**Agent ID:** `engagement-director`  
**Version:** `0.7.0`  
**Status:** released and Ed25519 signed

Supervise the approved engagement graph without bypassing methodology or human gates.

## Release contract

This package contains the immutable manifest, executable system prompt, typed input/output and company-context schemas, declared tools, default-deny policy, qualified model profiles, release evaluations, golden/adversarial/cross-industry cases, and signed release metadata. Runtime loading recalculates every file digest and verifies `release.signature.json` before the agent can be registered.

## Human gates

- `scope_approval`
- `finding_approval`
- `report_issuance`

## Explicit non-goals

- approve final findings
- access sources outside engagement scope
- write to customer systems except approved collaboration actions
