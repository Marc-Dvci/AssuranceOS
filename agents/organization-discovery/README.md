# Organization Discovery Agent

**Agent ID:** `organization-discovery`  
**Version:** `0.7.0`  
**Status:** released and Ed25519 signed

Build evidence-linked maps of entities, systems, processes, owners, and policies.

## Release contract

This package contains the immutable manifest, executable system prompt, typed input/output and company-context schemas, declared tools, default-deny policy, qualified model profiles, release evaluations, golden/adversarial/cross-industry cases, and signed release metadata. Runtime loading recalculates every file digest and verifies `release.signature.json` before the agent can be registered.

## Human gates

- `canonical_entity_merge_approval`

## Explicit non-goals

- invent missing owners
- merge entities without evidence
- access sources outside approved grants
