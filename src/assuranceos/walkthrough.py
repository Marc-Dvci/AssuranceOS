"""Walkthrough interviews: the recording, the transcript, and what they prove.

An audit walkthrough is a conversation. The process owner explains how the
control works, and everything downstream — the population, the sample, the test —
is aimed at what they said. It is the one input to an engagement that arrives
with no system behind it.

This module puts that input in the vault without letting it pretend to be more
than it is:

* the **recording** is original evidence, ``accepted=False``. It is admissible
  evidence of a conversation and of nothing else.
* the **transcript** is a *derivative* of the recording, produced by Chirp 3, with
  the model and its confidence recorded in the lineage. It never replaces the
  audio; a disputed sentence is settled by listening.
* an **assertion** is a sentence in which someone stated how a control operates.
  It becomes a claim about *what was said* — supported by the transcript — and
  carries a standing limitation saying it is uncorroborated. The claim that the
  control actually works that way has to come from system data.

That last distinction is the whole module. A transcript supports "the head of
support said first response is within eight hours". It does not support "first
response is within eight hours", and in the Asteria corpus it is precisely the
sentence that turns out to be false.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from .governance.speech import (
    Transcript,
    TranscriptionClient,
    TranscriptionError,
    WalkthroughAssertion,
    extract_assertions,
)
from .reporting.definitions import ClaimInput, ClaimType

#: Recorded against the transcript-derived claim, every time, without a way to
#: switch it off. A caller who could suppress it would be able to promote
#: hearsay to evidence by passing a flag.
UNCORROBORATED_LIMITATION = (
    "Management assertion from a recorded walkthrough. Evidence of what was "
    "stated, not of how the control operates. Requires corroboration by system "
    "evidence before any conclusion rests on it."
)

#: Below this, a segment is not turned into an assertion. A misheard sentence is
#: a different sentence, and the engagement would go on to test the wrong thing.
DEFAULT_MINIMUM_CONFIDENCE = 0.6


@dataclass(frozen=True)
class WalkthroughRecord:
    """A transcribed walkthrough, as it now exists in canonical state."""

    audio_evidence_id: str
    transcript_evidence_id: str
    transcript: Transcript
    assertions: tuple[WalkthroughAssertion, ...]
    interviewee: str | None = None
    conducted_at: datetime | None = None

    def describe(self) -> dict[str, Any]:
        return {
            "audio_evidence_id": self.audio_evidence_id,
            "transcript_evidence_id": self.transcript_evidence_id,
            "assertions": len(self.assertions),
            "interviewee": self.interviewee,
            **self.transcript.describe(),
        }


def record_walkthrough(
    vault: Any,
    *,
    tenant_id: str,
    audio: bytes,
    client: TranscriptionClient,
    actor_id: str,
    source_locator: str,
    engagement_id: str | None = None,
    task_id: str | None = None,
    interviewee: str | None = None,
    conducted_at: datetime | None = None,
    language_codes: Sequence[str] = ("en-GB",),
    classification: str = "confidential",
    mime_type: str = "audio/wav",
    original_filename: str | None = None,
    minimum_confidence: float = DEFAULT_MINIMUM_CONFIDENCE,
    acquisition_key: str | None = None,
) -> WalkthroughRecord:
    """Ingest a recording, transcribe it, and store the transcript as a derivative.

    The audio is ingested *before* transcription, so a recogniser that fails, or
    returns something the auditor disputes, leaves the recording in the vault
    with its custody chain intact. The alternative — transcribe, then store both —
    loses the only unarguable artefact whenever the model has a bad day.

    Interviews default to ``confidential``. A walkthrough routinely contains
    named individuals describing their own work, and the classification that
    reaches the retrieval index is decided here rather than at the call site.
    """
    audio_item = vault.ingest_bytes(
        tenant_id=tenant_id,
        payload=audio,
        source_type="walkthrough_interview",
        source_locator=source_locator,
        actor_id=actor_id,
        actor_type="user",
        engagement_id=engagement_id,
        task_id=task_id,
        acquisition_key=acquisition_key,
        original_filename=original_filename,
        mime_type=mime_type,
        classification=classification,
        source_time=conducted_at,
        # A recording of someone's account of a control is not accepted evidence
        # of the control. Nothing downstream may treat it as such.
        accepted=False,
        metadata={
            "interviewee": interviewee,
            "evidence_nature": "management_assertion",
        },
    )

    transcript = client.transcribe(
        audio, language_codes=language_codes, mime_type=mime_type
    )

    # The transcript must belong to the bytes the vault stored. Without this the
    # lineage is a guess: a client that transcribed a cached, resampled, or
    # simply different file would produce a derivative whose parent is wrong,
    # and every later "listen to it yourself" would play the wrong audio.
    if transcript.audio_sha256 != audio_item.content_sha256:
        raise TranscriptionError(
            "transcript does not correspond to the stored recording: "
            f"{transcript.audio_sha256} != {audio_item.content_sha256}"
        )

    assertions = extract_assertions(transcript, minimum_confidence=minimum_confidence)
    payload = _transcript_document(transcript, interviewee=interviewee).encode("utf-8")
    transcript_item = vault.create_derivative(
        tenant_id=tenant_id,
        source_evidence_ids=[audio_item.evidence_id],
        payload=payload,
        operation="speech-to-text",
        tool_version=f"{transcript.model}/{transcript.language_code}",
        actor_id=actor_id,
        actor_type="service",
        parameters={
            "language_codes": list(language_codes),
            "minimum_confidence": minimum_confidence,
        },
        original_filename=(
            f"{original_filename}.transcript.md" if original_filename else None
        ),
        mime_type="text/markdown",
        accepted=False,
        metadata={
            "evidence_nature": "management_assertion",
            "transcription": transcript.describe(),
            "assertions": len(assertions),
            "low_confidence_segments": len(
                transcript.low_confidence_segments(minimum_confidence)
            ),
        },
    )

    return WalkthroughRecord(
        audio_evidence_id=audio_item.evidence_id,
        transcript_evidence_id=transcript_item.evidence_id,
        transcript=transcript,
        assertions=assertions,
        interviewee=interviewee,
        conducted_at=conducted_at,
    )


def assertion_claims(
    record: WalkthroughRecord, *, key_prefix: str = "walkthrough"
) -> list[ClaimInput]:
    """Turn assertions into claims about what was said.

    The statement is always reported speech — "*X* stated that ..." — and never
    the bare assertion. That is not a stylistic choice. A claim graph stores the
    statement verbatim and the renderer resolves it against evidence; if the
    statement were "first response is within eight hours", the transcript would
    be sitting in the graph as support for a proposition it cannot support, and
    the one structural guarantee in the reporting path would be gone.
    """
    speaker = record.interviewee or "the interviewee"
    claims: list[ClaimInput] = []
    for index, assertion in enumerate(record.assertions, start=1):
        claims.append(
            ClaimInput(
                key=f"{key_prefix}-assertion-{index:03d}",
                claim_type=ClaimType.OBSERVATION,
                statement=(
                    f"At {assertion.timecode} in the recorded walkthrough, "
                    f"{speaker} stated: “{assertion.statement}”"
                ),
                material=False,
                confidence=round(assertion.confidence, 4),
                supporting_evidence_ids=[record.transcript_evidence_id],
                limitations=[UNCORROBORATED_LIMITATION],
            )
        )
    return claims


def _transcript_document(transcript: Transcript, *, interviewee: str | None) -> str:
    """The stored transcript. Line-ending explicit, because this text is hashed.

    ``str.join`` with ``\\n`` rather than writing through a text file: the digest
    of this payload is the derivative's identity, and a CRLF translation on one
    platform would make the same interview hash differently in CI than on a
    laptop.
    """
    header = [
        "# Walkthrough transcript",
        "",
        f"- Recogniser: {transcript.model}",
        f"- Language: {transcript.language_code}",
        f"- Audio SHA-256: {transcript.audio_sha256}",
        f"- Duration: {transcript.duration_seconds:.1f}s",
        f"- Interviewee: {interviewee or 'not recorded'}",
        "",
        "Evidence of what was said. Not evidence that it is so.",
        "",
    ]
    body = [
        f"[{segment.timecode}] "
        + (f"{segment.speaker}: " if segment.speaker else "")
        + f"{segment.text}  _(confidence {segment.confidence:.2f})_"
        for segment in transcript.segments
    ]
    return "\n".join(header + body) + "\n"
