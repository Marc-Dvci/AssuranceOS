"""EmbeddingGemma retrieval: how a person finds candidates, not how a claim resolves.

The reporting service already draws the line this module lives behind. Its
``retrieve`` is a substring match, and its docstring says why: a semantic index is
a useful way to *find* candidates and a terrible thing to let a conclusion rest
on, because the set it returns is not reproducible. Nothing here changes that.
What it adds is the finding.

Three transports behind one contract, mirroring :mod:`models_client`:

* :class:`VertexEmbeddingClient` — EmbeddingGemma through the Google GenAI SDK,
  on Vertex AI or the Gemini API.
* :class:`LocalEmbeddingClient` — any OpenAI-shaped ``/v1/embeddings`` endpoint,
  which is how EmbeddingGemma runs inside an auditee's network on ``llama.cpp``.
  A population that cannot leave the network cannot be indexed by a hosted model
  either, so the retrieval index has to have the same local profile the reasoning
  model does.
* :class:`DeterministicEmbeddingClient` — stable pseudo-embeddings for tests. It
  is never a fallback: :func:`build_embedding_client` will not resolve to it by
  accident, because an index that silently stops being semantic is worse than one
  that fails.

Three properties make the result admissible as a *pointer*:

1. Every candidate is an evidence id. The index returns nothing that is not
   already a canonical record, so a citation always resolves to bytes.
2. The access filter runs before ranking, not after. A record the caller may not
   see is never scored, so nearest-neighbour distance cannot leak the existence
   of a classification the caller is not cleared for.
3. Candidates are marked non-authoritative and carry the model and dimension
   that produced them. A ranking is an opinion with a version.
"""

from __future__ import annotations

import hashlib
import math
import os
import struct
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

#: EmbeddingGemma, Google's 300M open embedding model. Small enough to sit beside
#: the audit data rather than the other way round.
DEFAULT_EMBEDDING_MODEL = "embeddinggemma-300m"

#: EmbeddingGemma is trained with task prefixes and measurably degrades without
#: them: the query and the document it should match are deliberately embedded
#: through different prompts. Getting this wrong produces an index that works
#: well enough to look fine and badly enough to miss the record you needed.
QUERY_PROMPT = "task: search result | query: {content}"
DOCUMENT_PROMPT = "title: {title} | text: {content}"

#: Matryoshka representation: the 768-dimension output can be truncated to 512,
#: 256, or 128 and renormalised, trading a little recall for a quarter of the
#: storage and distance-computation cost. Truncating to an untrained dimension
#: just degrades quietly, so the supported set is explicit.
SUPPORTED_DIMENSIONS = (768, 512, 256, 128)


class EmbeddingError(RuntimeError):
    """The index could not be built or queried. Always fails closed."""


@dataclass(frozen=True)
class EmbeddingBatch:
    """Vectors for one call, with the provenance needed to reproduce them."""

    vectors: tuple[tuple[float, ...], ...]
    model: str
    dimensions: int
    prompt: str

    def __len__(self) -> int:
        return len(self.vectors)


class EmbeddingClient(Protocol):
    model_name: str
    #: Whether this transport produces vectors that mean anything. False for the
    #: test fixture. It is on the contract rather than inferred from the model
    #: name because a ranking with no semantics behind it looks exactly like one
    #: that works, and every surface that shows candidates has to be able to say
    #: which it is holding.
    semantic: bool

    def embed(
        self,
        texts: Sequence[str],
        *,
        task: str = "document",
        titles: Sequence[str | None] | None = None,
    ) -> EmbeddingBatch: ...


def _prompts(
    task: str, texts: Sequence[str], titles: Sequence[str | None] | None
) -> list[str]:
    resolved = list(titles or [None] * len(texts))
    if len(resolved) != len(texts):
        raise EmbeddingError("titles must line up with texts")
    return [_prompt_for(task, text, title) for text, title in zip(texts, resolved)]


def _prompt_for(task: str, text: str, title: str | None) -> str:
    if task == "query":
        return QUERY_PROMPT.format(content=text)
    if task == "document":
        return DOCUMENT_PROMPT.format(title=title or "none", content=text)
    raise EmbeddingError(f"unknown embedding task: {task!r}")


def _truncate(vector: Sequence[float], dimensions: int) -> tuple[float, ...]:
    """Matryoshka truncation. Renormalising afterwards is not optional.

    A truncated prefix is no longer a unit vector, and cosine similarity computed
    over unnormalised vectors silently becomes a dot product that rewards length.
    """
    if dimensions >= len(vector):
        return tuple(float(value) for value in vector)
    head = [float(value) for value in vector[:dimensions]]
    norm = math.sqrt(sum(value * value for value in head))
    if norm == 0.0:
        return tuple(head)
    return tuple(value / norm for value in head)


@dataclass
class DeterministicEmbeddingClient:
    """Stable pseudo-embeddings derived from token hashes. Test profile only.

    It gives the index a reproducible shape to test against — access filtering,
    caching, truncation, ordering — without a model server. It has no semantics:
    synonyms are as far apart as unrelated words. Nothing may resolve to it
    outside tests, which is why ``build_embedding_client`` requires the caller to
    name it.
    """

    model_name: str = "deterministic-fixture"
    dimensions: int = 256
    semantic: bool = False
    calls: list[str] = field(default_factory=list)

    def embed(
        self,
        texts: Sequence[str],
        *,
        task: str = "document",
        titles: Sequence[str | None] | None = None,
    ) -> EmbeddingBatch:
        vectors: list[tuple[float, ...]] = []
        for prompt in _prompts(task, texts, titles):
            self.calls.append(prompt)
            vectors.append(self._vector(prompt))
        return EmbeddingBatch(
            vectors=tuple(vectors),
            model=self.model_name,
            dimensions=self.dimensions,
            prompt=task,
        )

    def _vector(self, text: str) -> tuple[float, ...]:
        accumulator = [0.0] * self.dimensions
        for token in _tokenize(text):
            digest = hashlib.sha256(token.encode("utf-8")).digest()
            for index in range(self.dimensions):
                # Four bytes per dimension, cycling the digest. Deterministic
                # across processes and platforms, which a Python hash is not.
                offset = (index * 4) % (len(digest) - 4)
                (raw,) = struct.unpack_from(">i", digest, offset)
                accumulator[index] += raw / 2**31
        norm = math.sqrt(sum(value * value for value in accumulator))
        if norm == 0.0:
            return tuple(accumulator)
        return tuple(value / norm for value in accumulator)


@dataclass
class LocalEmbeddingClient:
    """EmbeddingGemma on an OpenAI-shaped ``/v1/embeddings`` endpoint.

    The base URL is explicit and there is no hosted fallback, so an engagement
    running under the local privacy profile cannot start shipping evidence text
    to a third party because a local server went down. It fails instead.
    """

    base_url: str = "http://127.0.0.1:5001/v1"
    model_name: str = DEFAULT_EMBEDDING_MODEL
    api_key: str = "not-needed"
    semantic: bool = True
    dimensions: int = 768
    timeout_seconds: float = 120.0

    def embed(
        self,
        texts: Sequence[str],
        *,
        task: str = "document",
        titles: Sequence[str | None] | None = None,
    ) -> EmbeddingBatch:
        import httpx

        prompted = _prompts(task, texts, titles)
        response = httpx.post(
            f"{self.base_url.rstrip('/')}/embeddings",
            json={"model": self.model_name, "input": prompted},
            headers={"Authorization": f"Bearer {self.api_key}"},
            timeout=self.timeout_seconds,
        )
        response.raise_for_status()
        body = response.json()
        rows = body.get("data") or []
        if len(rows) != len(texts):
            raise EmbeddingError(
                f"embedding endpoint returned {len(rows)} vectors for {len(texts)} inputs"
            )
        # The server returns rows in request order in practice and declares an
        # index in the contract. Sort by the declared index rather than trusting
        # the order, because a mismatched pairing here silently attributes one
        # document's meaning to another.
        rows = sorted(rows, key=lambda row: int(row.get("index", 0)))
        vectors = tuple(
            _truncate(row.get("embedding") or [], self.dimensions) for row in rows
        )
        if any(not vector for vector in vectors):
            raise EmbeddingError("embedding endpoint returned an empty vector")
        return EmbeddingBatch(
            vectors=vectors,
            model=str(body.get("model") or self.model_name),
            dimensions=len(vectors[0]),
            prompt=task,
        )


@dataclass
class VertexEmbeddingClient:
    """EmbeddingGemma through the Google GenAI SDK, on Vertex AI or the Gemini API."""

    model_name: str = DEFAULT_EMBEDDING_MODEL
    project: str | None = None
    location: str = "us-central1"
    dimensions: int = 768
    semantic: bool = True
    use_vertex: bool | None = None
    _client: Any = None

    def __post_init__(self) -> None:
        if self.use_vertex is None:
            self.use_vertex = bool(self.project or os.getenv("GOOGLE_CLOUD_PROJECT"))

    def _ensure_client(self) -> Any:
        if self._client is not None:
            return self._client
        try:
            from google import genai
        except ImportError as exc:  # pragma: no cover - optional integration
            raise EmbeddingError(
                "Install the agent-cloud extra to use EmbeddingGemma on Vertex AI: "
                "pip install -e '.[agent-cloud]'"
            ) from exc
        if self.use_vertex:
            self._client = genai.Client(
                vertexai=True,
                project=self.project or os.getenv("GOOGLE_CLOUD_PROJECT"),
                location=self.location
                or os.getenv("GOOGLE_CLOUD_LOCATION", "us-central1"),
            )
        else:
            self._client = genai.Client(api_key=os.getenv("GOOGLE_API_KEY"))
        return self._client

    def embed(
        self,
        texts: Sequence[str],
        *,
        task: str = "document",
        titles: Sequence[str | None] | None = None,
    ) -> EmbeddingBatch:  # pragma: no cover - optional integration
        client = self._ensure_client()
        from google.genai import types

        prompted = _prompts(task, texts, titles)
        response = client.models.embed_content(
            model=self.model_name,
            contents=prompted,
            config=types.EmbedContentConfig(output_dimensionality=self.dimensions),
        )
        vectors = tuple(
            _truncate(item.values or [], self.dimensions)
            for item in (response.embeddings or [])
        )
        if len(vectors) != len(texts):
            raise EmbeddingError(
                f"embedding model returned {len(vectors)} vectors for {len(texts)} inputs"
            )
        return EmbeddingBatch(
            vectors=vectors,
            model=self.model_name,
            dimensions=len(vectors[0]) if vectors else self.dimensions,
            prompt=task,
        )


def build_embedding_client(
    mode: str,
    *,
    model: str | None = None,
    base_url: str | None = None,
    project: str | None = None,
    location: str | None = None,
    dimensions: int | None = None,
) -> EmbeddingClient:
    """Resolve an embedding client from configuration. Unknown modes fail closed."""
    normalized = (mode or "").strip().lower()
    resolved_dimensions = dimensions or int(
        os.getenv("ASSURANCEOS_EMBEDDING_DIMENSIONS", "768")
    )
    if resolved_dimensions not in SUPPORTED_DIMENSIONS:
        raise EmbeddingError(
            f"EmbeddingGemma supports {SUPPORTED_DIMENSIONS}, not {resolved_dimensions}"
        )
    if normalized in {"vertex", "gemini"}:
        return VertexEmbeddingClient(
            model_name=model or DEFAULT_EMBEDDING_MODEL,
            project=project,
            location=location or "us-central1",
            dimensions=resolved_dimensions,
            use_vertex=normalized == "vertex" or None,
        )
    if normalized in {"local", "openai-compatible", "llamacpp"}:
        return LocalEmbeddingClient(
            base_url=base_url
            or os.getenv("ASSURANCEOS_EMBEDDING_URL", "http://127.0.0.1:5001/v1"),
            model_name=model
            or os.getenv("ASSURANCEOS_EMBEDDING_MODEL", DEFAULT_EMBEDDING_MODEL),
            dimensions=resolved_dimensions,
        )
    if normalized in {"deterministic", "mock", "test"}:
        return DeterministicEmbeddingClient(dimensions=resolved_dimensions)
    raise EmbeddingError(f"unknown embedding mode: {mode!r}")


# -- the index ---------------------------------------------------------------


@dataclass(frozen=True)
class IndexedDocument:
    """One canonical evidence record, as the index sees it."""

    evidence_id: str
    content_sha256: str
    text: str
    classification: str = "internal"
    engagement_id: str | None = None
    title: str | None = None
    source_locator: str | None = None


@dataclass(frozen=True)
class EvidenceCandidate:
    """A pointer to evidence, with the provenance of the pointer itself.

    ``authoritative`` is fixed at ``False`` and is not a parameter. It exists so
    that a candidate carried into a report, a prompt, or a log is self-describing:
    a reader who only ever sees this object still knows the ranking is a search
    result and the evidence id is the thing to check.
    """

    evidence_id: str
    score: float
    content_sha256: str
    classification: str
    source_locator: str | None
    model: str
    dimensions: int
    authoritative: bool = False


def cosine_similarity(left: Sequence[float], right: Sequence[float]) -> float:
    if len(left) != len(right):
        raise EmbeddingError(
            f"cannot compare vectors of {len(left)} and {len(right)} dimensions"
        )
    dot = sum(a * b for a, b in zip(left, right))
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    if left_norm == 0.0 or right_norm == 0.0:
        return 0.0
    return dot / (left_norm * right_norm)


class SemanticEvidenceIndex:
    """Content-addressed semantic index over canonical evidence.

    The cache is keyed on ``content_sha256`` rather than on the evidence id,
    which is the whole embedding strategy in one sentence. The vault is
    content-addressed already, so the same bytes collected twice — the same
    policy attached to two engagements, the same export re-ingested a month
    later — are one vector, computed once, and re-indexing a corpus after a
    partial change costs only the changed bytes. It also means the cache can be
    persisted and shared without carrying tenant identifiers, because a hash of
    the content is not a reference to whose content it was.
    """

    def __init__(
        self,
        client: EmbeddingClient,
        *,
        batch_size: int = 32,
    ) -> None:
        self.client = client
        self.batch_size = max(1, batch_size)
        self._vectors: dict[str, tuple[float, ...]] = {}
        self._documents: dict[str, IndexedDocument] = {}
        self._model: str = getattr(client, "model_name", "unknown")
        self._dimensions: int = 0
        # Absent rather than defaulted to True: a client that forgot to declare
        # itself is treated as meaningless, which is the safe direction.
        self._semantic: bool = bool(getattr(client, "semantic", False))
        self.embed_calls = 0
        self.cache_hits = 0

    @property
    def model(self) -> str:
        return self._model

    @property
    def dimensions(self) -> int:
        return self._dimensions

    def __len__(self) -> int:
        return len(self._documents)

    def index(self, documents: Iterable[IndexedDocument]) -> int:
        """Embed and store documents. Returns the number of vectors computed.

        Documents whose bytes are already in the cache are registered without a
        model call, so the return value is the real cost of the operation rather
        than the size of the corpus.
        """
        pending: list[IndexedDocument] = []
        for document in documents:
            self._documents[document.evidence_id] = document
            if document.content_sha256 in self._vectors:
                self.cache_hits += 1
                continue
            if any(item.content_sha256 == document.content_sha256 for item in pending):
                # Two records, identical bytes, first sighting. Embed once.
                self.cache_hits += 1
                continue
            pending.append(document)

        computed = 0
        for start in range(0, len(pending), self.batch_size):
            batch = pending[start : start + self.batch_size]
            result = self.client.embed(
                [item.text for item in batch],
                task="document",
                titles=[item.title for item in batch],
            )
            if len(result) != len(batch):
                raise EmbeddingError("embedding client returned a mismatched batch")
            self._model = result.model
            self._dimensions = result.dimensions
            for item, vector in zip(batch, result.vectors):
                self._vectors[item.content_sha256] = vector
            computed += len(batch)
        self.embed_calls += computed
        return computed

    def search(
        self,
        query: str,
        *,
        visible_classifications: Sequence[str],
        engagement_id: str | None = None,
        limit: int = 10,
        minimum_score: float = -1.0,
    ) -> list[EvidenceCandidate]:
        """Rank visible evidence against a query.

        ``visible_classifications`` is required rather than defaulted. A retrieval
        surface whose access filter has a default is one refactor away from being
        called without one, and the failure mode is silent: the caller gets a
        useful answer that happens to include a record they were not cleared to
        know exists.

        ``minimum_score`` defaults to no floor. A similarity threshold is a
        judgement about a particular model and corpus, and a default one would be
        an unowned, invisible decision to withhold records — the opposite of what
        this surface is for. Callers that want a floor state it.
        """
        visible = frozenset(visible_classifications)
        if not visible:
            return []
        # Filter first. A record outside the caller's view is never scored, so no
        # ranking, score distribution, or result count can depend on it.
        eligible = [
            document
            for document in self._documents.values()
            if document.classification in visible
            and (engagement_id is None or document.engagement_id == engagement_id)
            and document.content_sha256 in self._vectors
        ]
        if not eligible:
            return []

        query_vector = self.client.embed([query], task="query").vectors[0]
        scored: list[EvidenceCandidate] = []
        for document in eligible:
            score = cosine_similarity(query_vector, self._vectors[document.content_sha256])
            if score < minimum_score:
                continue
            scored.append(
                EvidenceCandidate(
                    evidence_id=document.evidence_id,
                    score=round(score, 6),
                    content_sha256=document.content_sha256,
                    classification=document.classification,
                    source_locator=document.source_locator,
                    model=self._model,
                    dimensions=self._dimensions,
                )
            )
        # Ties broken on evidence id so the same corpus and query always produce
        # the same ordering. A retrieval surface that reshuffles equal scores
        # between runs cannot be cited in a workpaper.
        scored.sort(key=lambda candidate: (-candidate.score, candidate.evidence_id))
        return scored[:limit]

    @property
    def semantic(self) -> bool:
        return self._semantic

    def describe(self) -> dict[str, Any]:
        """What produced this index, for the trace and for Judge Mode."""
        described: dict[str, Any] = {
            "model": self._model,
            "dimensions": self._dimensions,
            "documents": len(self._documents),
            "distinct_content_hashes": len(self._vectors),
            "vectors_computed": self.embed_calls,
            "cache_hits": self.cache_hits,
            "semantic": self._semantic,
            "authoritative": False,
        }
        if not self._semantic:
            described["warning"] = (
                "This index was built with the deterministic test transport. Its "
                "vectors carry no meaning and its ranking is not a retrieval "
                "result. Run with EmbeddingGemma to retrieve."
            )
        return described


def _tokenize(text: str) -> list[str]:
    return [token for token in text.lower().split() if token]
