# Remediation Coordinator Agent

**Agent ID:** `remediation-coordinator`  
**Version:** `0.7.0`  
**Status:** released and Ed25519 signed

Coordinate approved remediation obligations and idempotent external actions without assuming management ownership.

## Release contract

This package contains the immutable manifest, executable system prompt, typed input/output and company-context schemas, declared tools, default-deny policy, qualified model profiles, release evaluations, golden/adversarial/cross-industry cases, and signed release metadata. Runtime loading recalculates every file digest and verifies `release.signature.json` before the agent can be registered.

## Human gates

- `external_action_approval`
- `risk_acceptance_approval`

## Explicit non-goals

- approve its own remediation design
- close a finding
- perform independent retest
- write to high-impact systems without confirmation
