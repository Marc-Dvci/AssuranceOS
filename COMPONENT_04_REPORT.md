# Component 4 report — Content-addressed evidence vault and provenance

## Outcome

AssuranceOS can now acquire source material into tenant-scoped immutable storage, preserve distinct
acquisition provenance over deduplicated bytes, create explicit derivatives, verify object and
custody integrity, enforce retention gates, and produce self-contained evidence export packages.
The component runs locally without Cloud Storage, connector credentials, model access, or frontend
changes.

## Main additions

- `src/assuranceos/vault/storage.py`: object-store protocol and atomic local content-addressed
  adapter.
- `src/assuranceos/vault/repository.py`: tenant-scoped evidence, transformation, and custody access.
- `src/assuranceos/vault/custody.py`: canonical append-only custody-event hashing.
- `src/assuranceos/vault/service.py`: acquisition, derivation, access, verification, retention,
  tombstoning, garbage collection, and export workflows.
- `src/assuranceos/vault/export.py`: deterministic ZIP writer and independent verifier.
- `src/assuranceos/vault/definitions.py`: typed service result contracts.
- `src/assuranceos/vault/demo.py`: Asteria evidence and prompt-injection-redaction demonstration.
- `migrations/versions/0004_evidence_vault.py`: evidence metadata extensions and custody chain.
- `docs/architecture/evidence-vault-and-provenance.md`: design, invariants, and cloud mapping.
- backend API routes for bounded acquisition, derivatives, content, integrity, custody, lineage,
  retention, tombstoning, and export.
- JSON schemas for custody events and export manifests.

## Implemented behavior

### Content-addressed storage

- tenant-local object keys derived from SHA-256;
- atomic immutable write using temporary files and hard links;
- digest and size verification before object reuse;
- read-only permissions on newly created local objects;
- distinct evidence records for identical bytes acquired from different sources;
- physical deduplication inside a tenant without cross-tenant object sharing.

### Acquisition and provenance

- tenant, engagement, and task scope validation before storage;
- optional acquisition keys for connector retry idempotency;
- conflict rejection when an acquisition key is reused with different bytes;
- canonical source type, locator, timestamps, classification, acceptance, taint, retention, and
  metadata;
- atomic canonical record, custody genesis, audit event, and outbox event.

### Custody and integrity

- append-only custody sequence per evidence record;
- hash chain over identity, action, actor, timestamp, details, and prior hash;
- verification of every event and chain head;
- migration-generated custody genesis for pre-existing evidence;
- byte verification against canonical digest and size;
- explicit `verified`, `mismatch`, `missing`, and `purged` states;
- fail-closed exceptions after integrity failures are recorded.

### Derivatives and lineage

- immutable originals and separate derivative records;
- one transformation edge for every source-to-derivative relationship;
- operation, tool version, parameters, and timestamp provenance;
- automatic taint and legal-hold propagation;
- engagement and task inheritance only when all sources agree;
- explicit classification required when source classifications differ.

### Retention and deletion

- legal hold prevents tombstoning;
- absent or unexpired retention approval prevents tombstoning;
- tombstoned records retain canonical identity and custody but deny content access;
- garbage collection removes only unreferenced objects older than a grace period;
- shared objects remain while any active acquisition references them.

### Verifiable exports

- deterministic ZIP entry ordering and timestamps;
- canonical manifest JSON and separate manifest checksum;
- one object entry per digest;
- automatic inclusion of derivative ancestors;
- complete evidence provenance, custody chains, lineage, and object inventory;
- independent verification of paths, declarations, digests, sizes, custody, lineage, and requested
  evidence membership;
- package SHA-256 returned separately and recorded in custody.

## Demonstrated path

The local Asteria demonstration:

1. acquires four synthetic GitHub, Jira, governance, and Confluence sources;
2. marks the embedded prompt-injection source as tainted;
3. creates a separate redacted policy derivative;
4. preserves the original and transformation relationship;
5. verifies all five stored objects;
6. verifies the derivative custody chain;
7. exports the derivative and automatically includes its original source;
8. independently verifies the resulting ZIP package.

## Design characteristics

- The vault contains no connector, model, queue, or frontend logic.
- Physical deduplication and evidentiary identity are separate concepts.
- The local storage adapter can be replaced by Cloud Storage behind one protocol.
- Missing evidence never becomes an effective control conclusion.
- Redaction is lineage-producing derivation, not mutation.
- Deletion is split into canonical tombstoning and conservative physical garbage collection.
- Export verification does not trust ZIP filenames or the manifest without recomputation.
- All files under `apps/` remain unchanged.

## Deferred

- Cloud Storage adapter with generation preconditions and managed retention;
- digital signing of export manifests;
- production authentication and purpose-based authorization;
- malware scanning, DLP, and document parser isolation;
- KMS-backed encryption policy and customer-managed keys;
- legal-hold approval workflow and deletion authorization;
- direct streaming of large connector objects into the store;
- production connector collection grants and source request manifests.

## Validation

- 63 automated tests passed.
- Fresh Alembic upgrade to revision `0004_evidence_vault` passed.
- Upgrade from Component 3 with legacy evidence custody backfill passed.
- Alembic model-drift check reported no new operations.
- Python compilation passed.
- Repository, agent package, common schema, and Audit Pack validation passed.
- Golden, orchestrator, scheduler, and evidence-vault demonstrations passed.
- Evidence API and generated OpenAPI contracts passed.
- Object tampering, custody tampering, export tampering, tenant isolation, retention, and garbage
  collection negative paths passed.
- Frontend hashes remain unchanged.
