# AssuranceOS 0.8 build report

Release status: qualified

AssuranceOS 0.8 delivers the complete assurance lifecycle in one deployable
control plane: organization onboarding, risk portfolio planning, signed
methodology compilation, durable engagements, evidence collection, deterministic
testing, governed agents, findings, remediation, independent retest, reporting,
and continuous monitoring.

## Release inventory

- 19 Ed25519-signed Agent Definition Packages using Gemini 3.6 Flash.
- 76 release-qualification cases across golden, adversarial,
  missing-evidence, and cross-industry scenarios.
- 3 signed Audit Packs and 2 signed deterministic control-test releases.
- Canonical SQLAlchemy domain model with Alembic migrations.
- Google ADK and Vertex AI Agent Engine deployment with Memory Bank policy.
- Responsive operator cockpit and evaluator-specific Judge Mode.
- Cloud Run, Cloud SQL, Cloud Storage, Pub/Sub, Secret Manager, Cloud Trace,
  and Agent Platform Terraform.

## Release gates

The build is accepted only when lint, complete tests, the coverage floor,
signature validation, fleet qualification, migrations, OpenAPI generation,
artifact-manifest verification, frontend syntax, Docker build, infrastructure
validation, dependency audit, CodeQL, SBOM generation, and container scanning
pass.

Run the local release gate:

    make release-check

Generate the cloud deployment plan:

    python scripts/deploy_adk_agent.py --plan
