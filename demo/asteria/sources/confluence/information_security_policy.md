# Information security policy

> Asteria Systems DemoCo is a fictional company. Every person, system, transaction, and finding in this corpus is synthetic and exists only for the AssuranceOS hackathon demonstration.

**Owner:** Chief Information Security Officer · **Version:** 7.0 ·
**Effective:** 2026-01-01 · **Supersedes:** version 6.3 (2025-01-06)
**Approved by:** the Board, 2025-12-11 · **Review cycle:** annual
**Classification:** internal · **Applies to:** all employees, contractors and
third parties with access to Asteria systems or data

## 1. Purpose

This policy sets out the security requirements that apply across Asteria. It is
the parent document for the standards and procedures listed in section 12, each
of which implements part of it. Where a subordinate document conflicts with
this policy, this policy prevails.

## 2. Framework and commitments

Asteria commits contractually to ISO/IEC 27001 Annex A controls and reports
annually under SOC 2 Trust Services Criteria. Customer commitments reference
NIST CSF as a mapping framework only; Asteria does not certify against NIST CSF
and no customer commitment should be read as a certification.

The scope of the information security management system is the Asteria
platform, its supporting corporate systems, and the personnel who operate them.
The statement of applicability is maintained by the CISO.

## 3. Governance

3.1 The Board owns information security risk. The CISO is accountable for the
management system and reports to the Audit Committee quarterly.

3.2 A security exception may be granted only under
`confluence/exception_management_procedure.md`. Every exception has a named
risk owner, a stated compensating control, and an expiry date. An exception
without an expiry date is not an exception; it is an undocumented risk
acceptance.

3.3 Policy violations are handled under the disciplinary procedure. Reporting a
suspected violation in good faith never attracts a sanction.

## 4. Data classification and handling

| Class | Examples | Storage | Sharing |
| --- | --- | --- | --- |
| Restricted | Customer payment instructions, credentials, personal data | Encrypted, EEA only, access logged | Named individuals only, under contract |
| Confidential | Contracts, financial reports, source code | Encrypted at rest | Internal, need to know |
| Internal | Policies, architecture notes, meeting minutes | Standard | All staff |
| Public | Marketing material, trust centre content | Standard | Unrestricted |

4.1 Production data is stored in `europe-west1`. Transfer outside the EEA
requires a documented transfer basis maintained by the Data Protection Officer.

4.2 Restricted data may not be copied to a local device, a personal cloud
account, or a non-production environment. Where a production-like dataset is
needed for testing, it is generated or masked.

4.3 Retention periods are held in the records retention schedule. Data is
deleted at the end of its period unless a legal hold applies.

## 5. Access control

5.1 Access is granted on the principle of least privilege and only through a
group, never to an individual account directly.

5.2 Multi-factor authentication is enforced for all access to production and
for all administrative access to corporate systems. See
`confluence/access_control_policy.md`.

5.3 Privileged roles are reviewed quarterly. The review is evidenced by a
completed campaign in the identity platform; an abandoned or unstarted campaign
is a control failure, not a delay.

5.4 Access for a leaver is removed within one business day of their
termination date. This applies to contractors as it applies to employees. See
`confluence/offboarding_checklist.md`.

5.5 Service accounts have a named human owner, a documented purpose, and
credentials rotated at least annually.

## 6. Change and configuration management

6.1 Every production system carries a named business owner and technical owner
in the service criticality register.

6.2 Security-relevant configuration is managed as code and reviewed under
`confluence/change_management_policy.md`. Manual changes to production
configuration are permitted only under the emergency path in the incident
response plan and require retrospective approval.

6.3 Branch protection is enabled on every repository that deploys to
production, requiring at least one approving review from a person other than
the author.

## 7. Logging and monitoring

7.1 Logs of administrative activity are retained for 400 days and are not
modifiable by the principals they record.

7.2 Authentication events, privilege changes, and access to restricted data are
logged centrally. Log integrity is protected by write-once storage.

7.3 Alerts for privileged role assignment, failed administrative
authentication, and egress of restricted data are routed to the on-call rota.

## 8. Cryptography

8.1 Data in transit is protected with TLS 1.2 or above. Data at rest is
encrypted with AES-256 or an equivalent.

8.2 Key material is held in the managed key service. Keys are rotated annually
and on any suspected compromise. No key is stored in source control, in a
container image, or in an environment variable committed to a repository.

## 9. Supplier and third-party security

9.1 A supplier with access to restricted data is assessed before engagement and
reassessed annually. The assessment covers their own certification status,
sub-processors, breach history, and exit arrangements.

9.2 Sub-processors are published in the trust centre. A customer may object to
an addition within 30 days.

## 10. Vulnerability and patch management

| Severity | Remediate within | Applies to |
| --- | --- | --- |
| Critical | 7 days | Internet-facing and production |
| High | 30 days | Production |
| Medium | 90 days | All systems |
| Low | Next scheduled maintenance | All systems |

10.1 An independent penetration test is commissioned annually. Findings are
tracked to closure and reported to the Audit Committee.

10.2 Dependencies are scanned on every build. A build with an unremediated
critical vulnerability does not deploy to production.

## 11. Business continuity

11.1 Recovery time objective is four hours; recovery point objective is 15
minutes, for services classified critical in the service criticality register.

11.2 Restoration from backup is tested at least annually and the test result
retained.

## 12. Subordinate documents

- `confluence/access_control_policy.md`
- `confluence/privileged_access_standard.md`
- `confluence/access_review_procedure.md`
- `confluence/change_management_policy.md`
- `confluence/exception_management_procedure.md`
- `confluence/offboarding_checklist.md`
- `confluence/segregation_of_duties_matrix.md`
- `confluence/incident_response_plan.md`

## 13. Review

This policy is reviewed annually by the CISO and approved by the Board. The
next review is due 2026-12-11.

| Version | Date | Change | Approved by |
| --- | --- | --- | --- |
| 7.0 | 2026-01-01 | Added supplier assessment cadence; aligned retention to 400 days | Board |
| 6.3 | 2025-01-06 | Added MFA requirement for corporate administrative access | Board |
| 6.2 | 2024-04-30 | Reclassified customer payment instructions as Restricted | CISO |
| 6.1 | 2023-11-14 | Initial ISO/IEC 27001 alignment | Board |
