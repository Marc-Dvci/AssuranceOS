# Component 5 report — Connector SDK and realistic local fixture connectors

## Outcome

AssuranceOS now has a governed, provider-neutral collection runtime that can execute approved
read-only collection grants, persist durable checkpoints, normalize provider objects, and acquire
them into the content-addressed evidence vault. Four production-shaped adapters run against
strict local HTTP cassettes without external credentials or network access.

No files under `apps/` were modified.

## Main additions

- `src/assuranceos/connectors/definitions.py`: typed instance, grant, request, page, source-object,
  health, and run contracts.
- `src/assuranceos/connectors/protocol.py`: minimal provider-neutral connector protocol.
- `src/assuranceos/connectors/transport.py`: live HTTP transport, response classification,
  rate-limit handling, and deterministic fixture transport.
- `src/assuranceos/connectors/service.py`: grant enforcement, run lifecycle, checkpoints, evidence
  ingestion, schema drift, audit events, and outbox emission.
- `src/assuranceos/connectors/repository.py`: tenant-scoped persistence operations.
- `src/assuranceos/connectors/adapters/`: GitHub, Jira, Confluence, and Google Drive adapters.
- `src/assuranceos/connectors/demo.py`: Asteria four-source collection demonstration.
- `migrations/versions/0005_connector_sdk.py`: connector instances, grants, runs, checkpoints, and
  source-object lineage.
- `docs/architecture/connector-sdk-and-collection-grants.md`: design, invariants, provider
  contracts, and cloud mapping.
- backend API routes for connector registration, grant approval/listing/revocation, and run
  inspection.

## Canonical state

Five tables were added:

1. `connector_instances` — tenant-owned configuration and secret references;
2. `collection_grants` — purpose, streams, resources, approver, expiry, and revocation;
3. `connector_checkpoints` — one versioned cursor per connector stream;
4. `connector_runs` — immutable request context and mutable bounded execution result;
5. `collected_source_objects` — run-specific source identity linked to canonical evidence.

The backend now has 38 canonical tables and five Alembic revisions.

## Grant enforcement

A run fails closed unless:

- the connector instance is active;
- the implementation type matches the registration;
- the grant is active, unexpired, and read-only;
- the stream is both implemented and approved;
- the requested repository, project, space, drive, or dataset is within the approved selector;
- connector health succeeds.

Credential values are not accepted by the canonical models. Only a `credential_ref` is persisted.

## Run and recovery behavior

- tenant-unique idempotency keys prevent duplicate provider calls;
- a page checkpoint is committed only after every object on that page is linked to evidence;
- interrupted runs can resume at the next page;
- stable acquisition keys prevent duplicate evidence on replay;
- repeated acquisitions of unchanged provider versions reuse the prior evidence record;
- the same provider version with different bytes is rejected as a source-version conflict;
- successful run schema shapes are fingerprinted and compared for drift;
- run start and completion emit correlated audit and transactional-outbox events.

## Provider implementations

### GitHub

- pull requests through `GET /repos/{owner}/{repo}/pulls`;
- explicit API version `2026-03-10`, media type, and user agent;
- `state=all`, updated-time ordering, maximum 100-item pages;
- `Link`-header pagination;
- repository selector and `Pull requests: read` declaration.

### Jira Cloud

- issues through enhanced JQL search `POST /rest/api/3/search/jql`;
- approved project boundary composed into JQL;
- explicit field set and `nextPageToken` pagination;
- baseline `read:jira-work` scope declaration.

### Confluence Cloud

- pages through REST v2 `/wiki/api/v2/pages`;
- approved space IDs and explicit body representation;
- cursor pagination from `Link` or `_links.next`;
- baseline `read:page:confluence` scope declaration.

### Google Drive

- full metadata snapshots through `files.list`;
- incremental metadata changes through `getStartPageToken` and `changes.list`;
- durable `nextPageToken` and `newStartPageToken` handling;
- shared-drive parameters and explicit response fields;
- metadata-only `drive.metadata.readonly` scope declaration.

## Evidence provenance

Every normalized source object records:

- provider object ID and provider version;
- stable locator and source timestamp;
- canonical JSON digest;
- connector instance, grant, stream, and provider request metadata;
- evidence classification, taint, retention, and engagement/task scope;
- content-addressed object storage and custody genesis;
- run-specific source-object-to-evidence linkage.

## Demonstrated path

The local Asteria connector demonstration:

1. registers four connector instances with secret references;
2. approves four purpose-bound collection grants;
3. health-checks each fixture provider;
4. collects two GitHub pull requests, two Jira issues, one Confluence policy, and one Drive file;
5. stores six immutable evidence records;
6. creates six source-object lineage rows;
7. records checkpoints, run metrics, audit events, and outbox events;
8. completes all four runs successfully.

## Deferred

- live OAuth and application-installation flows;
- Secret Manager and workload-identity credential providers;
- live provider tenant contract tests;
- webhook signature verification and event-triggered collection;
- binary streaming and provider file export;
- distributed provider rate-limit coordination;
- GitHub review/commit streams, Jira changelogs, Confluence attachments, and Drive content;
- production authorization around connector management endpoints.

## Validation

- 79 automated tests passed before release packaging.
- Fresh Alembic upgrade to revision `0005_connector_sdk` passed.
- Database model-drift validation passed.
- Python compilation passed.
- Connector fixture demo passed.
- Existing golden, orchestrator, scheduler, and evidence-vault demonstrations remained compatible.
- Connector HTTP management contracts passed.
- Grant denial, expiry, revocation, source-version conflict, rate limit, resume, deduplication,
  schema-drift, and provider-pagination paths passed.
- Frontend files remained unchanged.
