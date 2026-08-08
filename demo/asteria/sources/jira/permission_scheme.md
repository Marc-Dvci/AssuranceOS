# Jira permission scheme — Change Management project (CHG)

> Asteria Systems DemoCo is a fictional company. Every person, system, transaction, and finding in this corpus is synthetic and exists only for the AssuranceOS hackathon demonstration.

| Permission | Granted to | Notes |
| --- | --- | --- |
| Create change | `jira-users` | Any engineer may raise a change |
| Transition to *In review* | Reporter, `grp-prod-deploy` | |
| Transition to *Approved* | `change-managers` | Two named holders: `change.manager`, `deputy.change.manager` |
| Transition to *Emergency-Pending-Retrospective* | `grp-prod-deploy` | Permitted during a P1 or P2 incident only |
| Transition from *Emergency-Pending-Retrospective* to *Approved* | `change-managers` | Must occur within five business days |
| Administer project | `jira-administrators` | Two named holders |

A reporter cannot approve their own change. The scheme has not been modified
since 2025-11-04.
