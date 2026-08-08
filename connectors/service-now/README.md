# service-now connector

**Status:** integration-ready remediation ticket writer

The executable contract is [`connector.contract.yaml`](connector.contract.yaml). It inherits the released connector control plane: scoped grants, isolated credentials, checkpoints, provenance, and content inspection.

Any implementation must use the shared connector SDK and cannot bypass grants, credential isolation, checkpoints, source-version checks, content inspection, or the evidence vault.
