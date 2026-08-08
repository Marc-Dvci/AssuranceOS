# AssuranceOS — Devpost submission

## The trust layer for autonomous internal audit

AssuranceOS runs the internal-audit lifecycle end to end—onboard, plan, collect,
test, conclude, approve, remediate, retest, report, and monitor—while making
every conclusion independently checkable.

The product is built around one rule: model output can propose work, but it
cannot grant itself authority or become canonical evidence. Every agent action
is constrained by a signed release, a short-lived execution envelope, a
default-deny gateway, evidence provenance, deterministic tests, and recorded
human gates.

## Why it matters

Internal audit teams spend too much time reconciling source systems, rebuilding
workpapers, and proving how a conclusion was reached. Generic copilots can draft
text; they do not provide the chain of custody, independence rules, replay
protection, or evidence-grounded reporting required for assurance work.

AssuranceOS turns those controls into product primitives. The result is faster
coverage without trading away accountability.

## What the product does

- Onboards an organization through reviewed, source-attributed facts.
- Builds the audit universe and a risk-based portfolio plan.
- Compiles signed Audit Packs into durable, approval-aware engagements.
- Collects read-only evidence through purpose-bound connector grants.
- Runs signed deterministic control tests in a network-denied worker.
- Routes all agent tools through the same policy and identity gateway.
- Detects and neutralizes prompt injection before tainted content reaches
  canonical state.
- Adjudicates findings with skeptic review, materiality, quality review, and a
  human approval boundary.
- Opens remediation exactly once, synchronizes external tickets, and requires an
  independent retest before closure.
- Renders reports only when material claims resolve to accepted evidence or an
  explicit recorded scope statement.
- Launches recurring audits and converts monitoring signals into review cases,
  never directly into approved findings.

## Google technology

### Gemini 3.6 Flash

Gemini 3.6 Flash is the mandatory hosted model across runtime configuration,
signed agent profiles, Agent Engine deployment, Memory Bank generation, and
telemetry. The Google GenAI SDK supports both Vertex AI and Gemini API
transports. Structured JSON output is validated against task-specific schemas
before it can influence workflow state.

### Google Agent Development Kit and Agent Engine

Nineteen signed ADK agent roles form a managed specialist fleet. Deployment is
qualification-gated: all release cases must pass before the script can create
Agent Engine resources. The resulting proof document records each
projects/.../reasoningEngines/... resource name next to its signed package
digest, model, region, deployment time, and Memory Bank policy. Judge Mode
re-validates that map against the running release rather than displaying a
static inventory.

### Vertex AI Memory Bank

Every managed ADK application uses VertexAiMemoryBankService. Memory is:

- isolated by a tenant-qualified ADK user subject;
- generated only from sessions explicitly marked approved for memory;
- revisioned for inspection and consolidation;
- retained under a bounded TTL;
- treated as context, never as authoritative audit evidence.

This gives the fleet durable organizational context while preserving the
evidence and approval boundaries that make assurance defensible.

### Google Cloud

- Cloud Run serves the API and dedicated operational jobs.
- Cloud SQL stores canonical transactional state.
- Cloud Storage provides content-addressed evidence objects and signed exports.
- Pub/Sub delivers the transactional outbox.
- Cloud Trace and OpenTelemetry correlate agent reasoning, gateway decisions,
  guardrail findings, and model usage.
- Secret Manager supplies credential and signing-key references.
- Terraform defines the production topology and IAM.

## The golden engagement

The included Asteria Systems dataset is a synthetic company, not a fixture: 51
files across nine source systems, containing a 44-merge change population, an
18-leaver termination population joined to 254 directory accounts, and ten
deliberate conditions.

Four must be reported — a merge with no independent approval, an emergency
change whose retrospective approval was never filed, a merge with no change
ticket, and a terminated contractor who retains a production administrator role.
Three must be suppressed with a stated reason — an active approved waiver, a
merge whose UTC offset places it outside the period, and a retained account
covered by a time-limited exception. One control must be reported as operating
effectively. One is a prompt injection embedded in a policy page.

A correct run raises exactly the four supported defects, suppresses the three
non-findings with their reasons, contains the injection without changing the
audit result, refuses model-authored approval, creates one remediation under
replay, rejects a non-independent retest, and closes only after independent
verification.

The corpus map is `demo/asteria/CORPUS.md` and the answer key is
`demo/asteria/ground_truth.yaml`.

Run the complete deterministic path:

```bash
make loop-demo
```

Run the signed release qualification:

```bash
python scripts/run_agent_evaluations.py --mode contract
python scripts/deploy_adk_agent.py --plan
```

Open the product:

```bash
uvicorn assuranceos.api:app --port 8080
```

Then visit / for the operator cockpit or /judge for the evaluator surface.

## Proof, not presentation

Judge Mode reads the application and release registries live. It exposes:

- signed fleet inventory and package digests;
- managed Agent Engine deployment and Memory Bank configuration;
- contract-evaluation totals;
- signed Audit Packs and deterministic-test releases;
- canonical execution traces and gateway decisions;
- prompt-injection replay with detector output and mutation checks;
- idempotency replay with persisted remediation identity;
- seeded ground truth next to observed results.

Raw proof remains available behind expandable details, while the primary view
explains the result in business language.

## Gemma, and why the same governed path runs on both

The governed runtime holds one model contract with three transports behind it:
the Google GenAI SDK for Gemini 3.6 Flash on Vertex AI, an OpenAI-compatible
client, and a scripted client for tests. The runtime never learns which one it
is talking to, so model choice is a deployment decision and the governance
guarantees are not.

That makes a second, verifiable claim possible. The complete assurance loop —
population test, injection containment, skeptic review, human gate, remediation
under replay, independent retest — has been run end to end against **Gemma 4
12B** (`IQ4_XS`) on a loopback `llama.cpp` server with network egress denied,
producing the same conclusion and the same ground-truth match as the hosted
path:

```bash
python scripts/run_assurance_loop_demo.py   --model-mode local   --base-url http://127.0.0.1:5000/v1   --model gemma-4-12b-it-IQ4_XS.gguf
```

The local profile exists for a real constraint, not for the demonstration: some
audit populations cannot leave the auditee's network. It proves private local
inference, prompt and manifest portability, no-egress enforcement, governed tool
use, and reproducible model qualification. It does not claim cloud feature
parity, and the primary golden audit remains the Google Cloud path.

## Security and governance

- Ed25519-signed agent, Audit Pack, control-test, execution-envelope, report, and
  evidence-export artifacts.
- Tenant-bound RBAC and workload identities.
- Tool authority computed as the intersection of signed package policy and the
  lease-bound execution envelope.
- Content screening at inbound context, tool arguments, model output, and
  reasoning retention.
- Immutable evidence acquisition records and hash-chained custody events.
- Transactional outbox and idempotency keys on externally visible state changes.
- Human-only approval and separation-of-duties checks for retesting.
- Local privacy runtime with loopback-only model routing and signed bundle
  transfer for restricted engagements.

## Quality

The repository gate runs lint, complete tests with an 85% coverage floor,
release-signature verification, all 76 fleet evaluation cases, SQLite and
PostgreSQL migrations, OpenAPI and artifact-manifest checks, Docker build, and
Terraform validation. Security workflows add dependency review, CodeQL, secret
scanning, SBOM generation, and container vulnerability scanning.

AssuranceOS is not an agent that asks to be trusted. It is the system that makes
an autonomous agent earn trust, one attributable decision at a time.
