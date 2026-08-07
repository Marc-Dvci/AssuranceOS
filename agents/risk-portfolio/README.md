# Risk and Audit Portfolio Agent

**Agent ID:** `risk-portfolio`  
**Version:** `0.7.0`  
**Status:** released and Ed25519 signed

Translate confirmed organization context into explainable risk and audit-plan recommendations.

## Release contract

This package contains the immutable manifest, executable system prompt, typed input/output and company-context schemas, declared tools, default-deny policy, qualified model profiles, release evaluations, golden/adversarial/cross-industry cases, and signed release metadata. Runtime loading recalculates every file digest and verifies `release.signature.json` before the agent can be registered.

## Human gates

- `audit_plan_approval`

## Explicit non-goals

- conclude control effectiveness from public information
- declare uncited legal obligations
- approve its own plan recommendations
