# Finding Adjudicator Agent

**Agent ID:** `finding-adjudicator`  
**Version:** `0.7.0`  
**Status:** released and Ed25519 signed

Assemble structured proposed findings and assess evidence sufficiency, severity, and confidence.

## Release contract

This package contains the immutable manifest, executable system prompt, typed input/output and company-context schemas, declared tools, default-deny policy, qualified model profiles, release evaluations, golden/adversarial/cross-industry cases, and signed release metadata. Runtime loading recalculates every file digest and verifies `release.signature.json` before the agent can be registered.

## Human gates

- `finding_approval`

## Explicit non-goals

- approve findings
- omit contradictory evidence
- state unsupported causal conclusions
