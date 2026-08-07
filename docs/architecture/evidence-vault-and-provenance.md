# Content-addressed evidence vault and provenance layer

## Purpose

The evidence vault preserves source material as immutable, tenant-scoped objects while keeping
acquisition identity, source provenance, transformations, custody, retention, and integrity state
in the canonical database. It is deliberately independent of connectors, model providers, queues,
and the web application.

The implementation follows four rules:

1. identical bytes do not imply identical evidence acquisitions;
2. originals and derivatives are separate canonical records;
3. every consequential custody transition is append-only and hash chained;
4. missing or altered bytes fail closed and cannot be interpreted as valid evidence.

## Module boundary

The implementation is under `src/assuranceos/vault/`:

- `storage.py` — object-store contract and local content-addressed adapter;
- `repository.py` — tenant-scoped evidence, lineage, and custody persistence;
- `custody.py` — canonical custody-event hashing;
- `service.py` — acquisition, derivation, access, verification, retention, and export workflows;
- `export.py` — deterministic export writer and independent package verifier;
- `definitions.py` — stable typed service results;
- `demo.py` — synthetic Asteria evidence demonstration.

`LocalObjectStore` is the local and Docker adapter. A future Cloud Storage adapter can implement the
same `ObjectStore` protocol without changing the domain service.

## Canonical records and physical objects

An `evidence_records` row represents one acquisition or one derived artifact. The physical bytes are
stored under a tenant-specific content address:

```text
<evidence-root>/<tenant-id>/objects/<sha[0:2]>/<sha[2:4]>/<sha256>
```

This produces tenant-local deduplication without exposing cross-tenant object existence. Two
connectors may collect identical bytes and create two records with different source locators,
collection times, scopes, and custody histories while referencing the same tenant object.

An optional acquisition key makes connector retries idempotent. Reusing the key with different
bytes is rejected.

## Immutable object write

The local adapter:

1. hashes the supplied bytes;
2. verifies the expected digest;
3. writes to a temporary file and flushes it;
4. creates the final content-addressed object atomically with a hard link;
5. verifies an existing object instead of overwriting it;
6. marks newly created objects read-only.

A digest or size mismatch is an immutable-object conflict, not an update operation.

## Acquisition transaction

Before storing evidence, the service verifies the tenant and any supplied engagement or task scope.
After the object is present, one database transaction creates:

- the canonical evidence record;
- the genesis custody event;
- the audit event;
- the transactional outbox event.

An object can remain unreferenced if a process terminates between storage and database commit. The
garbage collector removes only unreferenced objects older than a configurable grace period, so it
does not race normal acquisitions.

## Custody chain

`evidence_custody_events` is append-only. Sequence assignment is serialized by locking the evidence
record on PostgreSQL; SQLite serializes writes at database level.

Each event hash covers:

```text
tenant_id
+ evidence_id
+ sequence_no
+ action
+ actor_type
+ actor_id
+ occurred_at in canonical UTC form
+ canonical JSON details
+ previous_event_hash
```

The verifier recomputes every link and reports the exact broken sequence. The migration creates a
`legacy_registered` genesis event for evidence that existed before Component 4.

Custody actions currently include acquisition, access, derivation use, derivative creation,
integrity verification or failure, retention changes, tombstoning, and export.

## Derivatives and lineage

Derived files are new evidence records. `evidence_transformations` connects every source record to
the derivative and records the operation, tool version, parameters, and creation time.

The service:

- propagates source taint and legal hold;
- preserves engagement or task scope only when all sources agree;
- requires an explicit classification when sources have different classifications;
- never mutates or replaces the original object.

Redaction is therefore a transformation, not an edit to source evidence.

## Integrity verification

Verification re-hashes the stored object and compares both digest and size with canonical state.
The record is updated to one of:

- `verified`;
- `mismatch`;
- `missing`;
- `purged`.

A mismatch or missing object is recorded in custody and then raised to the caller. The service does
not return a successful result with degraded integrity.

## Retention, legal hold, and deletion

Deletion is split into two operations:

1. **Tombstone:** permitted only when no legal hold applies and the approved retention date has
   expired. The record and custody history remain canonical, but content access is denied.
2. **Garbage collection:** removes physical objects only when no active evidence record references
   the storage key and the object is older than the grace period.

This design supports shared content-addressed objects without deleting bytes still referenced by a
separate acquisition.

## Verifiable export package

Exports are deterministic ZIP packages containing:

```text
manifest.json
manifest.sha256
objects/<sha256>
```

The manifest includes evidence identities, source provenance, classification, hashes, sizes,
metadata, complete custody chains, lineage edges, requested records, and automatically included
ancestors. Objects are included once per digest.

The independent verifier rejects:

- unsafe or duplicate archive paths;
- missing, undeclared, or extra entries;
- altered manifest or object bytes;
- invalid object sizes or digests;
- duplicate evidence or object declarations;
- broken custody chains or custody heads;
- lineage edges that reference absent evidence;
- requested identifiers not contained in the package.

The package SHA-256 is returned separately. The local package proves internal integrity, not signer
identity; signed release/export envelopes are a later security component.

## API contracts

The backend API supports bounded raw-body acquisition and derivation, metadata listing, content
access with a declared purpose, integrity checks, custody and lineage inspection, retention changes,
tombstoning, and streamed ZIP export. Authentication and authorization remain explicitly outside
this component and must be added before production use.

## Cloud mapping

For Google Cloud deployment:

- `LocalObjectStore` maps to a Cloud Storage adapter with generation preconditions;
- local paths map to bucket object keys;
- object versioning and retention policies supplement, but do not replace, canonical retention state;
- customer-managed encryption, bucket IAM, and regional placement are deployment policy;
- the SQL custody and lineage records remain the source of truth.

## Known limitations

- the local API buffers uploads up to a configured bound rather than streaming directly to storage;
- local read-only file permissions are defense in depth, not an operating-system security boundary;
- export packages are hashed but not digitally signed;
- production authorization, legal-hold approvals, malware scanning, DLP, and KMS integration are
  deferred to later components;
- PostgreSQL row-lock behavior is implemented but cannot be runtime-tested in this sandbox because
  Docker is unavailable.
