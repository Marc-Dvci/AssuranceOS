"""EmbeddingGemma retrieval: the index finds candidates and never grants authority."""

from __future__ import annotations

import hashlib

import pytest

from assuranceos.governance.embeddings import (
    DEFAULT_EMBEDDING_MODEL,
    DOCUMENT_PROMPT,
    QUERY_PROMPT,
    DeterministicEmbeddingClient,
    EmbeddingError,
    IndexedDocument,
    LocalEmbeddingClient,
    SemanticEvidenceIndex,
    VertexEmbeddingClient,
    build_embedding_client,
    cosine_similarity,
)


def document(
    evidence_id: str,
    text: str,
    *,
    classification: str = "internal",
    engagement_id: str | None = "eng-1",
    content_sha256: str | None = None,
) -> IndexedDocument:
    return IndexedDocument(
        evidence_id=evidence_id,
        content_sha256=content_sha256 or hashlib.sha256(text.encode()).hexdigest(),
        text=text,
        classification=classification,
        engagement_id=engagement_id,
        source_locator=f"fixture://{evidence_id}",
    )


def build_index() -> SemanticEvidenceIndex:
    return SemanticEvidenceIndex(DeterministicEmbeddingClient(dimensions=256))


class TestPrompting:
    def test_query_and_document_use_different_prompts(self) -> None:
        """EmbeddingGemma is trained asymmetrically. Same text, different vector."""
        client = DeterministicEmbeddingClient(dimensions=128)
        as_query = client.embed(["access review"], task="query").vectors[0]
        as_document = client.embed(["access review"], task="document").vectors[0]
        assert as_query != as_document
        assert client.calls == [
            QUERY_PROMPT.format(content="access review"),
            DOCUMENT_PROMPT.format(title="none", content="access review"),
        ]

    def test_document_prompt_carries_the_title(self) -> None:
        client = DeterministicEmbeddingClient(dimensions=128)
        client.embed(["body text"], task="document", titles=["Access Control Policy"])
        assert client.calls == [
            DOCUMENT_PROMPT.format(title="Access Control Policy", content="body text")
        ]

    def test_unknown_task_fails_closed(self) -> None:
        client = DeterministicEmbeddingClient()
        with pytest.raises(EmbeddingError):
            client.embed(["text"], task="clustering")

    def test_titles_must_line_up_with_texts(self) -> None:
        client = DeterministicEmbeddingClient()
        with pytest.raises(EmbeddingError):
            client.embed(["a", "b"], task="document", titles=["only one"])


class TestDeterminism:
    def test_the_same_text_always_embeds_to_the_same_vector(self) -> None:
        """Across client instances, so a persisted index stays comparable."""
        first = DeterministicEmbeddingClient(dimensions=256).embed(["change ticket"])
        second = DeterministicEmbeddingClient(dimensions=256).embed(["change ticket"])
        assert first.vectors == second.vectors

    def test_vectors_are_normalised(self) -> None:
        (vector,) = DeterministicEmbeddingClient(dimensions=128).embed(["policy"]).vectors
        assert cosine_similarity(vector, vector) == pytest.approx(1.0)


class TestAccessFilter:
    def test_a_classification_the_caller_cannot_see_is_never_returned(self) -> None:
        index = build_index()
        index.index(
            [
                document("evd-open", "quarterly access review evidence"),
                document(
                    "evd-secret",
                    "quarterly access review evidence",
                    classification="restricted",
                ),
            ]
        )
        results = index.search(
            "quarterly access review evidence",
            visible_classifications=["internal"],
            limit=10,
        )
        assert [candidate.evidence_id for candidate in results] == ["evd-open"]

    def test_an_invisible_record_does_not_consume_a_result_slot(self) -> None:
        """Filtering before ranking, not after.

        Post-filtering a top-k is the subtle version of the same leak: the
        visible results shrink when a hidden record scores well, so the count
        itself reports on evidence the caller cannot see.
        """
        index = build_index()
        index.index(
            [
                document("evd-a", "access review of production administrators"),
                document(
                    "evd-hidden",
                    "access review of production administrators",
                    classification="restricted",
                ),
                document("evd-b", "access review of production administrators, part two"),
            ]
        )
        results = index.search(
            "access review of production administrators",
            visible_classifications=["internal"],
            limit=2,
        )
        assert len(results) == 2
        assert "evd-hidden" not in {candidate.evidence_id for candidate in results}

    def test_no_visible_classification_returns_nothing(self) -> None:
        index = build_index()
        index.index([document("evd-a", "anything at all")])
        assert index.search("anything", visible_classifications=[]) == []

    def test_engagement_scope_is_enforced(self) -> None:
        index = build_index()
        index.index(
            [
                document("evd-mine", "merge approval evidence", engagement_id="eng-1"),
                document("evd-theirs", "merge approval evidence", engagement_id="eng-2"),
            ]
        )
        results = index.search(
            "merge approval evidence",
            visible_classifications=["internal"],
            engagement_id="eng-1",
        )
        assert [candidate.evidence_id for candidate in results] == ["evd-mine"]


class TestCandidatesAreNotAuthority:
    def test_every_candidate_declares_itself_non_authoritative(self) -> None:
        index = build_index()
        index.index([document("evd-a", "incident response plan")])
        (candidate,) = index.search(
            "incident response plan", visible_classifications=["internal"]
        )
        assert candidate.authoritative is False
        assert candidate.model == "deterministic-fixture"
        assert candidate.dimensions == 256

    def test_a_candidate_resolves_to_canonical_evidence(self) -> None:
        """The index returns ids and hashes, never text it decided was relevant."""
        index = build_index()
        source = document("evd-a", "privileged access standard")
        index.index([source])
        (candidate,) = index.search(
            "privileged access standard", visible_classifications=["internal"]
        )
        assert candidate.evidence_id == "evd-a"
        assert candidate.content_sha256 == source.content_sha256

    def test_describe_reports_the_index_as_non_authoritative(self) -> None:
        index = build_index()
        index.index([document("evd-a", "policy text")])
        assert index.describe()["authoritative"] is False


class TestContentAddressedCache:
    def test_identical_bytes_are_embedded_once(self) -> None:
        index = build_index()
        shared = hashlib.sha256(b"the same policy").hexdigest()
        computed = index.index(
            [
                document("evd-a", "the same policy", content_sha256=shared),
                document("evd-b", "the same policy", content_sha256=shared),
            ]
        )
        assert computed == 1
        assert index.cache_hits == 1
        assert len(index) == 2

    def test_reindexing_unchanged_bytes_costs_nothing(self) -> None:
        index = build_index()
        corpus = [document("evd-a", "first"), document("evd-b", "second")]
        assert index.index(corpus) == 2
        assert index.index(corpus) == 0
        assert index.describe()["vectors_computed"] == 2

    def test_only_changed_bytes_are_re_embedded(self) -> None:
        index = build_index()
        index.index([document("evd-a", "first"), document("evd-b", "second")])
        computed = index.index(
            [document("evd-a", "first"), document("evd-b", "second, amended")]
        )
        assert computed == 1


class TestOrdering:
    def test_ranking_is_stable_for_equal_scores(self) -> None:
        """A workpaper cites an ordering. It has to be the same ordering tomorrow."""
        index = build_index()
        shared = "identical text"
        index.index(
            [
                document("evd-c", shared, content_sha256="c" * 64),
                document("evd-a", shared, content_sha256="a" * 64),
                document("evd-b", shared, content_sha256="b" * 64),
            ]
        )
        for _ in range(3):
            results = index.search(shared, visible_classifications=["internal"])
            assert [candidate.evidence_id for candidate in results] == [
                "evd-a",
                "evd-b",
                "evd-c",
            ]

    def test_minimum_score_drops_weak_candidates(self) -> None:
        index = build_index()
        index.index([document("evd-a", "wholly unrelated subject matter")])
        assert (
            index.search(
                "terminated contractor retains production administrator",
                visible_classifications=["internal"],
                minimum_score=0.99,
            )
            == []
        )


class TestMatryoshkaTruncation:
    def test_supported_dimensions_resolve(self) -> None:
        for dimensions in (768, 512, 256, 128):
            client = build_embedding_client("deterministic", dimensions=dimensions)
            assert client.embed(["text"]).dimensions == dimensions

    def test_an_untrained_dimension_is_refused(self) -> None:
        with pytest.raises(EmbeddingError):
            build_embedding_client("deterministic", dimensions=300)

    def test_truncation_renormalises(self) -> None:
        """Otherwise cosine over the prefix silently becomes a length contest."""
        from assuranceos.governance.embeddings import _truncate

        truncated = _truncate([0.8, 0.6, 0.4, 0.2], 2)
        assert cosine_similarity(truncated, truncated) == pytest.approx(1.0)
        assert sum(value * value for value in truncated) == pytest.approx(1.0)


class TestClientResolution:
    def test_vertex_mode_resolves_to_the_google_genai_transport(self) -> None:
        client = build_embedding_client("vertex", project="demo-project")
        assert isinstance(client, VertexEmbeddingClient)
        assert client.model_name == DEFAULT_EMBEDDING_MODEL
        assert client.use_vertex is True

    def test_local_mode_resolves_to_a_loopback_endpoint(self) -> None:
        client = build_embedding_client("local", base_url="http://127.0.0.1:5001/v1")
        assert isinstance(client, LocalEmbeddingClient)
        assert client.base_url == "http://127.0.0.1:5001/v1"

    def test_an_unknown_mode_fails_closed(self) -> None:
        with pytest.raises(EmbeddingError):
            build_embedding_client("whatever-is-available")

    def test_an_empty_mode_does_not_fall_back_to_the_fixture_client(self) -> None:
        """An index that silently stops being semantic is worse than one that stops."""
        with pytest.raises(EmbeddingError):
            build_embedding_client("")


class TestMismatchedBatches:
    def test_a_short_batch_is_refused(self) -> None:
        class ShortClient:
            model_name = "short"

            def embed(self, texts, *, task="document", titles=None):
                from assuranceos.governance.embeddings import EmbeddingBatch

                return EmbeddingBatch(
                    vectors=((1.0, 0.0),), model="short", dimensions=2, prompt=task
                )

        index = SemanticEvidenceIndex(ShortClient())
        with pytest.raises(EmbeddingError):
            index.index([document("evd-a", "one"), document("evd-b", "two")])

    def test_vectors_of_different_widths_cannot_be_compared(self) -> None:
        with pytest.raises(EmbeddingError):
            cosine_similarity([1.0, 0.0], [1.0, 0.0, 0.0])
