# Handoff — what is done, and what is left

Written 2026-08-07, after Components 7 (completed), 8, 10 and 11 landed. This
supersedes the "Remaining product scope" section of
`ASSURANCEOS_DEVELOPER_HANDOFF.md` for everything it covers; that document is
still correct about Components 1–6 and the governance layer.

**State:** `main` at `965a3c0`, **420 tests**, coverage ~88%, 13 migrations, ruff
clean, `validate_repo.py` green, OpenAPI and artifact manifest current.

---

## 1. What now exists that did not before

The platform now covers a complete audit lifecycle, and each stage refuses
something specific. Read the four architecture documents before changing any of
it — each opens with the rule the component exists to enforce.

| stage | component | doc |
|---|---|---|
| plan | 10 — audit universe, risk scoring, portfolio planning | `docs/architecture/risk-assessment-and-portfolio-planning.md` |
| scope | 8 — standards, criteria, Audit Pack compiler | `docs/architecture/standards-and-audit-pack-compilation.md` |
| execute | 1–6 + G (pre-existing) | `docs/architecture/agent-governance-and-assurance-loop.md` |
| conclude | 7 — materiality, quality review, disputes, ticketing | `docs/architecture/assurance-review-gates.md` |
| report | 11 — retrieval, claim graph, fail-closed reporting | **not yet written — see §3** |

Five demonstrations, all offline and deterministic:

```bash
make portfolio-demo   # risk scoring -> plan -> approval with accepted residual
make pack-demo        # signed pack -> engagement DAG, plus six refusals
make loop-demo        # exception -> finding -> remediation -> retest -> closed
make reporting-demo   # six unpublishable reports, then a publishable one
make governance-demo  # the governed agent runtime
```

`make loop-demo --model-mode local` still drives the governed runtime against the
local llama.cpp server at `http://127.0.0.1:5000/v1`.

### Things worth knowing before you touch them

- **Audit Packs now use their own release key.** `security/release-keys/audit-pack-release-public.pem`.
  The private half is at `var/release-keys/audit-pack-release-private.pem`
  (gitignored). **If you change any file inside `audit-packs/<pack>/` you must
  re-release it**, or the registry refuses to load and every test fails:
  ```bash
  python scripts/release_audit_pack.py audit-packs/<pack> \
      --private-key var/release-keys/audit-pack-release-private.pem \
      --key-id assuranceos-audit-pack-v1 --released-at 2026-08-07T18:00:00+00:00
  ```
  This bit me twice — editing a pack README silently invalidates its signature.
- **`validate_repo.py` now scans `git ls-files` for private keys, not the whole
  tree.** Otherwise the legitimate signing key in `var/` trips it.
- **The artifact manifest asserts it equals `git ls-files` exactly.** New files
  must be `git add`-ed *before* `build_artifact_manifest.py`, or
  `test_release_hardening` fails.
- **Approval of a finding now has preconditions.** Any test or script that calls
  `AdjudicationService.adjudicate(APPROVE)` must first call `assess_materiality`
  and `review_quality` with a reviewer who is neither the author nor the
  approver. See `clear_gates` in `tests/test_adjudication.py`.

---

## 2. Remaining components, in the order I would do them

### Component 15 — product UI and Judge Mode  ← **do this first**

Highest marginal value for the judging criteria, and the only thing standing
between a strong backend and a demonstrable product. `apps/web/judge.html` is a
minimal three-action page; everything below it now exists as API.

Build, roughly in this order:

1. **Judge Mode** — one page, backed by real endpoints, no static success text:
   deterministic reset, golden engagement launch, prompt-injection replay,
   idempotency replay, ground-truth comparison, trace navigation, and a footer
   showing cloud project / region / revision / model version / commit.
2. **Product routes** — Home, Plan (`/plan-proposals`), Audits, Findings
   (`approval_blockers` is already computed for the UI), Evidence, Standards
   (`/audit-packs`, `/standards/.../impact`), Governance, Reporting.
3. **Source-backed claim cards** — the reporting document already carries an
   `evidence_index` with digests; drill-down is a rendering job, not a backend one.

Everything the UI needs is in `api/openapi/openapi.yaml`.

### Component 12, cloud half — Vertex Agent Engine + evaluation harness

The governance requirements are done. What remains:

- register the 19-agent fleet on Vertex AI Agent Engine;
- wire the remaining agent roles to bounded domain tools through the gateway
  (only `operating-effectiveness` has real tools today);
- **build the evaluation runner** over `agents/*/golden_cases`,
  `adversarial_cases` and `cross_industry_cases` with release thresholds. This
  half is doable locally against the llama.cpp server and is worth doing even
  without GCP — 19 agents × three case classes is a lot of unexercised evidence
  sitting in the repo.

### Component 13 — connectors and continuous monitoring

- **Live Jira/ServiceNow writers** exist and are tested against recorded
  transports (`adjudication/ticketing.py`). What is missing is wiring a
  credentialed writer into `sync_remediation_ticket` at the API layer — it
  currently refuses rather than filing nowhere, which is honest but limited.
- Okta/Entra and Google Cloud IAM read connectors.
- Continuous-monitor definitions that rerun released deterministic tests,
  deduplicate alerts, and suspend conclusions when source freshness degrades.
  **Never convert an alert into an approved finding** — the human gate applies.

### Component 9 — onboarding and company intelligence

Lowest value of the remaining set and the most speculative (allowlisted public-web
egress). `OrganizationProfile` / `OrganizationFact` models already exist with a
`claim_type` column for the observed/proposed/inference/assertion/unknown
distinction. If you build it, the guardrail that matters is: **search snippets are
discovery aids, never canonical evidence.**

### Component 14 — local privacy runtime

Mostly already true — `ASSURANCEOS_MODEL_MODE=local` with an explicit base URL and
no hosted fallback. What is missing is packaging it as a deployment profile:
compose file with loopback-only gateway, explicit outbound denial, signed bundle
import/export, degraded-capability indicators.

---

## 3. Known gaps in what I delivered

Stated plainly so they are not mistaken for finished work.

- **Component 11 has no architecture document.** The other three do. Write
  `docs/architecture/evidence-grounded-reporting.md` following the same shape:
  the rule, the mermaid diagram, the refusal table, and a "what this does not do"
  section. The material is all in the module docstrings.
- **Jira and ServiceNow writers have never touched a live tenant.** The adapter
  code paths are the real ones; provider-side field validation, permission
  schemes and custom workflows are unverified.
- **`GeminiClient` is still unverified against a real Gemini endpoint.** It is the
  mandated model path. Verify it first once a GCP project exists.
- **Nothing is deployed.** Terraform validates and has never been applied. The
  OTel bridge is verified against an in-memory exporter, not Cloud Trace.
- **The compiler's `PLATFORM_VERSION` is a literal**, so a deployment running older
  code than it claims is not detected.
- **Plan candidates are supplied, not generated.** Effort and disruption estimates
  come from the caller; the plan is only as good as those declarations.
- **Approving a plan does not create schedules.** It creates an approved
  `AuditPlan`; hanging schedules off it is still a manual step.
- **The audit-universe graph is thin.** Entity relationships exist but nothing
  propagates risk along them.
- **Retrieval is substring, not semantic.** That is deliberate — a set a
  conclusion rests on has to be reproducible — but it does mean finding candidate
  evidence in a large corpus is weak.
- **`procure-to-pay` remains contract-defined**: a README, no `pack.yaml`, and
  therefore no claim that it runs.

---

## 4. Still owed by the owner

Unchanged from the previous handoff:

1. **A Google Cloud project** with Vertex AI enabled and `gcloud` authenticated.
   Everything cloud-facing is blocked on this.
2. **An actual deployment.** Judges want visible proof it ran on Google Cloud.
3. **The ~4-minute demo video.**

---

## 5. Release checklist

```bash
ruff check src scripts tests migrations
python scripts/validate_repo.py
pytest -q
pytest --cov=assuranceos --cov-report=term-missing --cov-fail-under=85
python scripts/migrate.py
python scripts/generate_openapi.py && python scripts/generate_openapi.py --check
git add -A
python scripts/build_artifact_manifest.py && python scripts/build_artifact_manifest.py --check
```

Run all five demonstrations. If a pack changed, re-release it first (§1).
