# Evidence Custodian Agent

**Agent ID:** `evidence-custodian`  
**Version:** `0.7.0`  
**Status:** released and Ed25519 signed

Collect and preserve source evidence with provenance, classification, hashes, and custody metadata.

## Release contract

This package contains the immutable manifest, executable system prompt, typed input/output and company-context schemas, declared tools, default-deny policy, qualified model profiles, release evaluations, golden/adversarial/cross-industry cases, and signed release metadata. Runtime loading recalculates every file digest and verifies `release.signature.json` before the agent can be registered.

## Human gates

- `sensitive_evidence_access`

## Explicit non-goals

- determine control effectiveness
- modify source data
- collect outside an approved collection grant
