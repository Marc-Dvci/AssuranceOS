# Segregation of duties matrix

> Asteria Systems DemoCo is a fictional company. Every person, system, transaction, and finding in this corpus is synthetic and exists only for the AssuranceOS hackathon demonstration.

**Owner:** Head of Risk · **Version:** 2.3 · **Effective:** 2026-01-01

| Activity | May be performed by | May **not** also perform |
| --- | --- | --- |
| Raise a change | Any engineer | Approve the same change |
| Approve a change | `change-managers` | Author the same change |
| Deploy to production | `grp-prod-deploy`, `svc-deploy` | Approve the change being deployed |
| Grant a privileged role | `grp-security-admins` | Review that grant in a campaign |
| Prepare a payment run | Accounts payable | Approve the same payment run |
| Approve a payment run | Finance manager, CFO | Prepare the same run |
| Remediate an audit finding | Control owner | Retest the same finding |
| Retest an audit finding | Independent reviewer | Have performed the remediation |
