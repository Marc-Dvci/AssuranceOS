# AssuranceOS production-readiness controls

## Decision

AssuranceOS 0.8 is release-qualified. Its product, security, data, deployment,
and evaluator paths share the same code and signed artifacts.

## Control summary

| Area | Enforced control |
|---|---|
| Tenancy | JWT authorization, tenant-bound repositories, trusted-host policy |
| Agent authority | Signed releases and lease-bound execution envelopes |
| Tool access | Default-deny gateway and declared-tool intersection |
| Model safety | Inbound, argument, output, and reasoning guardrails |
| Evidence | Immutable acquisition, hashes, custody chain, lineage, retention |
| Decisions | Human-only approval and separation of duties |
| Reliability | Durable tasks, leases, retries, idempotency, transactional outbox |
| Testing | Signed deterministic releases and reproducibility manifests |
| Reporting | Claim graph, admissibility checks, fail-closed issuance |
| Memory | Tenant-qualified scope, approved-session generation, revisions, TTL |
| Runtime | Non-root image, read-only service filesystem, dropped capabilities |
| Supply chain | Lockfile, signed artifacts, pinned actions, SBOM and CVE gates |
| Observability | Correlated spans, gateway decisions, findings, token usage |

## Managed fleet proof

Agent Engine deployment emits a versioned proof document. The product verifies
the resource-name shape, complete 19-agent coverage, Gemini 3.7 model, and exact
signed package digest for every resource. Memory Bank configuration is embedded
in both the deployment plan and result.

## Release gate

CI and the security workflow are the executable readiness record. A change
cannot qualify while tests, coverage, signatures, migration checks, OpenAPI,
artifact inventory, dependency audit, static analysis, SBOM, container scan, or
infrastructure validation fail.
