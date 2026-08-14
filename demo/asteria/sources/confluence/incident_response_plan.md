# Incident response plan

> Asteria Systems DemoCo is a fictional company. Every person, system, transaction, and finding in this corpus is synthetic and exists only for the AssuranceOS hackathon demonstration.

**Owner:** Chief Technology Officer · **Version:** 6.1 · **Effective:** 2026-03-01
**Review cycle:** annual · **Classification:** internal
**Applies to:** all production services listed in the service criticality register

## 1. Purpose and scope

This plan describes how Asteria detects, triages, communicates, resolves and
learns from incidents affecting production services. It applies to every
engineer on the on-call rota, to the service delivery team, and to anyone who
declares an incident.

It does not cover security incidents involving personal data, which follow the
data breach procedure held by the Data Protection Officer, or physical security
events, which follow the facilities procedure. A single event may trigger more
than one procedure.

## 2. Roles

| Role | Held by | Responsibility |
| --- | --- | --- |
| Incident commander | Primary on-call engineer | Owns the incident until stood down; makes the call on severity and on emergency change |
| Communications lead | Service delivery manager | Owns all customer-facing updates and the status page |
| Subject matter expert | Rota per service | Investigates; may not also be incident commander for a P1 |
| Executive sponsor | Chief Technology Officer | Engaged for any P1 lasting more than four hours |

The incident commander role is deliberately separated from the investigating
engineer for a P1 so that no single person is both diagnosing and deciding.

## Severity

| Severity | Definition | Emergency change permitted |
| --- | --- | --- |
| P1 | Production unavailable or funds at risk | Yes |
| P2 | Material degradation for a subset of customers | Yes |
| P3 | Limited impact with a workaround | No |
| P4 | No customer impact | No |

Severity is assessed at declaration and reassessed at each update. A severity
may be raised at any time; it may only be lowered by the incident commander,
who must record the reason on the ticket.

## 3. Declaration and triage

1. Any employee may declare an incident in the `#incidents` channel or by
   raising a ticket of type Incident in the service management system.
2. The on-call engineer acknowledges within 15 minutes and assigns a severity.
3. For a P1, the incident commander opens a bridge, pages the communications
   lead, and posts the first customer update within the applicable first
   response target.
4. Every incident carries a single ticket. Work performed under a different
   ticket is not evidence of the incident being handled.

## 4. Escalation

| Elapsed | P1 | P2 |
| --- | --- | --- |
| 30 minutes | Engineering manager notified | — |
| 2 hours | Head of Engineering notified | Engineering manager notified |
| 4 hours | Chief Technology Officer engaged as executive sponsor | Head of Engineering notified |
| 8 hours | Chief Executive Officer briefed; customer executive contact called | Chief Technology Officer engaged |

## Emergency change

1. During a P1 or P2 incident an engineer in `grp-prod-deploy` may merge and
   deploy without prior approval, and must open a change ticket in
   `Emergency-Pending-Retrospective` before the deploy.
2. **Retrospective approval by a change manager is filed within five business
   days of incident resolution.** Until it is filed the change is not approved.
3. A P1 incident requires a published postmortem within ten business days.
4. The emergency path may not be used to deploy a change that was already in
   the normal queue awaiting approval. Doing so is a policy breach whether or
   not the change itself was sound.
5. Use of the emergency path is reported to the Change Advisory Board at its
   next meeting, with the count of retrospective approvals still outstanding.

## Customer response commitments

The on-call rota and the Jira SLA automation are configured from this table.
Where a customer contract states a shorter target, the contract prevails and
this table is to be updated within ten business days of execution.

| Priority | First response target | Coverage | Contract basis |
| --- | --- | --- | --- |
| P1 | 8 hours | Business hours (08:00-18:00 CET, Mon-Fri) | MSA section 7.2 |
| P2 | 24 hours | Business hours | MSA section 7.2 |
| P3 | 3 business days | Business hours | MSA section 7.2 |

A first response means a human-authored update naming the affected component
and the engineer assigned. An automated acknowledgement from the service
management system does not satisfy the commitment, and the timestamp recorded
against `first_response_at` must be the human update.

### Customers with non-standard commitments

None recorded. Where a customer negotiates a shorter target, the service
delivery manager raises a change to this page and to the SLA automation, and
records the change in the contract commitment register.

## 5. Communications

1. The communications lead owns the status page. Engineers do not post to it.
2. A P1 receives a customer update at declaration and then at least every 60
   minutes until resolved.
3. No root cause is stated to a customer before the postmortem is approved.

## 6. Resolution and closure

1. An incident is resolved when customer impact has ended, not when the
   underlying defect is fixed.
2. Closure requires: a resolution timestamp, a severity that has been reviewed,
   any emergency change linked, and for a P1 a published postmortem.
3. Remediation actions arising from a postmortem are raised as tickets with a
   named owner and a due date. They are tracked to closure by the engineering
   manager, not by the incident process.

## 7. Postmortems

A P1 postmortem is blameless and covers: timeline, detection, contributing
factors, what went well, what did not, and the actions arising. It is published
to the whole engineering organisation within ten business days.

## 8. Testing this plan

The plan is exercised at least twice a year through a simulated P1. The
exercise report is retained for three years and reviewed by the Audit
Committee.

## Related documents

- `confluence/change_management_policy.md`
- `confluence/exception_management_procedure.md`
- `jira/sla_configuration.json` — the automation configured from this page
- `legal/contract_commitment_register.csv` — the contractual position of record

*Last reviewed 2025-11-04 by the Chief Technology Officer.*
*Next review due 2026-11-04.*
