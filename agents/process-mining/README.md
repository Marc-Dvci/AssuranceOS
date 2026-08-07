# Process Mining Agent

**Agent ID:** `process-mining`  
**Version:** `0.7.0`  
**Status:** released and Ed25519 signed

Reconstruct observed process flows from approved event data and quantify deviations.

## Release contract

This package contains the immutable manifest, executable system prompt, typed input/output and company-context schemas, declared tools, default-deny policy, qualified model profiles, release evaluations, golden/adversarial/cross-industry cases, and signed release metadata. Runtime loading recalculates every file digest and verifies `release.signature.json` before the agent can be registered.

## Human gates

- `material_process_observation_review`

## Explicit non-goals

- infer intent from sequence alone
- run analytics outside the approved sandbox
- ignore incomplete populations
