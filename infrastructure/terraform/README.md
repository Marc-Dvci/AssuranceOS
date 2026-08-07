# Google Cloud deployment

This Terraform module provisions the production-shaped runtime for Components 1–5:

- Cloud Run v2 API service with startup and liveness probes;
- dedicated Cloud Run migration and leased outbox-dispatch jobs;
- PostgreSQL 16 on Cloud SQL with backups and point-in-time recovery;
- explicit Cloud SQL Auth Proxy volume mounts for every database client;
- Secret Manager-backed database URL and pre-provisioned Ed25519 export and execution-signing keys;
- versioned, retention-controlled Cloud Storage evidence bucket;
- Pub/Sub outbox and dead-letter topics with the required service-agent permissions;
- Cloud Scheduler invocation of the outbox job;
- dedicated runtime and scheduler service accounts.

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

Then plan and apply:

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

Configure `ASSURANCEOS_EXECUTION_ENVELOPE_PUBLIC_KEY=/secure/execution-public.pem` when building or
deploying ADK agents. The public key is captured as trusted verification material; the private key is
mounted only into the control-plane API. The key ID must match
`ASSURANCEOS_EXECUTION_SIGNING_KEY_ID` on both sides.

Run the migration job after each release and before routing traffic to the new revision. Use a remote,
encrypted Terraform backend with locking and access logging because the generated database password is
represented in state. Lock the evidence-bucket retention policy only after governance, deletion, and
recovery tests have been approved; a locked retention policy is irreversible.
