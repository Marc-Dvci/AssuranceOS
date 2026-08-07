# Continuous Monitoring Agent

**Agent ID:** `continuous-monitoring`  
**Version:** `0.7.0`  
**Status:** released and Ed25519 signed

Run approved recurring tests, detect drift, and open review cases without converting alerts into findings.

## Release contract

This package contains the immutable manifest, executable system prompt, typed input/output and company-context schemas, declared tools, default-deny policy, qualified model profiles, release evaluations, golden/adversarial/cross-industry cases, and signed release metadata. Runtime loading recalculates every file digest and verifies `release.signature.json` before the agent can be registered.

## Human gates

- `monitor_version_change_approval`

## Explicit non-goals

- convert an alert directly into an approved finding
- conclude when source freshness or completeness is below threshold
- alter thresholds or escalation policy without release approval
