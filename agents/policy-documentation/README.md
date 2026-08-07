# Policy and Documentation Agent

**Agent ID:** `policy-documentation`  
**Version:** `0.7.0`  
**Status:** released and Ed25519 signed

Extract and compare explicit requirements from accepted policy and procedure evidence.

## Release contract

This package contains the immutable manifest, executable system prompt, typed input/output and company-context schemas, declared tools, default-deny policy, qualified model profiles, release evaluations, golden/adversarial/cross-industry cases, and signed release metadata. Runtime loading recalculates every file digest and verifies `release.signature.json` before the agent can be registered.

## Human gates

- `criteria_acceptance`

## Explicit non-goals

- convert ambiguous text into an obligation without review
- treat superseded policy as current
- ignore source taint
