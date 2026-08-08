# Code owners — Asteria Systems DemoCo

> Asteria Systems DemoCo is a fictional company. Every person, system, transaction, and finding in this corpus is synthetic and exists only for the AssuranceOS hackathon demonstration.

Ownership drives the required-reviewer rule in branch protection. A pull request
cannot satisfy SCM-01 with an approval from its own author.

| Path | Owning team | Required reviewers |
| --- | --- | --- |
| `asteria/payments-api` | Payments | 1 from `@asteria/payments-reviewers` |
| `asteria/identity-service` | Identity | 1 from `@asteria/identity-reviewers` |
| `asteria/ledger-core` | Ledger | 1 from `@asteria/ledger-reviewers` |
| `asteria/invoice-ingest` | Ingest | 1 from `@asteria/ingest-reviewers` |
| `asteria/reporting-ui` | Product engineering | 1 from `@asteria/frontend-reviewers` |
| `asteria/ops-automation` | Platform | Automated maintenance may merge under EXC-SVC-001 |

`svc-release-bot` is listed in the bypass allowances of `asteria/ops-automation`
only. The exception is time-limited and recorded in the exception register.
