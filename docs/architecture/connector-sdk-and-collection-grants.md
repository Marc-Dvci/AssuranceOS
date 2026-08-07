# Connector SDK and collection grants

## Scope

Component 5 introduces a provider-neutral, read-only connector runtime and four production-shaped
REST adapters. The implementation deliberately separates provider-specific pagination and payload
normalization from grant enforcement, run state, checkpointing, evidence storage, and provenance.
It does not contain OAuth consent flows, credential values, webhook receivers, or frontend code.

## Design

```text
approved collection grant
        │
        ▼
connector service ── validates stream and resource scope
        │
        ▼
provider adapter ── provider-native pagination / checkpoint
        │
        ▼
normalized source objects
        │
        ├── connector run + checkpoint + source-object lineage
        └── evidence vault acquisition + custody + content hash
```

The SDK uses four small contracts:

- `ConnectorDescriptor`: connector type, supported streams, documented read scopes, and references;
- `CollectionRequest`: stream, resource scope, parameters, classification, and optional engagement;
- `ConnectorPage`: normalized source objects plus the checkpoint to persist after that page;
- `SourceObject`: immutable provider object identity, provider version, locator, payload, and source
  timestamp.

The connector service owns all canonical state and transaction boundaries. Adapters do not write to
the database or evidence vault directly.

## Collection-grant invariants

A collection can begin only when:

1. the connector instance is active;
2. the implementation type matches the registered connector type;
3. the grant is active and unexpired;
4. the grant is read-only;
5. the requested stream is exposed by the adapter and allowed by the grant;
6. every requested resource selector is a subset of the approved selector;
7. the connector health probe succeeds.

Credential values are never stored in canonical state. A connector instance stores only a
`credential_ref`, intended to resolve through Secret Manager, workload identity, or a local secret
provider outside model context.

## Run and checkpoint semantics

Each collection run has a tenant-unique idempotency key. Repeating the same request returns the
existing run without calling the provider again. A run records:

- connector instance and collection grant;
- stream and complete request contract;
- checkpoint before and after;
- start and completion timestamps;
- object counts;
- schema fingerprint and drift status;
- metrics and failure reason.

The runner commits a checkpoint only after every object on a page has been acquired and linked.
If execution stops between pages, a later run resumes from the committed cursor. Evidence
acquisition keys are derived from connector, stream, source identity, source version, and digest,
so replay is safe. Reusing one provider version identifier for different bytes is rejected as a
source-version conflict rather than silently accepted.

## Evidence provenance

Every source object becomes an accepted original evidence record with:

- provider source type and stable source locator;
- source object identifier and provider version;
- SHA-256 digest of canonical JSON;
- connector instance and grant identifiers;
- stream and provider request metadata;
- source timestamp, classification, taint, and retention state;
- append-only vault custody events.

Physical bytes may be deduplicated by the vault, while every collection run retains its own
source-object-to-evidence relationship.

## Provider adapters

### GitHub REST

The GitHub adapter collects pull requests with `state=all`, `sort=updated`, a maximum page size of
100, and `Link`-header pagination. It sends an explicit `X-GitHub-Api-Version` header, currently
pinned to `2026-03-10`, plus the recommended media type and user agent. The adapter exposes the
required fine-grained repository permission as `Pull requests: read`.

### Jira Cloud REST v3

The Jira adapter uses the enhanced JQL search endpoint `POST /rest/api/3/search/jql`, because the
older search operations are being removed. It composes the approved project selector into the JQL
expression and follows `nextPageToken` pagination. The baseline read scope is `read:jira-work`.

### Confluence Cloud REST v2

The Confluence adapter collects pages through `/wiki/api/v2/pages`, explicitly requests a body
representation, limits collection to approved space IDs, and follows cursor pagination from
`Link` or `_links.next`. The baseline scope is `read:page:confluence`.

### Google Drive API v3

The Drive adapter supports:

- full metadata snapshots through `files.list` with explicit `fields`, page tokens, shared-drive
  parameters, and `trashed = false` by default;
- incremental change collection through `changes.getStartPageToken` and `changes.list`, persisting
  `nextPageToken` between pages and `newStartPageToken` after the final page.

The implementation collects metadata only and declares
`drive.metadata.readonly`; downloading file content requires a separately approved connector
stream and broader scope.

## Transport and retry policy

The live transport uses bounded attempts and retries only after rate-limit or server-unavailable
responses. It honors `Retry-After`, then GitHub-style `X-RateLimit-Reset`, and otherwise uses bounded
exponential delay. Authentication, permission, rate-limit, protocol, and availability failures are
separate exception classes so orchestration can apply the correct retry or escalation policy.

`FixtureTransport` provides deterministic cassettes for local execution. It records every request
and refuses unregistered calls, preventing tests from passing through accidental network access.

## Local and cloud mapping

| Local component | Google Cloud deployment |
|---|---|
| `ConnectorService` worker call | Cloud Run Job or worker service |
| SQLAlchemy connector tables | Cloud SQL for PostgreSQL |
| `EvidenceVault` local store | Cloud Storage adapter |
| `credential_ref` | Secret Manager / workload identity |
| fixture transport | live `HttpxTransport` |
| synchronous invocation | Pub/Sub-triggered worker |

The service and adapters are queue-neutral. Pub/Sub acknowledgement must occur only after the run
state and page checkpoint transaction has committed.

## Official API references reviewed

- GitHub REST API versions, pull-request listing, pagination, fine-grained permissions, and rate
  limits;
- Jira Cloud REST v3 enhanced JQL search and pagination;
- Confluence Cloud REST v2 page endpoints, scopes, and cursor pagination;
- Google Drive API v3 `files.list`, `changes.list`, and change-token guidance.

These references guide the adapters but are not copied into the repository as redistributable API
specifications.

## Deferred

- OAuth and app-installation consent flows;
- Secret Manager and workload-identity credential providers;
- webhook verification and event-triggered collection;
- binary content streaming and multipart export;
- provider-specific permission introspection;
- live sandbox accounts and contract tests against external tenants;
- additional streams such as GitHub reviews/commits, Jira changelogs, Confluence attachments, and
  Drive content export;
- source-specific back-pressure and distributed rate-limit coordination.
