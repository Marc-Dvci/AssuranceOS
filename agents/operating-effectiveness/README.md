# Operating Effectiveness Agent

**Agent ID:** `operating-effectiveness`  
**Version:** `0.7.0`  
**Status:** released and Ed25519 signed

Evaluate control operation through approved deterministic tests and accepted evidence.

## Release contract

This package contains the immutable manifest, executable system prompt, typed input/output and company-context schemas, declared tools, default-deny policy, qualified model profiles, release evaluations, golden/adversarial/cross-industry cases, and signed release metadata. Runtime loading recalculates every file digest and verifies `release.signature.json` before the agent can be registered.

## Human gates

- `test_result_acceptance`

## Explicit non-goals

- replace failed tests with model judgment
- treat missing evidence as a control failure
- approve final findings
