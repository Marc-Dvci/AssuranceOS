# Retest Verification Agent

**Agent ID:** `retest-verification`  
**Version:** `0.7.0`  
**Status:** released and Ed25519 signed

Independently test fresh closure evidence against the original approved finding and retest procedure.

## Release contract

This package contains the immutable manifest, executable system prompt, typed input/output and company-context schemas, declared tools, default-deny policy, qualified model profiles, release evaluations, golden/adversarial/cross-industry cases, and signed release metadata. Runtime loading recalculates every file digest and verifies `release.signature.json` before the agent can be registered.

## Human gates

- `finding_closure_approval`

## Explicit non-goals

- modify the remediated control or source evidence
- rely solely on remediation-owner assertions
- close consequential findings without approval
