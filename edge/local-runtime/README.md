# Local Privacy Runtime

This deployment profile runs the AssuranceOS control plane, PostgreSQL, evidence
vault, deterministic test engine, and an OpenAI-compatible `llama.cpp` model on
one internal Docker network. The network is marked `internal`, both published
ports bind only to loopback, every workload drops Linux capabilities, and the
application validates that local privacy mode targets only an explicitly allowed
model host. There is no hosted-model fallback.

## Prepare the release

1. Copy `.env.example` to `.env` outside source control and supply an immutable
   `llama.cpp` image digest and approved GGUF model.
2. Generate the export and execution Ed25519 keys with the repository scripts.
   Keep their private paths in `.env`; only read-only mounts enter the containers.
3. Set a random PostgreSQL password and an HMAC JWT secret of at least 32 bytes.
4. Start the profile from the repository root:

   ```bash
   docker compose --env-file edge/local-runtime/.env \
     -f edge/local-runtime/docker-compose.yml up --build
   ```

The product is then available at `http://127.0.0.1:8080`. The model health API is
available at `http://127.0.0.1:5000`; neither service has a route to the public
internet from its Docker network.

## Signed bundle transfer

Evidence leaves a deployment through the signed evidence-export API. On the
receiving deployment, verify and admit that export as one sealed canonical object:

```bash
python scripts/import_evidence_bundle.py bundle.zip \
  --tenant-id tnt_private --actor-id auditor@example.com \
  --public-key /keys/source-export-public.pem --key-id source-export-v1
```

Admission verifies the package digest, manifest digest, Ed25519 signature,
declared objects, every object digest and size, custody hash chains, lineage
references, and archive paths before a byte is written to the receiving vault.
The original bundle remains byte-identical and its verification result is stored
in canonical metadata.
