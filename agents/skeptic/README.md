# Skeptic Agent

**Agent ID:** `skeptic`  
**Version:** `0.7.0`  
**Status:** released and Ed25519 signed

Independently attempt to disprove proposed observations before finding adjudication.

## Release contract

This package contains the immutable manifest, executable system prompt, typed input/output and company-context schemas, declared tools, default-deny policy, qualified model profiles, release evaluations, golden/adversarial/cross-industry cases, and signed release metadata. Runtime loading recalculates every file digest and verifies `release.signature.json` before the agent can be registered.

## Human gates

- `observation_rework_decision`

## Explicit non-goals

- receive the drafting agent's hidden rationale
- alter source evidence
- approve or issue findings
