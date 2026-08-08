# Incident response plan (extract)

> Asteria Systems DemoCo is a fictional company. Every person, system, transaction, and finding in this corpus is synthetic and exists only for the AssuranceOS hackathon demonstration.

**Owner:** Chief Technology Officer · **Version:** 6.1 · **Effective:** 2026-03-01

## Severity

| Severity | Definition | Emergency change permitted |
| --- | --- | --- |
| P1 | Production unavailable or funds at risk | Yes |
| P2 | Material degradation for a subset of customers | Yes |
| P3 | Limited impact with a workaround | No |
| P4 | No customer impact | No |

## Emergency change

1. During a P1 or P2 incident an engineer in `grp-prod-deploy` may merge and
   deploy without prior approval, and must open a change ticket in
   `Emergency-Pending-Retrospective` before the deploy.
2. **Retrospective approval by a change manager is filed within five business
   days of incident resolution.** Until it is filed the change is not approved.
3. A P1 incident requires a published postmortem within ten business days.

## Customer response commitments

The on-call rota and the Jira SLA automation are configured from this table.
Where a customer contract states a shorter target, the contract prevails and
this table is to be updated within ten business days of execution.

| Priority | First response target | Coverage | Contract basis |
| --- | --- | --- | --- |
| P1 | 8 hours | Business hours (08:00-18:00 CET, Mon-Fri) | MSA section 7.2 |
| P2 | 24 hours | Business hours | MSA section 7.2 |
| P3 | 3 business days | Business hours | MSA section 7.2 |

*Last reviewed 2025-11-04 by the Chief Technology Officer.*
