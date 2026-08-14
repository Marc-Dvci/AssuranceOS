# AssuranceOS — Devpost submission

## What an audit function costs, and what one costs here

An internal audit function is four people and something near half a million
dollars a year, and it still cannot cover everything. The plan names the third
of the universe it will not reach, and a human signs for the residual risk. So a
two-hundred-person company with real customers and real contractual obligations
usually has no audit function at all — a team to check the controls it promised
costs more than the risk feels like it is worth.

One governed agent task in AssuranceOS — read the signed control test over a
44-change population, conclude, get refused a tool it was never granted, repair
a citation the output gate rejected — was measured at **4,032 input and 391
output tokens**. At Gemini 3.7 Flash's published rate that is **$0.009**.

That is the whole argument for building this. At nine tenths of a cent per
governed task, the constraint on audit coverage stops being budget. It becomes
trust: if a machine did the audit, why would anyone act on what it concluded?

The platform meters that usage itself rather than asserting it. Every model call
records its token usage on the reasoning span, the cockpit renders the total on
a **What this cost to run** card priced at published rates, and the card
declares when a run was scripted rather than measured — because a cost computed
from a scripted client's word counts looks exactly like one that was measured.

## The trust layer for autonomous internal audit

AssuranceOS runs the internal-audit lifecycle end to end—onboard, plan, collect,
test, conclude, approve, remediate, retest, report, and monitor—while making
every conclusion independently checkable.

The product is built around one rule: model output can propose work, but it
cannot grant itself authority or become canonical evidence. Every agent action
is constrained by a signed release, a short-lived execution envelope, a
default-deny gateway, evidence provenance, deterministic tests, and recorded
human gates.

Where audit teams do exist, they spend too much time reconciling source systems,
rebuilding workpapers, and proving how a conclusion was reached. Generic copilots
can draft text; they do not provide the chain of custody, independence rules,
replay protection, or evidence-grounded reporting required for assurance work.

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
- Publishes the signed fleet as a **catalogue** a department can adopt from —
  each agent's mandate, its non-goals, who may call it, the tools its package
  permits, its human gates and its known limitations, all read from the signed
  artefact so an entry cannot promise more than the release verifies.
- Meters what the work cost. Token usage is recorded on every model call and
  totalled on a **What this cost to run** card against records tested, evidence
  hashed and human decisions required — priced at published rates, with the
  model that *served* the tokens named separately from the model the price came
  from, and with unmetered runs declared rather than quietly counted.

## Google technology

### Gemini 3.7 Flash

Gemini 3.7 Flash is the mandatory hosted model across runtime configuration,
signed agent profiles, Agent Engine deployment, Memory Bank generation, and
telemetry. The Google GenAI SDK supports both Vertex AI and Gemini API
transports. Structured JSON output is validated against task-specific schemas
before it can influence workflow state.

### Google Agent Development Kit and Agent Engine

Nineteen signed ADK agent roles form a managed specialist fleet. Deployment is
qualification-gated: all release cases must pass before the script can create
Agent Engine resources. The resulting proof document records each
projects/.../reasoningEngines/... resource name next to its signed package
digest, model, region, deployment time, managed Agent Identity, and Memory Bank
policy. The deploy command reads every resource back through the Agent Engine
API. Judge Mode re-validates that receipt against the running release rather
than displaying a static inventory; until that receipt exists it says
"deployment-qualified," not "operational."

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

## An agent that does the audit, not one that describes it

The distinction the whole track turns on is whether the model is doing work or
narrating it, and there is a mechanical test for that: does a tool result ever
come back into the prompt? Until it does, an agent's "tool calls" are a list of
things it claims to have done, executed by someone else, whose answers it never
reads — and its conclusion can only ever restate the documents it was handed.

The governed runtime is a real loop. The agent replies either with a request for
data or with a conclusion. A request is routed through the Agent Gateway, and
whatever comes back — a population, a denial and its reason, a refusal from the
output gate — is written into the next prompt.

```bash
make agent-audit-demo                                     # deterministic
python scripts/run_agent_audit_demo.py --model-mode local \
  --base-url http://127.0.0.1:5000/v1 --model gemma-4-12b-it-IQ4_XS.gguf \
  --context-tokens 16384                                  # Gemma 4 12B decides
python scripts/run_agent_audit_demo.py --model-mode vertex   # Gemini 3.7 Flash
```

Thirteen domain tools are bound to real services — evidence query with custody
recorded against the agent's own identity, signed control-test execution,
population reconciliation, exception classification, criteria and control reads,
contradiction search, and a finding proposal that proposes and nothing more.
Each publishes the arguments it takes, because a tool that is only a name gets
called with invented arguments; measured on Gemma 4 12B, `tests.execute` arrived
with no `test_id` at all until the contract was published in the prompt.

The boundary is proven rather than hoped for. A well-behaved model never asks for
a tool outside its envelope, so the demonstration asks for one explicitly, under
the same identity, and records the denial.

### Evidence is never trimmed to fit

The worst failure available to a system like this is quiet. An OpenAI-compatible
server handed more input than its window drops the overflow, returns HTTP 200,
and answers confidently on whatever survived. Nothing downstream can tell that
answer apart from one drawn from the whole population.

Measured against the local endpoint: a 51,909-token prompt was answered after the
server read 12,288 tokens, with no error of any kind.

So the runtime refuses instead. Before the call, if the evidence plus the reserved
answer exceeds the served window, the task ends as `context_exceeded` naming the
shortfall. After the call, if the server reports reading fewer tokens than the
prompt can possibly encode to, the task ends as `context_truncated`. Neither path
samples, summarises or truncates the evidence to make it fit — an audit conclusion
drawn from the rows that happened to survive is not a weaker conclusion, it is a
different one.

Nothing here caps context. The window is a property of the serving process, and
the serving process does not advertise it — `/props`, `/slots` and `/v1/models`
are all silent — so `--context-tokens auto` measures it, by observing where
`usage.prompt_tokens` pins on a deliberately oversized prompt. The measurement
exists so the refusal can be accurate, and it follows whatever window is served:
raise the server's `-c` and the same command uses all of it. On Vertex AI, where
Gemini 3.7 Flash serves a million tokens and rejects an oversized request rather
than trimming it, the check never fires.

## The golden engagement

The included Asteria Systems dataset is a synthetic company, not a fixture: 56
files across ten source systems, containing a 44-merge change population, an
18-leaver termination population joined to 254 directory accounts, a nine-ticket
priority-one incident population, and seventeen deliberate conditions.

Eight must be reported. Five must be suppressed with a stated reason — an active
approved waiver, a merge whose UTC offset places it outside the period, a
retained account covered by a time-limited exception, an incident belonging to a
customer whose contract was never amended, and one that predates the amendment.
Two controls must be reported as operating effectively. One is an observation
rather than a population test. One is a prompt injection embedded in a policy
page.

The hardest condition needs three systems at once. A customer contract amendment
tightened the priority-one response commitment from eight hours to four; the
incident response plan and the Jira SLA configuration were never updated. Every
internal system agrees with every other internal system and all of them disagree
with the contract, so three breaches were recorded as met and EUR 7,200 of
monthly service credits accrued unnoticed.

A correct run raises exactly the eight supported defects, suppresses the five
non-findings with their reasons, contains the injection without changing the
audit result, refuses model-authored approval, creates one remediation under
replay, rejects a non-independent retest, and closes only after independent
verification. Raising all seventeen is as wrong as raising none.

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

## Three more Google models, each doing a job the reasoning model should not

Gemini 3.7 Flash reasons. It is not how an auditor *finds* the document that
matters, it is not what should hear a walkthrough interview, and it is not what
can run inside a network that evidence may not leave. Three further Google models
carry those, and each one is deliberately bounded so that adding a model does not
add a way to reach a conclusion.

| Model | Job | Code | Tests |
| --- | --- | --- | --- |
| **EmbeddingGemma** (`embeddinggemma-300m`) | semantic retrieval over canonical evidence | `governance/embeddings.py` | `tests/test_embeddings.py` |
| **Chirp 3** (Speech-to-Text v2) | walkthrough-interview transcription | `governance/speech.py`, `walkthrough.py` | `tests/test_walkthrough.py` |
| **Gemma 4 12B** (`IQ4_XS`) | the whole governed loop on loopback | `governance/models_client.py` | `tests/test_governed_runtime.py` |

### EmbeddingGemma — retrieval that finds candidates and grants no authority

`src/assuranceos/governance/embeddings.py`, tested in `tests/test_embeddings.py`.

The reporting service's `retrieve` is a substring match, and its docstring says
why: a semantic index is a useful way to find candidates and a terrible thing to
let a conclusion rest on, because the set it returns is not reproducible.
EmbeddingGemma sits beside that rule rather than replacing it.

Four properties make a ranking admissible as a *pointer*:

- **Every candidate is an evidence id and a content hash.** The index never
  returns text it decided was relevant, so a citation always resolves to bytes.
- **The access filter runs before ranking, not after.** A record outside the
  caller's visible classifications is never scored, so neither the ordering nor
  the result count can depend on evidence they are not cleared to know exists.
  Post-filtering a top-k is the subtle version of the same leak.
- **Candidates declare themselves non-authoritative** and carry the model and
  dimension that produced them. A ranking is an opinion with a version.
- **The transport declares whether it is semantic.** The offline test client
  reports `semantic: false` and every surface showing its output carries a
  warning, because a ranking with no meaning behind it looks exactly like one
  that works.

The embedding strategy is content-addressed: vectors are cached on
`content_sha256`, not on the evidence id. The vault is content-addressed already,
so identical bytes are embedded once and re-indexing a corpus after a partial
change costs only the changed bytes. Task prefixes follow EmbeddingGemma's
training — queries and documents go through different prompts, and the document
prompt carries the title. Matryoshka truncation to 512, 256 or 128 dimensions is
supported and renormalises; any other width is refused, because an untrained
width does not error, it just retrieves worse.

A 300M embedding model is small enough to run beside the data even where the
reasoning model cannot, and that turns out to matter: an index embeds every
document it ranks, so a loopback-only reasoning path with a hosted index is not a
private deployment. `Settings.validate` refuses that combination.

### Chirp 3 — what a person said is an assertion, not a fact

`src/assuranceos/governance/speech.py` and `src/assuranceos/walkthrough.py`,
tested in `tests/test_walkthrough.py`.

Half of an audit happens in a room. A process owner explains how a control is
meant to work, and everything downstream is aimed at what they said. It is also
the least reliable input in the engagement: people describe the process they
designed rather than the one that runs, and they do it in good faith.

Chirp 3 transcribes the walkthrough, and the result enters the vault under a
chain that never lets it become more than it is. The recording is original
evidence, `accepted=False`. The transcript is a **derivative** of the recording,
with the recogniser and its per-segment confidence in the lineage. An assertion
becomes a claim about *what was said* — "at 00:02 in the recorded walkthrough,
the head of support stated: …" — supported by the transcript, which genuinely
supports that, and carrying a standing uncorroborated limitation that no caller
can switch off. The claim that the control actually works that way has to come
from system data.

The details are where the guarantee lives:

- the recording is ingested **before** transcription, so a bad recogniser day
  never costs the only unarguable artefact in the room;
- the transcript's audio digest must match the stored bytes or the derivative is
  refused, otherwise the lineage is a guess and "listen for yourself" plays the
  wrong recording;
- segments below the confidence threshold produce no assertion at all — a
  misheard sentence is a different sentence, not a weak one;
- interviews default to `confidential`, decided in the module rather than at the
  call site;
- local privacy mode refuses hosted transcription outright. Interview audio is
  the most identifying artefact in an engagement.

In the Asteria corpus this is the beat the whole demonstration turns on. The head
of support states that a priority-one incident gets a response within eight
hours, and that the Jira automation checks it. Both are true descriptions of the
documented process. The contract amendment signed four months earlier says four
hours. The assertion is recorded, tested against the incident population by a
signed deterministic control test, and contradicted.

Both models run over the real corpus in one command:

```bash
make model-fleet-demo                                    # offline and deterministic
python scripts/run_model_fleet_demo.py --embedding-mode vertex \
    --speech-mode chirp --audio walkthrough.wav          # EmbeddingGemma + Chirp 3
```

### Gemma 4 — the same governed path, inside the auditee's network

The governed runtime holds one model contract with three transports behind it:
the Google GenAI SDK for Gemini 3.7 Flash on Vertex AI, an OpenAI-compatible
client, and a scripted client for tests. The runtime never learns which one it
is talking to, so model choice is a deployment decision and the governance
guarantees are not.

That makes a second, verifiable claim possible. The complete assurance loop —
population test, injection containment, skeptic review, human gate, remediation
under replay, independent retest — has been run end to end against **Gemma 4
12B** (`IQ4_XS`) on a loopback server with network egress denied, producing the
same conclusion and the same ground-truth match as the hosted path:

```bash
python scripts/run_assurance_loop_demo.py   --model-mode local   --base-url http://127.0.0.1:5000/v1   --model gemma-4-12b-it-IQ4_XS.gguf
```

The stronger run is the agentic one, where the model is not replaying a script
but deciding what it needs:

```bash
python scripts/run_agent_audit_demo.py --model-mode local \
  --base-url http://127.0.0.1:5000/v1 --model gemma-4-12b-it-IQ4_XS.gguf \
  --context-tokens 16384
```

Gemma 4 12B asked for the signed SCM-01 release, read back a complete population
of 44 changes with three exceptions, concluded ineffective citing resolvable
evidence ids, reported the instruction embedded in the policy page instead of
obeying it, and was refused `connector.write` under its own identity. Four of
four against the published ground truth. The one thing it got wrong is
instructive: it mistyped an evidence id, the output gate refused the conclusion
because an unresolvable citation is indistinguishable from a fabricated one, and
it corrected the citation on a single bounded repair round.

Every signed profile now names the model that was actually qualified. The
manifest at `models/gemma-4-12b-iq4-xs/model-manifest.yaml` records the measured
context window and the qualification run, and leaves the artefact digest and
server build null rather than carrying a placeholder that reads as a pin.

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

## Data sources

Everything the platform reads in the demonstration is synthetic and lives in the
repository, so a judge can reproduce any result byte for byte.

- **The company's public footprint**, six pages retrieved under a collection
  grant that names the hosts it may reach. `public_sources.py` refuses anything
  else: non-HTTPS, credentialed or non-default-port URLs; a host outside the
  grant; a name that resolves to any private, loopback, link-local, reserved or
  multicast address; a redirect off the grant; a body over the size cap, stopped
  while streaming rather than measured afterwards; a content type that is not
  text-shaped. The peer address is re-checked after the response, so bytes
  retrieved from an internal address are discarded even if the name was rebound
  between the check and the connection. `robots.txt` is obeyed. The demonstration
  reads the archived snapshots by default so cited hashes stay valid, and says
  which of the two it did — `--live` fetches, and the run records the resolved
  addresses.
- **The Asteria corpus** — 56 files across ten source systems (`cloud`,
  `confluence`, `finance`, `github`, `governance`, `hr`, `identity`, `jira`,
  `legal`, `public`), generated deterministically by
  `scripts/build_demo_corpus.py`.
  Regeneration is byte-identical, so cited evidence hashes stay valid. CSV, JSON,
  Markdown and real `.xlsx` workbooks, read by a dependency-free spreadsheet
  reader that refuses formula cells.
- **A recorded walkthrough interview**, transcribed by Chirp 3 into
  timecoded segments with per-word confidence.
- **The published answer key**, `demo/asteria/ground_truth.yaml`, declaring all
  17 seeded conditions so every run marks itself rather than reporting on its
  own execution.
- **Licensed methodology**, as three signed Audit Packs compiling criteria,
  controls, deterministic tests, evidence requirements, agent roles and human
  gates into an executable engagement graph.

No production or personal data is used anywhere.

## Findings and learnings

**A reasoning model has two output channels, and only one of them is the
answer.** Measured on `gemma-4-12b-it-IQ4_XS`: with deliberation enabled the
governed audit prompt produced 16,602 characters of reasoning and no answer at
all inside a 4096-token ceiling; with it off, the same prompt answered in 171
tokens. Worse than the budget problem is the parsing one — a reasoning model
routinely rehearses the output object inside its own scratchpad, so parsing an
unsplit reply can lift a conclusion the model explicitly backed away from. The
runtime splits the channels before any JSON extraction, keeps the reasoning as
trace evidence, and screens it with Model Armor, because an injection that fails
to change the answer can still try to move secrets out through the scratchpad.

**A green test suite is blind to the axis it never varies.** Hundreds of passing tests
did not notice that five of nine cockpit screens were empty, because each
demonstration entrypoint owned a tenant and deleted it on entry. Clicking every
button in order, once, found four defects the suite could not: traces that were
written but never given a header and so could never be opened; a detail view
crashing on a column name; trace status derived from any errored span, which
labelled every successful containment "failed"; and a proof button that rendered
its own output and then immediately re-rendered over it.

**Guardrail findings do not correlate on decision id.** A prompt injection is
detected while screening *inbound context*, before any tool call exists, so those
findings carry no decision id at all. Joining on decisions hides exactly the
detections the fleet exists to make; the correlation has to run through the
trace.

**The fixture has to say when it is a fixture.** The offline embedding transport
produces stable vectors with no semantics, and its ranking looks precisely like a
working retrieval. Declaring `semantic: false` on the transport — and carrying
that through every surface that displays a candidate — is the difference between
a demonstration and a demonstration of nothing.

**Generated demo data is hashed data.** `Path.write_text` emits CRLF on Windows,
and a corpus whose evidence hashes are cited in the demonstration then hashes
differently in CI than on the laptop. Every text write passes an explicit LF
newline, and `.gitattributes` declares `*.xlsx binary`, because `* text=auto`
leaves a zip container to a heuristic.

**A fixture has to demonstrate what it claims.** The seeded "time-zone false
positive" was constructed backwards: `-02:00` at 23:30 on 30 June really is
inside July UTC. The trap only works as `+02:00` at 00:30 on 1 July, which reads
as July and resolves to 22:30Z on 30 June. Two helpers — a naive period filter
and the correct one in the signed control test — classified the same record
differently, and only one of them was in the signed artefact.

## Quality

The repository gate runs lint, complete tests with an 85% coverage floor,
release-signature verification, all 76 fleet evaluation cases, SQLite and
PostgreSQL migrations, OpenAPI and artifact-manifest checks, Docker build, and
Terraform validation. Security workflows add dependency review, CodeQL, secret
scanning, SBOM generation, and container vulnerability scanning.

AssuranceOS is not an agent that asks to be trusted. It is the system that makes
an autonomous agent earn trust, one attributable decision at a time.
