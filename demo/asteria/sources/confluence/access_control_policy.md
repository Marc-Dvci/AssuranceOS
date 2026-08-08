# Access control policy

> Asteria Systems DemoCo is a fictional company. Every person, system, transaction, and finding in this corpus is synthetic and exists only for the AssuranceOS hackathon demonstration.

**Owner:** Chief Information Security Officer · **Version:** 4.2 ·
**Effective:** 2026-01-01 · **Review cycle:** annual

## Scope

This policy applies to every identity that can reach an Asteria production
system, including employees, contractors, and non-human service accounts.

## Provisioning

1. Access is granted on the basis of a documented role. Standing access is
   granted only where just-in-time elevation is not technically available.
2. Contractor identities are provisioned from the contractor register and carry
   an engagement end date.

## Deprovisioning

3. **A workforce identity is disabled within 24 hours of the effective
   termination time.** This applies identically to employees and contractors.
4. Where an account must be retained after termination, a time-limited exception
   is recorded in the exception register with a compensating control before the
   deadline passes.
5. Privileged role assignments are revoked at the same time as the account is
   disabled, and revocation is evidenced separately from account status.

## Authentication

6. Every active identity enrols multi-factor authentication. Phishing-resistant
   methods are required for identities holding production privileged roles.

## Review

7. Production privileged roles are reviewed **quarterly** under the access
   review procedure. The review is completed, not merely opened, within the
   quarter.
