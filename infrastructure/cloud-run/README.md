# Cloud Run deployment

Build the root Dockerfile and deploy the digest-pinned image through Terraform.
The Terraform module defines separate migration, demo-seed, deterministic
control-test, recurring-scheduler, and transactional-outbox Cloud Run Jobs.
See `../terraform/README.md` for the bootstrap and evidence-capture sequence.
