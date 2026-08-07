# Deterministic control-test engine and versioned registry

## Purpose

Language-model agents select and interpret approved procedures; they do not perform authoritative
numerical or rule-based testing. Component 6 provides a deterministic execution boundary whose
inputs, code, release, population treatment, environment, and outputs can be independently verified.

## Release package

Each test is an immutable signed directory containing:

- `manifest.yaml` with identity, engine, datasets, reconciliation, sampling, resource, and library policy;
- JSON Schemas for the request envelope, parameters, and result;
- one reviewed Python or SQL entrypoint;
- golden cases and known limitations;
- `release.json` containing every file digest and the package digest;
- an Ed25519 signature verified against the committed trust key.

The database mirrors released packages in `control_test_releases`. A `(test_id, version)` whose
package, code, or manifest hash changes is rejected. Changes require a new semantic version.

## Run lifecycle

1. Resolve an exact signed release; no implicit latest-version migration occurs.
2. Validate parameters, dataset names, row schemas, evidence requirements, and tenant references.
3. Reconcile the authoritative population against expected count and duplicate-key policy.
4. Apply full-population or deterministic hash sampling.
5. Persist the run and immutable dataset-binding hashes before execution.
6. Execute in the selected deterministic runtime.
7. Validate the typed output and persist exception records.
8. Commit the result, audit event, and transactional outbox event atomically.
9. Reproduce only when resubmitted inputs match the recorded input manifest hash.

A technically failed test is never converted to a control failure or an effective result. Incomplete
populations fail closed as `population_incomplete` when the release requires complete coverage.

## Execution engines

### Python

The local adapter uses an isolated Python process with:

- ignored caller environment and user site configuration;
- deterministic hash seed and UTC locale;
- network socket denial;
- CPU, file-size, descriptor, timeout, and data-memory bounds;
- static import allowlisting and prohibited dynamic-code/file-access calls;
- bounded JSON input and output.

The local process is defense in depth, not a multi-tenant kernel security boundary. The production
adapter should run the same signed package in a network-denied Cloud Run Job or equivalent hardened
sandbox.

### SQL

Datasets are loaded into an ephemeral SQLite database. The runtime accepts one `SELECT` or `WITH`
statement, enables query-only mode, exposes no external database attachment, and converts returned
exception rows into the canonical result contract. A production PostgreSQL analytical adapter can
implement the same interface and manifest semantics.

## Reproducibility manifests

Every run records three SHA-256 identities:

- **input manifest:** exact dataset content hashes, schemas, evidence references, parameters, and period;
- **execution manifest:** signed release, code and manifest hashes, sampling, resources, and runtime environment;
- **result manifest:** typed output plus reconciled population statistics.

The verification operation reruns the exact release only after the caller supplies inputs matching
the recorded input hash, then compares the complete result manifest hash.

## Implemented releases

- `SCM-01@2.0.0`: Python full-population test of peer approval and approved change tickets before merge.
- `IAM-01@1.0.0`: SQL full-population test of terminated-user deprovisioning deadlines.

Both contain approved-exception handling and golden false-positive cases.
