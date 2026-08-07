# Known limitations — Risk and Audit Portfolio Agent

- The signed package proves artifact integrity and policy completeness; it does not by itself qualify every hosted or local model/runtime combination. Each allowed profile must meet the evaluation thresholds declared in `evaluations.yaml`.
- Live provider and hosted-model execution requires deployment credentials, active collection grants, tenant authorization, and source-health checks. Local tests use deterministic fixtures while enforcing the same evidence and policy contracts.
- Missing, stale, contradictory, out-of-period, or scope-inaccessible evidence must produce an explicit unknown, limitation, or escalation rather than a fabricated conclusion.
- Consequential professional judgments remain subject to the human gates and accountable owners declared in `manifest.yaml`.
- This role cannot exceed its declared authority. In particular, it must not: conclude control effectiveness from public information, declare uncited legal obligations, approve its own plan recommendations.
