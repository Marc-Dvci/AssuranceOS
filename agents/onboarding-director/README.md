# Onboarding Director Agent

**Agent ID:** `onboarding-director`  
**Version:** `0.7.0`  
**Status:** released and Ed25519 signed

Coordinate the durable organization onboarding workflow and readiness gates.

## Release contract

This package contains the immutable manifest, executable system prompt, typed input/output and company-context schemas, declared tools, default-deny policy, qualified model profiles, release evaluations, golden/adversarial/cross-industry cases, and signed release metadata. Runtime loading recalculates every file digest and verifies `release.signature.json` before the agent can be registered.

## Human gates

- `profile_confirmation`
- `connector_authorization`
- `audit_plan_approval`

## Explicit non-goals

- silently accept inferred company facts
- grant connector permissions
- approve the audit plan
- mark setup ready while a mandatory gate is blocked
