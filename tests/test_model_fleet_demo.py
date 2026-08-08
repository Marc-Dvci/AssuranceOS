"""The EmbeddingGemma and Chirp 3 demonstration over the Asteria corpus."""

from __future__ import annotations

from pathlib import Path

import pytest

from assuranceos.governance.embeddings import (
    DeterministicEmbeddingClient,
    SemanticEvidenceIndex,
)
from assuranceos.model_fleet_demo import (
    EXPECTED_CANDIDATES,
    RETRIEVAL_QUERY,
    run_model_fleet_demo,
)

ROOT = Path(__file__).resolve().parents[1]
DEMO_ROOT = ROOT / "demo" / "asteria"


@pytest.fixture(scope="module")
def result():
    return run_model_fleet_demo(demo_root=DEMO_ROOT, limit=5)


class TestTheWalkthrough:
    def test_the_assertion_is_extracted_from_the_interview(self, result) -> None:
        statements = [item["statement"] for item in result.assertions]
        assert any("eight hours" in statement for statement in statements)

    def test_the_assertion_is_recorded_as_uncorroborated(self, result) -> None:
        assert all(item["status"] == "uncorroborated" for item in result.assertions)

    def test_the_transcription_declares_itself_non_authoritative(self, result) -> None:
        assert result.transcription["authoritative"] is False

    def test_the_questions_the_auditor_asked_are_not_assertions(self, result) -> None:
        statements = " ".join(item["statement"] for item in result.assertions)
        assert "Can you walk me through" not in statements


class TestTheIndex:
    def test_the_whole_corpus_is_indexed(self, result) -> None:
        assert result.retrieval["documents"] >= 50

    def test_no_document_is_embedded_twice(self, result) -> None:
        assert (
            result.retrieval["vectors_computed"]
            == result.retrieval["distinct_content_hashes"]
        )

    def test_candidates_resolve_to_evidence_ids_and_hashes(self, result) -> None:
        for candidate in result.candidates:
            assert candidate["evidence_id"].startswith("evd_")
            assert candidate["content_sha256"]
            assert candidate["authoritative"] is False


class TestTheOfflineTransportDoesNotPretend:
    def test_the_offline_run_declares_itself_non_semantic(self, result) -> None:
        assert result.retrieval["semantic"] is False

    def test_the_offline_run_carries_a_warning(self, result) -> None:
        """A ranking with no meaning behind it must not read like a retrieval."""
        assert "not a retrieval result" in result.retrieval["warning"]

    def test_no_recall_is_reported_for_a_meaningless_index(self, result) -> None:
        assert result.retrieval["expected_recall"] is None

    def test_the_expectation_is_published_rather_than_asserted(self, result) -> None:
        assert set(result.retrieval["expected_when_semantic"]) == set(
            EXPECTED_CANDIDATES
        )

    def test_a_semantic_transport_reports_recall(self) -> None:
        """The same code path scores itself as soon as the vectors mean something."""

        scored = run_model_fleet_demo(
            demo_root=DEMO_ROOT,
            embedding_client=DeterministicEmbeddingClient(dimensions=256, semantic=True),
            limit=5,
        )
        assert scored.retrieval["semantic"] is True
        assert "warning" not in scored.retrieval
        assert scored.retrieval["expected_recall"] is not None


class TestTheBoundary:
    def test_the_models_do_not_decide(self, result) -> None:
        assert "not either model" in result.boundary["who_decides"]

    def test_the_uncorroborated_limitation_is_stated(self, result) -> None:
        assert "Requires corroboration" in result.boundary["assertion_limitation"]

    def test_the_three_documents_that_matter_are_in_the_corpus(self) -> None:
        """The expectation is only meaningful if the files exist to be found."""
        for relative in EXPECTED_CANDIDATES:
            assert (DEMO_ROOT / "sources" / relative).is_file()

    def test_the_query_does_not_quote_the_amendment(self) -> None:
        """If the query contained the answer's words, a substring match would do."""
        amendment = (
            DEMO_ROOT / "sources" / "legal" / "amendment_02_northwind_2026.md"
        ).read_text(encoding="utf-8")
        distinctive = [
            word
            for word in RETRIEVAL_QUERY.lower().split()
            if len(word) > 6 and word.isalpha()
        ]
        assert distinctive
        assert not all(word in amendment.lower() for word in distinctive)


class TestIndexSemanticsFlag:
    def test_a_client_that_does_not_declare_itself_is_treated_as_meaningless(
        self,
    ) -> None:
        class Undeclared:
            model_name = "undeclared"

            def embed(self, texts, *, task="document", titles=None):
                from assuranceos.governance.embeddings import EmbeddingBatch

                return EmbeddingBatch(
                    vectors=tuple((1.0, 0.0) for _ in texts),
                    model="undeclared",
                    dimensions=2,
                    prompt=task,
                )

        assert SemanticEvidenceIndex(Undeclared()).semantic is False
