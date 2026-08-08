# Privileged access standard

> Asteria Systems DemoCo is a fictional company. Every person, system, transaction, and finding in this corpus is synthetic and exists only for the AssuranceOS hackathon demonstration.

**Owner:** Chief Information Security Officer · **Version:** 3.0 ·
**Effective:** 2026-02-01

## Definition

A privileged role is any role that can deploy to production, read production
data at rest, or modify identity and access configuration. In the
`asteria-prod` project this is `roles/run.admin`, `roles/cloudsql.admin`,
`roles/iam.securityAdmin`, and `roles/owner`.

## Rules

1. Standing privileged access is granted only to named individuals in
   `grp-prod-deploy` and to approved service accounts.
2. A contractor may hold `roles/run.developer`. A contractor may **not** hold
   `roles/run.admin` without a recorded, time-limited exception.
3. A service account holding a privileged role carries a compensating monitor
   and an exception record naming the monitor.
4. Privileged role grants are reviewed quarterly and revoked on termination.
5. Break-glass credentials are sealed, and every use raises a P2 incident.
