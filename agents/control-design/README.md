# Control Design Agent

**Agent ID:** `control-design`  
**Version:** `0.7.0`  
**Status:** released and Ed25519 signed

Assess whether documented control design is capable of addressing the approved risk.

## Release contract

This package contains the immutable manifest, executable system prompt, typed input/output and company-context schemas, declared tools, default-deny policy, qualified model profiles, release evaluations, golden/adversarial/cross-industry cases, and signed release metadata. Runtime loading recalculates every file digest and verifies `release.signature.json` before the agent can be registered.

## Human gates

- `design_conclusion_review`

## Explicit non-goals

- conclude operating effectiveness
- assume undocumented control operation
- approve final findings
