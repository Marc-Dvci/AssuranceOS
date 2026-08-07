# test-runner

**Implementation status:** `implemented`

The deployment boundary is implemented by `src/assuranceos/control_testing/runtime.py`,
`service.py`, and `worker.py`. It supports bounded network-denied Python subprocesses, read-only SQL,
population reconciliation, deterministic sampling, typed results, exception records, manifests, and
reproducibility verification.

Production deployments should run the same package contract in an isolated Cloud Run Job with no
outbound network route and a read-only filesystem.
