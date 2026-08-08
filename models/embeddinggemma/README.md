# EmbeddingGemma retrieval profile

**Model:** `embeddinggemma-300m` · **Implementation:**
[`src/assuranceos/governance/embeddings.py`](../../src/assuranceos/governance/embeddings.py)
· **Tests:** [`tests/test_embeddings.py`](../../tests/test_embeddings.py)

A non-authoritative index over canonical evidence. It is how a person finds the
record they should read; it is never what a claim resolves to. The reporting
service's `retrieve` stays a substring match for exactly that reason, and this
index sits beside it rather than replacing it.

## Where it runs

| Mode | Transport | Used for |
| --- | --- | --- |
| `vertex` | Google GenAI SDK on Vertex AI | the hosted deployment |
| `local` | OpenAI-shaped `/v1/embeddings`, e.g. `llama.cpp` | restricted engagements whose data cannot leave the network |
| `deterministic` | hashed pseudo-vectors | tests only, and it says so |

A 300M embedding model is small enough to run beside the data even where the
reasoning model cannot. That matters more than it sounds: an index embeds every
document it ranks, so a loopback-only reasoning path with a hosted index is not
a private deployment. `Settings.validate` refuses that combination rather than
documenting it.

## Mandatory properties

* **Every result resolves to source evidence.** Candidates are evidence ids and
  content hashes. The index never returns text it decided was relevant.
* **The access filter runs before ranking.** A record outside the caller's
  visible classifications is never scored, so neither the ordering nor the
  result count can depend on evidence they are not cleared to know exists.
* **Candidates declare themselves non-authoritative** and carry the model and
  dimension that produced them.
* **The transport declares whether it is semantic.** The deterministic client
  reports `semantic: false` and every surface that shows its output carries a
  warning, because a ranking with no meaning behind it looks exactly like one
  that works.
* **Ties break on evidence id**, so a cited ordering is the same ordering
  tomorrow.

## Embedding strategy

Vectors are cached on `content_sha256`, not on the evidence id. The vault is
content-addressed already, so identical bytes — the same policy attached to two
engagements, the same export re-ingested later — are embedded once, and
re-indexing a corpus after a partial change costs only the changed bytes.

Task prefixes are applied per EmbeddingGemma's training: queries and documents go
through different prompts, and the document prompt carries the title. Matryoshka
truncation to 512, 256 or 128 dimensions is supported and renormalises; any other
width is refused, since an untrained width does not error, it just retrieves
worse.

## Running it

```bash
# beside the data
python scripts/run_model_fleet_demo.py --embedding-mode local \
    --embedding-url http://127.0.0.1:5001/v1

# on Vertex AI
python scripts/run_model_fleet_demo.py --embedding-mode vertex
```

Model and runtime digests, multilingual evaluation, and the access-filter tests
are release gates before this profile is used on a real engagement.
