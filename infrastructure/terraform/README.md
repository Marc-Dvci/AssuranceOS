# Google Cloud deployment

This Terraform module provisions the production-shaped runtime for Components 1–5:

- Cloud Run v2 API service with startup and liveness probes;
- dedicated Cloud Run migration, demo-seed, deterministic control-test,
  recurring-scheduler, and leased outbox-dispatch jobs;
- PostgreSQL 16 on Cloud SQL with backups and point-in-time recovery;
- explicit Cloud SQL Auth Proxy volume mounts for every database client;
- Secret Manager-backed database URL and pre-provisioned Ed25519 export and execution-signing keys;
- versioned, retention-controlled Cloud Storage evidence bucket;
- Pub/Sub outbox and dead-letter topics with the required service-agent permissions;
- Cloud Scheduler invocation of the outbox job;
- dedicated runtime and scheduler service accounts.
- a Docker Artifact Registry repository and least-privilege Model Armor caller role.

The container image must be pinned by digest. Generate signing keys outside the repository and create
both secrets outside Terraform so private key material is never written into Terraform state:

```bash
python scripts/generate_export_signing_key.py \
  --private /secure/export-private.pem \
  --public /secure/export-public.pem
python scripts/generate_execution_signing_key.py \
  --private-key /secure/execution-private.pem \
  --public-key /secure/execution-public.pem

gcloud secrets create assuranceos-demo-export-signing-key --replication-policy=automatic
gcloud secrets versions add assuranceos-demo-export-signing-key \
  --data-file=/secure/export-private.pem

gcloud secrets create assuranceos-demo-execution-signing-key --replication-policy=automatic
gcloud secrets versions add assuranceos-demo-execution-signing-key \
  --data-file=/secure/execution-private.pem
```

## Bootstrap and deploy

The image input is digest-only, while the repository that receives it is itself
managed here. Bootstrap the APIs and repository once:

```bash
terraform init
terraform apply \
  -target=google_artifact_registry_repository.containers \
  -var project_id=my-project \
  -var container_image=us-central1-docker.pkg.dev/my-project/assuranceos-demo/bootstrap@sha256:0000000000000000000000000000000000000000000000000000000000000000 \
  -var auth_jwt_issuer=https://issuer.example/ \
  -var auth_jwt_audience=assuranceos \
  -var auth_jwks_url=https://issuer.example/.well-known/jwks.json \
  -var trusted_hosts=assurance.example.com \
  -var export_signing_secret_id=assuranceos-demo-export-signing-key \
  -var execution_signing_secret_id=assuranceos-demo-execution-signing-key
```

The placeholder digest is validation-only because the targeted apply does not
create a Cloud Run resource. Configure Docker authentication, build, push, and
resolve the registry-reported digest. Then perform the full apply:

```bash
terraform init
terraform plan \
  -var project_id=my-project \
  -var container_image=us-central1-docker.pkg.dev/my-project/assuranceos/api@sha256:... \
  -var auth_jwt_issuer=https://issuer.example/ \
  -var auth_jwt_audience=assuranceos \
  -var auth_jwks_url=https://issuer.example/.well-known/jwks.json \
  -var trusted_hosts=assurance.example.com \
  -var export_signing_secret_id=assuranceos-demo-export-signing-key \
  -var execution_signing_secret_id=assuranceos-demo-execution-signing-key
terraform apply
```

The first API revision starts against `/health`; `/ready` deliberately remains
unready until the migration and signed control-test registry job completes. Run
the release lifecycle in this order (all commands use `--wait` so a failed job
cannot be mistaken for a successful deployment):

```bash
gcloud run jobs execute "$(terraform output -raw migration_job)" --region us-central1 --wait
gcloud run jobs execute "$(terraform output -raw demo_seed_job)" --region us-central1 --wait
gcloud run jobs execute "$(terraform output -raw deterministic_control_test_job)" --region us-central1 --wait
```

The seed job keeps the published Asteria fixture corpus as the reproducible demo
population, stores its evidence in the configured GCS bucket, and uses Vertex AI
for the model-driven stages.

## Managed agent and Model Armor activation

Create a Model Armor template with prompt-injection/jailbreak, malicious-URI,
sensitive-data, and responsible-AI filters, with sanitize-operation logging
enabled. Pass its full resource name as `model_armor_template`. The runtime
service account already has the least-privilege `roles/modelarmor.user` role;
the adapter fails closed if sanitization errors or returns an unknown state.

Deploy and read every ADK resource back through the Agent Engine API:

```bash
export GOOGLE_CLOUD_PROJECT=my-project
export GOOGLE_CLOUD_LOCATION=us-central1
export ASSURANCEOS_AGENT_ENGINE_STAGING_BUCKET="$(terraform output -raw agent_engine_staging_bucket)"
export ASSURANCEOS_EXECUTION_ENVELOPE_PUBLIC_KEY=/secure/execution-public.pem
uv run --extra agent-cloud python scripts/deploy_adk_agent.py \
  --output var/agent-engine-deployment-result.json
```

Supply the compact contents of that result as
`agent_engine_resource_map_json` in the next Terraform apply. Judge Mode will
then validate all 19 digests, managed identities, Memory Bank confirmations,
resource paths, timestamps, completeness, and live read-back metadata. It stays
at `attention` when the value is empty or invalid.

For evaluator evidence, retain the Terraform outputs, Cloud Run revision and job
execution URLs/logs, `/health` and `/ready` responses, the Judge Mode JSON, the
Agent Engine resource list, one Model Armor sanitize log, one Cloud Trace joined
to its canonical `assuranceos.trace_id`, and GCS evidence object metadata.

Configure `ASSURANCEOS_EXECUTION_ENVELOPE_PUBLIC_KEY=/secure/execution-public.pem` when building or
deploying ADK agents. The public key is captured as trusted verification material; the private key is
mounted only into the control-plane API. The key ID must match
`ASSURANCEOS_EXECUTION_SIGNING_KEY_ID` on both sides.

Run the migration job after each release and before routing traffic to the new revision. Use a remote,
encrypted Terraform backend with locking and access logging because the generated database password is
represented in state. Lock the evidence-bucket retention policy only after governance, deletion, and
recovery tests have been approved; a locked retention policy is irreversible.
