# Software Change Management Audit Pack

**Pack ID:** `software-change-management`  
**Version:** `1.0.0`  
**Status:** released and Ed25519 signed

This executable Audit Pack drives the Asteria golden engagement. It defines the audit objective,
synthetic criterion, SCM-01 control expectation, evidence procedure, deterministic population test,
independent skeptic and quality-review steps, consequential human gates, and fail-closed quality
rules.

`release.json` contains the immutable file manifest and package digest.
`release.signature.json` is verified with `security/release-keys/agent-release-public.pem`; the private
release key is not stored in the repository.

The criterion is intentionally Asteria-specific synthetic policy content. Applying this pack to a real
organization requires an approved mapping to that organization's authoritative policy and criteria.
