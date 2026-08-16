# Runbook — deploying AssuranceOS to Google Cloud

End to end, from an empty project to a running deployment with a receipt that
Judge Mode will accept, and a teardown that stops the bill without taking the
demonstration offline.

Read this once before starting. Two steps have to happen in a particular order
and one of them cannot be undone cheaply.

Everything here is also automatable, and deliberately is not: the value of a
runbook for a system like this is that an operator can see each decision being
made. `infrastructure/terraform/README.md` is the reference for the module
itself; this is the sequence.

---

## 0. What you need

| | |
|---|---|
| A Google Cloud project with billing | the $150 hackathon credit is enough — see §9 |
| `gcloud`, `terraform` ≥ 1.9, `docker` | `terraform` is a single zip from releases.hashicorp.com |
| Python 3.12+ with the repository installed | `pip install -e '.[dev,cloud]'` |
| Region | this runbook uses `us-central1` throughout. Agent Engine and Model Armor are not available everywhere; if you change it, check both. |

```bash
export PROJECT_ID=your-project
export REGION=us-central1
gcloud config set project "$PROJECT_ID"

gcloud services enable \
  run.googleapis.com sqladmin.googleapis.com storage.googleapis.com \
  pubsub.googleapis.com secretmanager.googleapis.com cloudscheduler.googleapis.com \
  cloudtrace.googleapis.com artifactregistry.googleapis.com \
  aiplatform.googleapis.com modelarmor.googleapis.com \
  cloudresourcemanager.googleapis.com iam.googleapis.com
```

Enable the APIs first and separately. Terraform will otherwise fail partway
through the first apply on a service that takes a minute to become available,
and a half-applied foundation is more annoying to reason about than a slow one.

---

## 1. Signing keys — created outside Terraform, on purpose

The generated database password is represented in Terraform state. Private
signing keys must not be, so they are created here and referenced by secret id.

```bash
mkdir -p var/secure

python scripts/generate_export_signing_key.py \
  --private var/secure/export-private.pem --public var/secure/export-public.pem
python scripts/generate_execution_signing_key.py \
  --private-key var/secure/execution-private.pem --public-key var/secure/execution-public.pem

gcloud secrets create assuranceos-demo-export-signing-key --replication-policy=automatic
gcloud secrets versions add assuranceos-demo-export-signing-key \
  --data-file=var/secure/export-private.pem

gcloud secrets create assuranceos-demo-execution-signing-key --replication-policy=automatic
gcloud secrets versions add assuranceos-demo-execution-signing-key \
  --data-file=var/secure/execution-private.pem
```

`var/` is gitignored. Keep `execution-public.pem` — §7 needs it.

---

## 2. Authentication, without an identity provider

**This is the step most likely to cost you an afternoon, so it comes early.**

The API verifies bearer tokens against a JWKS document over HTTPS.
`Settings.validate` allows `disabled` or `jwt`; production forbids `disabled`;
JWKS verification refuses HS algorithms. So the deployment needs an RS256 JWKS
document, and `https://issuer.example/.well-known/jwks.json` in the module README
is a placeholder, not a requirement to run an OIDC provider.

You do not need one. A JWKS document is a static JSON file describing a public
key, and `PyJWKClient` does not care what serves it.

```bash
python scripts/make_evaluator_token.py init --out-dir var/auth

gcloud storage buckets create "gs://${PROJECT_ID}-assuranceos-auth" --location="$REGION"
gcloud storage cp var/auth/jwks.json "gs://${PROJECT_ID}-assuranceos-auth/jwks.json"
gcloud storage objects update "gs://${PROJECT_ID}-assuranceos-auth/jwks.json" \
  --add-acl-grant=entity=AllUsers,role=READER

export JWKS_URL="https://storage.googleapis.com/${PROJECT_ID}-assuranceos-auth/jwks.json"
export JWT_ISSUER="https://assuranceos.local/issuer"
export JWT_AUDIENCE="assuranceos"
curl -sf "$JWKS_URL" | head -c 120 && echo   # must return the document
```

`auth_jwt_issuer` is a stable identifier that has to match the token's `iss`. It
is never fetched, so it does not have to resolve.

Mint two tokens now, and note why there are two:

```bash
# read-only, outlives judging — this is the one that goes in the submission
python scripts/make_evaluator_token.py token --role viewer \
  --tenant tnt_asteria_demo --days 30

# can approve a finding, expires the same day — only for recording the walkthrough
python scripts/make_evaluator_token.py token --role admin --hours 6
```

A judge given a token that can write acquires the ability to change the
demonstration by clicking, which is not a courtesy to either of you.

**A `viewer` token with no `tenant_ids` returns 403, not 401.** The verifier
refuses a token with no tenant assignment unless it carries the `admin` role.
If the cockpit loads empty with a 403 in the console, that is this.

---

## 3. Bootstrap the registry, then build by digest

`container_image` is digest-only, and the repository that receives the image is
managed by the same module — so the first apply is targeted.

```bash
cd infrastructure/terraform
terraform init

terraform apply \
  -target=google_artifact_registry_repository.containers \
  -var project_id="$PROJECT_ID" -var region="$REGION" \
  -var container_image="${REGION}-docker.pkg.dev/${PROJECT_ID}/assuranceos-demo/bootstrap@sha256:0000000000000000000000000000000000000000000000000000000000000000" \
  -var auth_jwt_issuer="$JWT_ISSUER" -var auth_jwt_audience="$JWT_AUDIENCE" \
  -var auth_jwks_url="$JWKS_URL" \
  -var trusted_hosts="*.run.app" \
  -var export_signing_secret_id=assuranceos-demo-export-signing-key \
  -var execution_signing_secret_id=assuranceos-demo-execution-signing-key
```

The placeholder digest validates without being used, because a targeted apply
creates no Cloud Run resource.

```bash
cd ../..
gcloud auth configure-docker "${REGION}-docker.pkg.dev" --quiet

REPO="${REGION}-docker.pkg.dev/${PROJECT_ID}/assuranceos-demo/api"
docker build -t "$REPO:v1" .
docker push "$REPO:v1"
export IMAGE_DIGEST="$(gcloud artifacts docker images describe "$REPO:v1" --format='value(image_summary.digest)')"
export CONTAINER_IMAGE="${REPO}@${IMAGE_DIGEST}"
echo "$CONTAINER_IMAGE"
```

Before pushing, confirm the image passes the product's own gates rather than
only building — the tree and the image are different artefacts and only the tree
is covered by CI:

```bash
docker run --rm "$REPO:v1" python scripts/run_agent_evaluations.py --mode contract
# expect 19/19 agents, 76/76 cases
```

---

## 4. Full apply

```bash
cd infrastructure/terraform
terraform apply \
  -var project_id="$PROJECT_ID" -var region="$REGION" \
  -var container_image="$CONTAINER_IMAGE" \
  -var auth_jwt_issuer="$JWT_ISSUER" -var auth_jwt_audience="$JWT_AUDIENCE" \
  -var auth_jwks_url="$JWKS_URL" \
  -var trusted_hosts="*.run.app" \
  -var export_signing_secret_id=assuranceos-demo-export-signing-key \
  -var execution_signing_secret_id=assuranceos-demo-execution-signing-key \
  -var min_instances=0 -var max_instances=3 \
  -var database_tier=db-f1-micro

export API_URI="$(terraform output -raw api_uri)"
curl -sf "$API_URI/health"      # 200
curl -s  "$API_URI/ready"       # deliberately not ready until §5
```

`/ready` staying unready is correct: it reports the migration and the signed
control-test registry, and neither has run.

---

## 5. Model Armor — create the template *before* seeding, filter it *after*

The template is passed to the API **and to the Cloud Run jobs**, and
`GoogleManagedModelArmor._enforce` fails closed on any `MATCH_FOUND` — including
a sensitive-data or responsible-AI match. The Asteria corpus is full of synthetic
names, addresses, emails and contract text.

So: create the template with prompt-injection and jailbreak detection only, run
the seed, and tighten afterwards if you want the stricter filters on camera.

**Grant `roles/modelarmor.admin` first.** Model Armor is not covered by
`roles/owner`, so template administration is refused to the project owner until
that role is granted explicitly. The symptom is a flat `PERMISSION_DENIED` on
even *read* access, with the API enabled and billing active, which reads like a
broken project rather than a missing role.

```bash
gcloud projects add-iam-policy-binding "$PROJECT_ID" \
  --member="user:$(gcloud config get-value account)" \
  --role="roles/modelarmor.admin" --condition=None
```

Model Armor answers on a **regional** endpoint, and that is the one
`managed_armor.py` calls. If `gcloud model-armor` still reports
`PERMISSION_DENIED` after the grant, address the API directly rather than
debugging the project:

```bash
TOKEN="$(gcloud auth print-access-token)"
ARMOR_API="https://modelarmor.${REGION}.rep.googleapis.com/v1"

curl -s -X POST -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"filterConfig":{
        "piAndJailbreakFilterSettings":{"filterEnforcement":"ENABLED","confidenceLevel":"LOW_AND_ABOVE"},
        "maliciousUriFilterSettings":{"filterEnforcement":"ENABLED"},
        "sdpSettings":{"basicConfig":{"filterEnforcement":"DISABLED"}}}}' \
  "${ARMOR_API}/projects/${PROJECT_ID}/locations/${REGION}/templates?template_id=assuranceos-guardrails"

export ARMOR_TEMPLATE="projects/${PROJECT_ID}/locations/${REGION}/templates/assuranceos-guardrails"
```

Sensitive-data inspection is disabled in that configuration for the reason above.

**Then prove the filter fires once, and only once, before it goes near the seed.**
A guardrail only ever observed staying quiet has not been observed working, and
one that matches a benign export will stop the seed:

```bash
for f in $(find demo/asteria/sources -type f \( -name '*.md' -o -name '*.json' -o -name '*.csv' \)); do
  python -c "import json,sys;print(json.dumps({'userPromptData':{'text':open(sys.argv[1],encoding='utf-8').read()[:8000]}}))" "$f" > /tmp/p.json
  state=$(curl -s -X POST -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
    -d @/tmp/p.json "${ARMOR_API}/${ARMOR_TEMPLATE}:sanitizeUserPrompt" \
    | python -c "import json,sys;print(json.load(sys.stdin)['sanitizationResult']['filterMatchState'])")
  [ "$state" = "MATCH_FOUND" ] && echo "MATCH -> $f"
done
```

Expect exactly one line, naming `confluence/change_management_policy.md`. On
2026-08-16 that is what the 51 files produced: one match, fifty clean.

Then run the release lifecycle. Every job uses `--wait`, so a failure cannot be
mistaken for a success:

```bash
gcloud run jobs execute "$(terraform output -raw migration_job)" --region "$REGION" --wait
gcloud run jobs execute "$(terraform output -raw demo_seed_job)" --region "$REGION" --wait
gcloud run jobs execute "$(terraform output -raw deterministic_control_test_job)" --region "$REGION" --wait

curl -sf "$API_URI/ready"       # 200 now
```

Re-apply with the template to put it in the request path:

```bash
terraform apply ... -var model_armor_template="$ARMOR_TEMPLATE"
```

If the seed job fails closed on a Model Armor match, the message names the
filter. Loosen that filter, re-run the seed, and tighten again afterwards —
do not remove the template from the API service, because that is where Judge
Mode's receipt comes from.

---

## 6. Verify before spending on the fleet

Everything to this point is cheap and reversible. The Agent Engine fleet is
neither, so confirm the deployment is genuinely working first.

```bash
TOKEN=<the admin token from §2>
curl -sf -H "Authorization: Bearer $TOKEN" "$API_URI/api/v1/judge/overview" | \
  python -c "import json,sys; d=json.load(sys.stdin); print(*[(c['name'],c['status']) for c in d['components']], sep='\n')"
```

Expect the managed components to read `attention` — nothing is deployed yet —
and everything else to be `operational`. If a non-managed component is in
`attention`, fix it now.

---

## 7. Deploy the managed fleet — all nineteen, once

**`managed_fleet_proof` is all-or-nothing.** It appends *"deployment result does
not cover the complete signed fleet"* unless all 19 agents are deployed and read
back. Deploying eighteen gives you exactly the screen you get having deployed
none. Budget accordingly and run it once.

```bash
export GOOGLE_CLOUD_PROJECT="$PROJECT_ID"
export GOOGLE_CLOUD_LOCATION="$REGION"
export ASSURANCEOS_AGENT_ENGINE_STAGING_BUCKET="$(terraform output -raw agent_engine_staging_bucket)"
export ASSURANCEOS_EXECUTION_ENVELOPE_PUBLIC_KEY="$PWD/../../var/secure/execution-public.pem"
export ASSURANCEOS_MODEL_ARMOR_TEMPLATE="$ARMOR_TEMPLATE"

python scripts/deploy_adk_agent.py --plan          # dry run: 19 agents, no resources created
python scripts/deploy_adk_agent.py --output var/agent-engine-deployment-result.json
```

The script verifies Model Armor in both directions before creating anything,
enables managed Agent Identity, and reads all 19 resources back through the
Agent Engine API. Then feed the receipt to the running service:

```bash
terraform apply ... \
  -var model_armor_template="$ARMOR_TEMPLATE" \
  -var agent_engine_resource_map_json="$(python -c 'import json;print(json.dumps(json.load(open("../../var/agent-engine-deployment-result.json")),separators=(",",":")))')"
```

Re-check §6. The five managed components should now read `operational`. Judge
Mode validates the receipt — digests, managed identities, Memory Bank
confirmations, resource paths, timestamps, completeness — rather than displaying
it, so an invalid or partial receipt keeps them at `attention`.

---

## 8. Capture the evidence, while it is all up

Do this before touching anything else. Retain:

- `terraform output` in full;
- the Cloud Run service page — name, region, revision, "Serving traffic";
- `$API_URI/health` and `/ready` responses;
- the three job execution logs;
- `GET /api/v1/judge/overview` JSON with the managed components operational;
- the Agent Engine resource list (`gcloud ai reasoning-engines list --region="$REGION"`);
- one Model Armor sanitize-operation log entry;
- one Cloud Trace joined on `assuranceos.trace_id`;
- GCS evidence object metadata from the evidence bucket.

Record the walkthrough now, against `$API_URI`, while the fleet is live.

---

## 9. Teardown — split it

The Agent Engine fleet is the cost driver and is only needed once. Cloud Run
scales to zero and the smallest Cloud SQL tier is a rounding error against the
credit. So do not tear down symmetrically:

```bash
# delete the 19 reasoning engines the same day you capture the receipt.
# The deploy script creates and verifies; it does not delete, so this reads the
# resource names straight out of the receipt it wrote.
python - <<'PY' | xargs -n1 -I{} gcloud ai reasoning-engines delete {} --region="$REGION" --quiet
import json
receipt = json.load(open("var/agent-engine-deployment-result.json"))
for agent in receipt["agents"]:
    print(agent["resource_name"])
PY

gcloud ai reasoning-engines list --region="$REGION"   # confirm: empty
```

Keep `var/agent-engine-deployment-result.json`. It is the receipt, and deleting
the resources does not invalidate it — it records what was true when it was read
back.

**Leave Cloud Run and Cloud SQL running** and publish the URL. Judge Mode keeps
reporting the managed fleet as operational because it validates the *stored
receipt*; it makes no live Agent Engine call at render time. That limitation is
stated in `docs/implementation/capability-status.yaml` under `deployment_proof`
and should stay stated.

Set a budget alert before any of this:

```bash
gcloud billing budgets create --billing-account=<ACCOUNT> \
  --display-name=assuranceos --budget-amount=150USD \
  --threshold-rule=percent=0.5 --threshold-rule=percent=0.9
```

When judging is over:

```bash
cd infrastructure/terraform && terraform destroy -var ...
```

`database_deletion_protection` defaults on; set it to `false` in a prior apply or
the destroy will refuse. Do **not** lock the evidence bucket's retention policy
before you intend to keep it — a locked retention policy is irreversible.

---

## 10. After the deployment: reconcile the repository

The deployment changes what is true, and two files in the repository still say
otherwise. Both are hashed by the artifact manifest.

```bash
# docs/implementation/capability-status.yaml
#   google_platform.*.activation:      credentials_pending -> provider_verified
#   deployment_proof.current_status:   awaiting_receipt    -> provider_verified

python scripts/build_artifact_manifest.py
pytest tests/test_release_hardening.py
git commit -am "Record the verified Google Cloud activation"
```

Leaving this undone puts the repository in the position of contradicting the
running system, which is precisely the class of defect this product exists to
find.

---

## Troubleshooting

**`401 invalid bearer token`** — the token's `iss`/`aud` do not match
`auth_jwt_issuer`/`auth_jwt_audience`, or the JWKS object is not publicly
readable. `curl "$JWKS_URL"` from outside the project.

**`403 token has no tenant assignment`** — a non-`admin` token with no
`tenant_ids`. Re-mint with `--tenant tnt_asteria_demo`.

**Cloud Run rejects the request with a host error** — `trusted_hosts` is
explicit in production and must cover the `*.run.app` domain you are calling.

**The seed job fails and the log names a Model Armor filter** — §5. The template
is applied to the jobs, not only to the API.

**Judge Mode stays at `attention` after deploying** — the receipt is partial or
malformed. All 19, or nothing. Check `var/agent-engine-deployment-result.json`
covers 19 resources with read-back metadata.

**`terraform` reports a provider version mismatch at `init`** — the lock file and
`versions.tf` must move together; do not take one without the other.

**A job succeeds but `/ready` stays unready** — run them in order. `/ready`
reports the migration *and* the signed control-test registry.
