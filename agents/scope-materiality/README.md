# Scope and Materiality Agent

**Agent ID:** `scope-materiality`  
**Version:** `0.7.0`  
**Status:** released and Ed25519 signed

Propose risk-based engagement scope, materiality, sampling, assumptions, exclusions, and expected coverage.

## Release contract

This package contains the immutable manifest, executable system prompt, typed input/output and company-context schemas, declared tools, default-deny policy, qualified model profiles, release evaluations, golden/adversarial/cross-industry cases, and signed release metadata. Runtime loading recalculates every file digest and verifies `release.signature.json` before the agent can be registered.

## Human gates

- `engagement_scope_approval`

## Explicit non-goals

- approve engagement scope
- expand scope beyond the execution envelope
- hide data limitations
