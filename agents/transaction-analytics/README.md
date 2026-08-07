# Transaction Analytics Agent

**Agent ID:** `transaction-analytics`  
**Version:** `0.7.0`  
**Status:** released and Ed25519 signed

Select and run approved SQL, Python, or graph analyses in a bounded reproducible sandbox.

## Release contract

This package contains the immutable manifest, executable system prompt, typed input/output and company-context schemas, declared tools, default-deny policy, qualified model profiles, release evaluations, golden/adversarial/cross-industry cases, and signed release metadata. Runtime loading recalculates every file digest and verifies `release.signature.json` before the agent can be registered.

## Human gates

- `generated_code_approval`

## Explicit non-goals

- execute unreviewed generated code
- use unrestricted network egress
- write to source systems
