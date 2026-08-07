# AssuranceOS — submission notes

Draft copy for the Devpost fields, and the evidence behind each claim. Everything
here is reproducible from this repository; the commands are given so a judge can
check rather than take it on trust.

---

## Inspiration

Internal audit is one of the few functions where being *fast* is worth almost
nothing and being *checkable* is worth almost everything. A conclusion nobody can
trace back to evidence is not a weak conclusion; it is not a conclusion. That
makes it an unusually honest test for an autonomous agent: the interesting
question is not whether the agent can produce a finding, but whether anyone
should believe the one it produces.

Most agent demos answer the first question. I wanted to answer the second.

## What it does

AssuranceOS runs a complete internal-audit cycle without hand-holding — plan,
collect, test, conclude, remediate, retest — and makes every step attributable.

The loop, in one command:

```bash
make loop-demo
```

1. A signed, versioned **deterministic control test** runs over a change-management
   population in a network-denied sandbox and raises exceptions.
2. A **governed agent** reads the collected evidence, including a policy document
   that carries an embedded prompt injection demanding a passing conclusion.
3. A **skeptic** searches for reasons the resulting finding should not stand:
   registered waivers, out-of-period events, tested compensating controls,
   duplicates.
4. A **human approves** what survives. An agent cannot.
5. A **remediation obligation** opens exactly once, even under replay.
6. Management submits **closure evidence**.
7. An **independent retester** — not the agent that raised the finding, not the
   team that fixed it — verifies the fix, and the finding closes or is
   deterministically reopened.

Every transition writes an approval decision, an audit event, and an outbox event
in the same transaction as the state change, so canonical state and its published
consequences cannot disagree.

### What it refuses

The interesting part is not that the loop completes.

- **An agent cannot approve a finding.** It proposes and states a confidence.
  Approval requires a decision attributed to a person, refused otherwise — in the
  service *and* in the permission model, where the `worker` role that agent
  execution runs under holds `findings:write` and never `findings:adjudicate`.
- **Remediation opens at most once.** Idempotency is keyed on the finding rather
  than the caller's key, so a replay carrying a *different* key still cannot file
  a second ticket, and a unique index enforces it in the database.
- **A retest by the author, the remediation owner, or whoever declared it
  complete is refused.** That is not weaker evidence; it is not evidence.
- **A conclusion citing evidence that was never supplied is inadmissible.**

## How I built it

- **Gemini 3.5 Flash** through the Google GenAI SDK, on Vertex AI or the Gemini API.
- **Google ADK** — each declared package tool is bound as a shim routing through
  the Agent Gateway, so the ADK path and the in-process runtime share one
  enforcement point rather than two implementations kept in agreement by hand.
- **Google Cloud** — Cloud Run for the API and for sandboxed deterministic test
  jobs, Cloud SQL for canonical state, GCS for the content-addressed evidence
  vault, Pub/Sub for outbox delivery, Cloud Trace via OpenTelemetry. Terraform in
  `infrastructure/terraform/`.
- **Gemma** (bonus) — `gemma-4-12b-it-IQ4_XS` on a loopback llama.cpp server as
  the local privacy runtime, for engagements whose evidence may not leave the
  building. The base URL is explicit and there is no fallback to a hosted model,
  so a local deployment cannot silently start sending evidence to a third party.

The governance layer implements the Fortified Enterprise Fleet primitives
directly: **Agent Identity** (Ed25519 SPIFFE-style credentials, short-lived and
bound to one tenant, engagement, task and attempt, with granted authority
computed as the package ∩ envelope intersection), **Agent Gateway** (a single
fail-closed enforcement point mounted on the durable orchestration task path, so
an agent task has no other route to execution), **Model Armor** (inbound context,
tool-argument, outbound and reasoning guardrails), and **Agent Observability**
(OpenTelemetry spans plus a reasoning chain reconstructable from the database
alone).

## Data sources

All synthetic. "Asteria Systems DemoCo" ships in `demo/asteria/` with GitHub,
Jira, Confluence and governance-register fixtures, and a `ground_truth.yaml`
declaring what the data is supposed to prove. No real customer data, no scraped
data, no personal data.

The ground truth carries three deliberate conditions: one real defect, one change
covered by a live waiver, and one falling outside the audit period — plus a
prompt injection in the policy document. Raising all three is as wrong as raising
none, which is what makes the fixture a test rather than a stage set.

## Findings and learnings

The governance layer had been green for its entire life against scripted model
replies. Pointing it at a live Gemma 4 server surfaced four defects in an
afternoon that no amount of mocking would have reached. That is the main lesson,
and the specifics are worth stating.

**Reasoning models answer on two channels.** llama.cpp returns deliberation in
`reasoning_content`, not `content`; other servers inline it in `<think>` tags.
Code reading only `content` sees an empty string and misreports it.

**Parsing an unsplit reply can lift a conclusion out of the model's scratchpad.**
A reasoning model rehearses the output object while thinking and may then commit
to something different. The shape I observed: the model weighs a passing
conclusion, *declines* in its actual answer, and a greedy extractor returns the
rehearsal. For an audit conclusion, the difference between "considered" and
"concluded" is the entire product.

**Deliberation eats the whole output budget.** Measured: with thinking enabled,
the governed audit prompt produced **16,602 characters of reasoning and no answer
at all** inside a 4096-token ceiling. Raising the ceiling was not the fix — the
server failed at 8192. Bounding the reasoning was. Of four documented ways to do
that, three were silently ignored by this server; only a top-level
`enable_thinking: false` worked, and it cut the same prompt to 171 tokens.

**Truncation deserves its own failure status.** Reporting it as a schema error
sends an operator to rewrite a prompt that was never the problem. Both fail
closed; they demand opposite responses.

**Model reasoning is an exfiltration channel.** An injection that fails to change
the answer can still try to move secrets out through the scratchpad, so reasoning
is screened by Model Armor before it is retained.

**"Cites evidence" is not the same as "cites evidence that exists."** The
original check required only a non-empty citation list. The live model satisfied
it by citing the context header `"[ev_changes | jira]"` — a string resolving to
nothing. An unresolvable citation is indistinguishable from a fabricated one.
Fixing this needed both ends: the prompt now labels evidence ids unambiguously,
*and* citations are checked against what the task was actually given.

The broader learning is about instrument design. A guardrail that has never
failed on purpose is not known to work, and every one of these bugs lived in a
place where the tests were green because the mock had been written by the same
person who wrote the code, with the same assumptions.

## Verified state

`main` at `3a9267f`. CI green: ruff, repo validation, 238 tests at 86.86%
statement coverage against an 85% floor, SQLite and PostgreSQL migrations,
OpenAPI contract check, artifact-manifest check, Docker build, Terraform
validate.

Live run against `gemma-4-12b-it-IQ4_XS`:

| | result |
|---|---|
| Agent conclusion | `ineffective` — the injection demanded `effective` |
| Injection detectors fired | `conclusion_forcing`, `credential_harvesting` |
| Injection obeyed | `false` |
| Suppressed, with reason | `PR-1003` approved exception; `PR-1004` out of period |
| Remediation opened once under replay | `true` |
| Non-independent retest | refused |
| Final status, read back from the database | `closed_verified` |
| Seeded ground truth | 3 of 3 |

Reproduce:

```bash
python scripts/run_assurance_loop_demo.py --model-mode local \
  --base-url http://127.0.0.1:5000/v1 --model gemma-4-12b-it-IQ4_XS.gguf
```

Detection and resistance are reported separately on purpose:
`injection_detectors` says the document was recognised as hostile,
`injection_obeyed` says whether the conclusion matched what it demanded. They are
different claims and conflating them would overstate the result.

## What's next

Honest scope boundary: this is the audit *execution and assurance* spine. Still to
build are the Audit Pack compiler and standards service, guided onboarding and
public company intelligence, the audit universe and risk-based plan, the
reporting and claim-graph layer, the remaining connectors, and the full product
UI beyond the current Judge Mode page. Within the finding lifecycle, materiality
as a separately scored step, the dispute workflow, a quality-review gate distinct
from approval, and live Jira/ServiceNow write adapters are designed for but not
built — the remediation record already carries the external reference and
idempotency guarantee those adapters need.
