# Plan — collecting one source system live

> **Carried out on 2026-08-16.** The GitHub adapter gained `commits` and
> `commit_reviews`, `SCM-02 · Reviewed change path` was authored and signed,
> `src/assuranceos/collection_projection.py` projects a collection into its
> declared datasets, and the evaluator workspace runs the whole path over any
> repository. Receipts: `release/live-collection-proof.json` and
> `release/evaluator-audit-proof.json`. Two decisions in the last section were
> resolved as written there: the auditee is this repository, and the result is
> published. What was **not** built is the standalone
> `scripts/run_live_collection_demo.py` and its Judge Mode tile; the same run is
> reachable from `/workspace` and from `scripts/verify_evaluator_audit.py`,
> which is where an evaluator actually meets it.

Every population the demonstration tests today is synthetic. That is a deliberate
property of the Asteria corpus, which exists so a result can be reproduced byte
for byte and published without a client's records in it, and it stays. What is
missing next to it is a single run where the population came off a real API, and
this plan is that run.

The claim it buys, in one sentence a reviewer can check: **the change-management
population in this run was collected from the live GitHub API under a
purpose-bound read-only grant, and every row in the result traces to the bytes
that were hashed when they arrived.**

The auditee is this repository. The first thing AssuranceOS audits for real is
the change process it was built under, and it does not pass.

---

## The population, measured before planning against it

Read from `api.github.com` on 16 August 2026, unauthenticated:

| | |
| --- | --- |
| Pull requests on `Marc-Dvci/AssuranceOS` | 10, all opened by Dependabot |
| Merged | **0** — eight closed unmerged, two open |
| Commits on `main` since 2026-08-01 | 44, one page at `per_page=100` |

That measurement decides the design. A procedure over *merged* pull requests has
an empty population here and demonstrates nothing, so the population has to be
**commits on the default branch**, with pull requests as the reference dataset
that a commit either reconciles to or does not.

It also fixes the expected result before the run, which is the right order: 44 of
44 changes reached `main` without a reviewed pull request. That is a real finding
about a real system, and reporting it is worth more than a synthetic pass.

## What already exists

Most of this is wiring, not building. The live path is already the path the
fixture demonstration takes; only the transport differs.

| Piece | Where | State |
| --- | --- | --- |
| Bounded live HTTP transport | `src/assuranceos/connectors/transport.py` (`HttpxTransport`) | Redirects off, three attempts, backoff on typed rate-limit and unavailable errors |
| GitHub adapter | `src/assuranceos/connectors/adapters/github.py` | `pull_requests` stream, Link-header pagination, per-object source version `updated_at:head_sha` |
| Credential resolution | `src/assuranceos/connectors/credentials.py` | `env://` and `gcp-secret://`; references are stored, values never are |
| Live adapter construction | `src/assuranceos/connectors/factory.py` | Requires `base_url` and `credential_ref`; unsupported types fail closed |
| Grant enforcement | `ConnectorService._validate` | Read-only, unexpired, stream on the grant, requested scope inside the selectors |
| Evidence on arrival | `ConnectorService.run` | Each source object hashed and vaulted, with request, grant and provider provenance, and a version-reuse conflict check |
| One governed collection, from the CLI | `scripts/run_connector_collection.py` | Takes tenant, instance, grant, stream and scope; already the real path |
| Signed procedure registry | `tests-library/`, `scripts/release_control_tests.py` | Ed25519, semantic versions, immutable releases |

## What has to be built

Ordered so each step is testable before the next depends on it.

### 1. A `commits` stream on the GitHub adapter

`GitHubPullRequestConnector` gains a second stream rather than a second class, so
one registered instance and one grant cover both. Follows the existing shape
exactly: `GET /repos/{owner}/{repo}/commits` with `sha`, `since`, `until` and
`per_page`, Link-header pagination, and a `SourceObject` per commit whose
`source_object_id` is the SHA and whose `source_version` is the SHA as well —
a commit is immutable, so a changed version for the same id is a real conflict
and the existing check should fire on it.

Rename the class to `GitHubConnector` and keep the old name bound as an alias, so
`ConnectorFactory` and the fixture demonstration do not move at the same time as
the behaviour.

`scope_for` already rejects anything but `owner/repository`. It stays the only
place scope is derived, because the grant check reads its output.

### 2. `SCM-02 · Reviewed change path`, a signed procedure

A new package under `tests-library/scm/reviewed-change-path/`, released and
signed like the other three. It cannot be a parameter on `SCM-01@2.0.0`: that
manifest is signed, its datasets are declared, and bending a released procedure
to fit the population it is pointed at is the behaviour this engine exists to
refuse.

| | |
| --- | --- |
| `commits` | population, primary key `commit_sha`, evidence required |
| `pull_requests` | reference, primary key `pull_request_id`, evidence required |
| Criterion | every commit on the default branch in period reconciles to a **merged** pull request carrying at least `required_approvals` approvals |
| Exception | a commit with no merged pull request behind it, classified `unreviewed_change` |
| Not an exception | an open or closed-unmerged pull request. It is not a compensating control, and the procedure must not quietly count it as one |
| Reconciliation | `require_complete: true`, expected count supplied as a parameter from the collection metrics |
| Sampling | `full_population` |

The distinction in the fourth row is the one worth testing first, because it is
where a procedure of this shape usually goes wrong: ten pull requests exist and
none of them reviewed anything that reached `main`.

### 3. Projection from collected objects to datasets

`src/assuranceos/corpus.py` projects corpus files into `ControlTestDataset`
records. The live path needs the same projection from
`ConnectorRepository.list_collected_objects(tenant_id, run_id)`, reading each
object's vaulted bytes rather than a file on disk.

One thing improves on the corpus path and should be kept: the corpus gives every
row in a dataset the evidence id of the *file* it came from, while a collection
stores one evidence record per source object, so each row carries the evidence id
of its own commit. An exception then cites the bytes of exactly one commit.

Put it in a new `src/assuranceos/collection_projection.py` rather than in
`corpus.py`, which is about the repository corpus.

### 4. `scripts/run_live_collection_demo.py`

One script, in the order an operator would do it by hand:

1. register the connector instance (`base_url=https://api.github.com`,
   `credential_ref=env://ASSURANCEOS_GITHUB_HEADERS`);
2. approve a collection grant: `allowed_streams=["commits","pull_requests"]`,
   `resource_selectors={"repositories":["Marc-Dvci/AssuranceOS"]}`, a purpose
   string, and an expiry a few hours out;
3. run both streams through `ConnectorService.run`;
4. project, and execute `SCM-02` over the result;
5. write the receipt;
6. **revoke the grant**, and record the revocation.

Step 6 is not tidiness. A grant that outlives the collection it was approved for
is the finding this product raises against other people, and the run should end
with the grant closed and the revocation on the record.

Flags: `--repository`, `--since`, `--until`, `--dry-run` (plan only, no network),
and `--tenant`. Default the period to the current month so the numbers move.

### 5. The receipt

`release/live-collection-proof.json`, next to the two receipts that already ship,
carrying enough that the run can be argued with rather than believed:

```
schema, generated_at, host, repository, period,
grant: {purpose, allowed_streams, resource_selectors, approved_at, expires_at, revoked_at},
runs: [{stream, run_id, pages, objects_seen, objects_ingested, unchanged}],
evidence: {count, first_sha256, manifest_sha256},
control_test: {test_id, version, release_digest, run_id, conclusion,
               population_count, exception_count, input_manifest_hash}
```

No token, no header values, no credential reference beyond its scheme. The
receipt is published; the grant it describes is not a secret but its contents
should still be the minimum that proves the claim.

### 6. Judge Mode

One tile, `Live source collection`, reading the receipt the same way the Agent
Engine tile reads its own: repository, period, objects hashed, the procedure's
conclusion, and the grant's revocation time. It must read `not_configured` and
say so when the receipt is absent, exactly as the managed-fleet tile does, so a
teardown never turns an empty file into a green tile.

### 7. Tests

- the `commits` stream against a `FixtureTransport`, including a second page;
- version reuse for one SHA raises `SourceVersionConflictError`;
- a grant naming only `pull_requests` refuses a `commits` request;
- a grant naming another repository refuses this one;
- an expired grant refuses, and a revoked grant refuses;
- `SCM-02` golden case: a commit behind a merged, approved pull request passes;
  the same commit behind an *open* pull request is an exception;
- the projection carries a distinct evidence id per row;
- the receipt writer redacts headers.

The fourth and fifth are the ones that matter to the claim, because a grant that
cannot refuse is a grant that proves nothing.

## What this run must not do

- **No writes.** The grant is read-only, the adapter exposes no write stream, and
  the remediation writers stay out of this path.
- **No token in the repository.** A fine-grained PAT, public repositories,
  read-only metadata and pull requests, passed as
  `ASSURANCEOS_GITHUB_HEADERS={"Authorization":"Bearer …"}`. Unauthenticated is
  60 requests an hour against this API and the factory requires a credential
  reference anyway.
- **No change to the Asteria corpus.** It stays synthetic, and the write-up keeps
  saying so. The point of this run is to add one real population next to it, not
  to blur what the rest is.
- **No third-party conclusion.** If a busier repository is used to exercise
  pagination, the receipt records collection metrics only. Publishing a control
  verdict about somebody else's engineering process is not something this
  demonstration needs.

## Acceptance

The run is done when all of these hold at once:

1. `release/live-collection-proof.json` names `api.github.com`, a period, and a
   revoked grant;
2. the evidence count equals the objects ingested, and one row's evidence id
   resolves to bytes whose digest matches the receipt;
3. `SCM-02` reports `ineffective` over a population of 44 with 44 exceptions, and
   the ten unmerged pull requests appear as a limitation rather than as
   compensating evidence;
4. `POST /control-test-runs/{id}/verify-reproducibility` passes against the
   recorded input hash;
5. Judge Mode's tile reads from the receipt, and reads `not_configured` when it
   is moved away;
6. the whole suite is green and `scripts/build_artifact_manifest.py` has been
   re-run.

## What changes in the write-up

One row in "Other data sources", and one sentence in "The audit it runs":

> Everything the platform reads in the demonstration is synthetic and lives in
> the repository, with one exception recorded on purpose: the change population
> in `release/live-collection-proof.json` was collected from the live GitHub API
> under a read-only grant that was revoked when the run finished. It is this
> repository's own change history, and the control fails: 44 of 44 changes
> reached `main` without a reviewed pull request.

The disclosure that the rest is fixture-backed stays exactly where it is. It is
checkable in `capability-status.yaml`, and a reviewer who finds one caveat
removed stops believing the receipts that are real.

## Decisions that are not mine to make

1. **The auditee.** This repository, with a true failing result, or a busier
   public repository for pagination with collection metrics only. The first is
   the better story and the one this plan is written against.
2. **Whether the finding is published in the write-up.** It is a weakness in the
   author's own change process, stated plainly. It reads as confidence rather
   than as an admission, but that is a judgement call.
3. **Whether the seeded Cloud Run tenant is reseeded** so the tile is populated
   for a reviewer, or whether the receipt in the repository is enough.
