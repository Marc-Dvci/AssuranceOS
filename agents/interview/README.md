# Interview Agent

**Agent ID:** `interview`  
**Version:** `0.7.0`  
**Status:** released and Ed25519 signed

Conduct transparent structured evidence interviews and preserve participant-confirmed assertions.

## Release contract

This package contains the immutable manifest, executable system prompt, typed input/output and company-context schemas, declared tools, default-deny policy, qualified model profiles, release evaluations, golden/adversarial/cross-industry cases, and signed release metadata. Runtime loading recalculates every file digest and verifies `release.signature.json` before the agent can be registered.

## Human gates

- `interview_invitation_approval`
- `sensitive_matter_handoff`

## Explicit non-goals

- evaluate honesty from emotion or linguistic style
- hide AI identity
- handle sensitive employment matters without human routing
- send unapproved email
