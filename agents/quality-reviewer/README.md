# Quality Reviewer Agent

**Agent ID:** `quality-reviewer`  
**Version:** `0.7.0`  
**Status:** released and Ed25519 signed

Independently review methodology compliance, evidence support, severity consistency, and required workpapers.

## Release contract

This package contains the immutable manifest, executable system prompt, typed input/output and company-context schemas, declared tools, default-deny policy, qualified model profiles, release evaluations, golden/adversarial/cross-industry cases, and signed release metadata. Runtime loading recalculates every file digest and verifies `release.signature.json` before the agent can be registered.

## Human gates

- `quality_review_acceptance`

## Explicit non-goals

- share the execution identity of engagement agents
- approve its own work
- silently waive missing methodology steps
