# Public Company Intelligence Agent

**Agent ID:** `company-intelligence`  
**Version:** `0.7.0`  
**Status:** released and Ed25519 signed

Create attributable, source-backed organization claim proposals from approved public sources.

## Release contract

This package contains the immutable manifest, executable system prompt, typed input/output and company-context schemas, declared tools, default-deny policy, qualified model profiles, release evaluations, golden/adversarial/cross-industry cases, and signed release metadata. Runtime loading recalculates every file digest and verifies `release.signature.json` before the agent can be registered.

## Human gates

- `organization_profile_confirmation`

## Explicit non-goals

- access non-allowlisted or authenticated sources
- use leaked, personal, or unlawfully obtained material
- treat search snippets as canonical evidence
- infer wrongdoing or protected characteristics
- promote public claims to canonical facts without confirmation
