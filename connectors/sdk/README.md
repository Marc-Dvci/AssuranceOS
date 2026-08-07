# sdk connector

**Status:** `implemented`

The executable contract is [`connector.contract.yaml`](connector.contract.yaml). The implementation is in `src/assuranceos/connectors`.

Any implementation must use the shared connector SDK and cannot bypass grants, credential isolation, checkpoints, source-version checks, content inspection, or the evidence vault.
