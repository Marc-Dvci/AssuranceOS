# Starter threat model

## Protected assets

Tenant boundaries, connector credentials, immutable evidence, canonical facts, approval records, prompts, model policies, agent identities, deterministic test results, and report claims.

## Initial threats and controls

| Threat | Starter control |
|---|---|
| Prompt injection in connected evidence | Evidence is data, not instruction; source is tainted; prohibited tool calls are denied; attack is logged. |
| Cross-tenant access | Every envelope and event carries tenant identity; policy defaults deny. |
| Scope expansion | The control plane signs lease-bound execution envelopes; the ADK gateway verifies issuer, expiry, task binding, package prohibitions, evidence scopes, and tools before execution. |
| Duplicate external action | Side-effecting tools require idempotency keys. |
| Unsupported finding | Structured claims require accepted evidence and human finding approval. |
| Secret exposure | No credential-read tool is declared; credentials remain outside model context. |
| Technical failure misclassified as control result | Taxonomy preserves technical failure and insufficient evidence as separate outcomes. |

## Deferred validation

Authenticated identity federation, production Agent Gateway integration, Model Armor APIs, Secret Manager bindings, DLP/redaction, VPC Service Controls, Cloud SQL IAM authentication, and penetration testing.
