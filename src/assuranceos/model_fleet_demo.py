"""The two supporting Google models, on the corpus, in the order an audit uses them.

Gemini 3.7 Flash reasons and Gemma 4 can stand in for it inside a closed network.
Neither of them is how an auditor *finds* the document that matters, and neither
of them hears the interview. This demonstration runs the two models that do:

1. **Chirp 3** transcribes the walkthrough with the head of support. The
   transcript enters the vault as a derivative of the recording, and the sentence
   "we always respond within eight hours" becomes an assertion — a thing to test,
   recorded as reported speech.
2. **EmbeddingGemma** indexes the corpus and is asked what the company says about
   response commitments. It returns the incident response plan the interviewee was
   describing, the Jira configuration built from it, *and* the contract amendment
   that superseded both — which is the document nobody thought to look for,
   because no keyword in the interview appears in it.
3. The deterministic control test then decides, as it always did. The models
   found the question. They do not answer it.

Both models default to their offline transports so the run is deterministic and
costs nothing. ``--embedding-mode`` and ``--speech-mode`` switch them to
EmbeddingGemma and Chirp 3 for real.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .corpus import AsteriaCorpus
from .governance.embeddings import (
    EmbeddingClient,
    IndexedDocument,
    SemanticEvidenceIndex,
    build_embedding_client,
)
from .governance.speech import (
    ScriptedTranscriptionClient,
    TranscriptionClient,
    TranscriptSegment,
    extract_assertions,
)
from .walkthrough import UNCORROBORATED_LIMITATION

#: The walkthrough as it was recorded, for the offline path. Every word here is
#: also in the corpus: the interviewee is quoting `confluence/
#: incident_response_plan.md`, which is genuinely what their team works from, and
#: genuinely four months out of date.
WALKTHROUGH_SEGMENTS = (
    TranscriptSegment(
        text="Thanks for making time. Can you walk me through a priority one incident?",
        start_seconds=0.0,
        end_seconds=4.6,
        confidence=0.96,
        speaker="auditor",
    ),
    TranscriptSegment(
        text=(
            "Sure. It pages the on-call engineer, and we always respond within "
            "eight hours during business hours."
        ),
        start_seconds=4.6,
        end_seconds=12.1,
        confidence=0.95,
        speaker="head-of-support",
    ),
    TranscriptSegment(
        text=(
            "That is what the incident response plan requires, and the Jira "
            "automation checks the target for every ticket, so we would know."
        ),
        start_seconds=12.1,
        end_seconds=20.4,
        confidence=0.93,
        speaker="head-of-support",
    ),
    TranscriptSegment(
        text="And that is the same for every customer?",
        start_seconds=20.4,
        end_seconds=22.8,
        confidence=0.97,
        speaker="auditor",
    ),
    TranscriptSegment(
        text="Yes. One process, one target. Contracts are legal's side of the house.",
        start_seconds=22.8,
        end_seconds=27.9,
        confidence=0.91,
        speaker="head-of-support",
    ),
)

#: The audio the offline path stands in for. Real bytes, deterministically
#: derived, so the digest is stable and the lineage check is a real check —
#: but plainly not a recording, and never described as one.
PLACEHOLDER_AUDIO = (
    b"placeholder-walkthrough-audio:asteria/support-lead/2026-07-14"
)

#: What an auditor types after the interview. Not a keyword: none of these words
#: appear in the amendment that turns out to matter, which is the point.
RETRIEVAL_QUERY = (
    "how quickly must we respond to a priority one incident, and what did we "
    "promise customers"
)

#: What a working index should surface for that query, and what it means.
#: Published as an expectation rather than asserted as a result: the offline
#: transport has no semantics, so under it these files will *not* come back, and
#: a demonstration that presented its candidate list as a retrieval either way
#: would be reporting on nothing.
EXPECTED_CANDIDATES = {
    "legal/amendment_02_northwind_2026.md": (
        "the four-hour obligation nobody in the interview mentioned"
    ),
    "confluence/incident_response_plan.md": (
        "the eight-hour procedure the interviewee was quoting"
    ),
    "jira/sla_configuration.json": (
        "the automation configured from that procedure, which reports every "
        "ticket as met"
    ),
}


@dataclass(frozen=True)
class ModelFleetResult:
    transcription: dict[str, Any]
    assertions: tuple[dict[str, Any], ...]
    retrieval: dict[str, Any]
    candidates: tuple[dict[str, Any], ...]
    boundary: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "transcription": self.transcription,
            "assertions": list(self.assertions),
            "retrieval": self.retrieval,
            "candidates": list(self.candidates),
            "boundary": self.boundary,
        }


def _document_for(item: Any, *, text: str) -> IndexedDocument:
    return IndexedDocument(
        evidence_id=item.evidence_id,
        content_sha256=item.evidence.sha256,
        text=text,
        classification=item.evidence.classification,
        engagement_id="eng-service-delivery",
        title=Path(item.relative_path).stem.replace("_", " "),
        source_locator=item.relative_path,
    )


def _recall(candidates: Any) -> float:
    """How many of the three documents that matter came back."""
    returned = {candidate.source_locator for candidate in candidates}
    hits = sum(1 for source in EXPECTED_CANDIDATES if source in returned)
    return hits / len(EXPECTED_CANDIDATES)


def _readable(path: Path, *, limit: int = 4000) -> str:
    """The text an index can honestly embed.

    Spreadsheets and binaries are indexed by their name and system alone rather
    than by whatever a decoder makes of their bytes. An embedding of mojibake
    ranks; it just ranks meaninglessly, and a candidate list that looks plausible
    and is noise is worse than a short one.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        return path.stem.replace("_", " ")
    return text[:limit]


def run_model_fleet_demo(
    *,
    demo_root: Path | str,
    embedding_client: EmbeddingClient | None = None,
    transcription_client: TranscriptionClient | None = None,
    audio: bytes | None = None,
    minimum_confidence: float = 0.6,
    limit: int = 5,
) -> ModelFleetResult:
    """Transcribe the walkthrough, index the corpus, and rank it against the assertion."""
    corpus = AsteriaCorpus(demo_root)
    speech = transcription_client or ScriptedTranscriptionClient(
        segments=WALKTHROUGH_SEGMENTS, model_name="chirp_3 (offline stand-in)"
    )
    recording = PLACEHOLDER_AUDIO if audio is None else audio
    transcript = speech.transcribe(recording, language_codes=("en-GB",))
    if transcript.audio_sha256 != hashlib.sha256(recording).hexdigest():
        raise ValueError("transcript does not correspond to the supplied audio")
    assertions = extract_assertions(transcript, minimum_confidence=minimum_confidence)

    index = SemanticEvidenceIndex(
        embedding_client or build_embedding_client("deterministic")
    )
    index.index(
        _document_for(item, text=_readable(item.path))
        for item in corpus
        if item.system != "public"
    )
    candidates = index.search(
        RETRIEVAL_QUERY,
        visible_classifications=["internal", "confidential"],
        engagement_id="eng-service-delivery",
        limit=limit,
    )

    return ModelFleetResult(
        transcription={
            **transcript.describe(),
            "recorded_at": datetime(2026, 7, 14, 10, 0, tzinfo=timezone.utc).isoformat(),
            "interviewee": "head of support",
        },
        assertions=tuple(
            {
                "timecode": assertion.timecode,
                "statement": assertion.statement,
                "confidence": round(assertion.confidence, 3),
                "status": "uncorroborated",
            }
            for assertion in assertions
        ),
        retrieval={
            **index.describe(),
            "query": RETRIEVAL_QUERY,
            "corpus_files": len(corpus),
            "expected_when_semantic": EXPECTED_CANDIDATES,
            "expected_recall": (
                round(_recall(candidates), 3) if index.semantic else None
            ),
        },
        candidates=tuple(
            {
                "rank": rank,
                "evidence_id": candidate.evidence_id,
                "source": candidate.source_locator,
                "score": candidate.score,
                "content_sha256": candidate.content_sha256[:12],
                "authoritative": candidate.authoritative,
                "why_it_matters": EXPECTED_CANDIDATES.get(
                    candidate.source_locator or ""
                ),
            }
            for rank, candidate in enumerate(candidates, start=1)
        ),
        boundary={
            "assertion_limitation": UNCORROBORATED_LIMITATION,
            "who_decides": (
                "the signed deterministic control test over the incident "
                "population, not either model"
            ),
            "models_may": "surface candidates and record what was said",
            "models_may_not": (
                "accept evidence, resolve a claim, or conclude on a control"
            ),
        },
    )
