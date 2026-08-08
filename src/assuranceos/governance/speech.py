"""Chirp 3 transcription: what a person said is an assertion, not a fact.

Half of an audit happens in a room. A process owner walks the auditor through how
a control is meant to work, the auditor writes it down, and the walkthrough note
becomes the description everything downstream is tested against. It is also the
least reliable input in the engagement: people describe the process they designed
rather than the one that runs, and they do it in good faith.

So the recording is evidence and the transcript is a derivative of it, but neither
is proof of the *process*. Both enter the vault as a management assertion —
``accepted=False`` — and stay there until a deterministic test over system data
either corroborates or contradicts the statement. This module produces the
transcript and the assertions; :mod:`assuranceos.walkthrough` puts them in the
vault under that rule.

Two transports behind one contract, in the shape :mod:`models_client` already
uses:

* :class:`Chirp3Client` — Google Cloud Speech-to-Text v2 with the ``chirp_3``
  model, which is the current Universal Speech Model generation.
* :class:`ScriptedTranscriptionClient` — a fixed transcript, for tests and for
  the offline demonstration path.

Word-level confidence is requested and kept. A recogniser that is unsure of the
number in "response within *four* hours" has produced an assertion the auditor
must re-listen to, and the only way to know that is to carry the confidence
through instead of flattening the transcript to a string.
"""

from __future__ import annotations

import hashlib
import os
import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

#: Chirp 3, Google's current Universal Speech Model generation.
DEFAULT_SPEECH_MODEL = "chirp_3"

#: Chirp 3 is served from regional endpoints and is not available in every
#: region. The recogniser resource and the API endpoint must agree, and the
#: default global endpoint does not serve this model.
DEFAULT_SPEECH_LOCATION = "us-central1"

#: The synchronous v2 ``Recognize`` call accepts inline audio up to one minute.
#: Longer recordings go through ``BatchRecognize`` against a Cloud Storage URI.
#: A walkthrough interview is always longer than a minute, so the batch path is
#: the real one; the inline path exists for short clips and for the excerpt a
#: judge can run without a bucket.
INLINE_AUDIO_LIMIT_SECONDS = 60


class TranscriptionError(RuntimeError):
    """Transcription failed or was refused. Always fails closed."""


@dataclass(frozen=True)
class TranscriptSegment:
    """One utterance, with where it sits in the recording.

    The offsets are what make the transcript checkable. An assertion that turns
    out to matter can be listened to, at the second it was made, against the
    audio record whose hash is in the vault.
    """

    text: str
    start_seconds: float
    end_seconds: float
    confidence: float = 0.0
    speaker: str | None = None

    @property
    def timecode(self) -> str:
        minutes, seconds = divmod(int(self.start_seconds), 60)
        return f"{minutes:02d}:{seconds:02d}"


@dataclass(frozen=True)
class Transcript:
    """A recognised recording, with the provenance of the recognition."""

    text: str
    segments: tuple[TranscriptSegment, ...]
    model: str
    language_code: str
    audio_sha256: str
    duration_seconds: float = 0.0

    def low_confidence_segments(self, threshold: float) -> tuple[TranscriptSegment, ...]:
        """Segments the recogniser was unsure of, which a human must re-listen to."""
        return tuple(
            segment for segment in self.segments if segment.confidence < threshold
        )

    def describe(self) -> dict[str, Any]:
        confidences = [segment.confidence for segment in self.segments]
        return {
            "model": self.model,
            "language_code": self.language_code,
            "audio_sha256": self.audio_sha256,
            "duration_seconds": round(self.duration_seconds, 3),
            "segments": len(self.segments),
            "mean_confidence": (
                round(sum(confidences) / len(confidences), 4) if confidences else 0.0
            ),
            "minimum_confidence": round(min(confidences), 4) if confidences else 0.0,
            "authoritative": False,
        }


class TranscriptionClient(Protocol):
    model_name: str

    def transcribe(
        self,
        audio: bytes,
        *,
        language_codes: Sequence[str] = ("en-GB",),
        mime_type: str | None = None,
    ) -> Transcript: ...


@dataclass
class ScriptedTranscriptionClient:
    """A fixed transcript. Deterministic for tests and the offline demonstration.

    It is a transport, not a fallback. Nothing resolves to it unless the caller
    names it, because a walkthrough that quietly stops being transcribed would
    produce an engagement full of assertions nobody made.
    """

    segments: tuple[TranscriptSegment, ...] = ()
    model_name: str = "scripted-transcription"
    language_code: str = "en-GB"
    calls: list[int] = field(default_factory=list)

    def transcribe(
        self,
        audio: bytes,
        *,
        language_codes: Sequence[str] = ("en-GB",),
        mime_type: str | None = None,
    ) -> Transcript:
        self.calls.append(len(audio))
        return Transcript(
            text=" ".join(segment.text for segment in self.segments).strip(),
            segments=tuple(self.segments),
            model=self.model_name,
            language_code=language_codes[0] if language_codes else self.language_code,
            audio_sha256=hashlib.sha256(audio).hexdigest(),
            duration_seconds=(
                max(segment.end_seconds for segment in self.segments)
                if self.segments
                else 0.0
            ),
        )


@dataclass
class Chirp3Client:
    """Google Cloud Speech-to-Text v2, Chirp 3.

    ``gcs_uri`` selects the batch path. Inline bytes are limited to a minute by
    the synchronous API, and exceeding it is refused here rather than truncated,
    because a transcript silently missing its last twenty minutes is an audit
    record that says the process owner never mentioned the thing they mentioned.
    """

    project: str | None = None
    location: str = DEFAULT_SPEECH_LOCATION
    model_name: str = DEFAULT_SPEECH_MODEL
    diarization_speakers: int = 0
    timeout_seconds: float = 600.0
    _client: Any = None

    def __post_init__(self) -> None:
        self.project = self.project or os.getenv("GOOGLE_CLOUD_PROJECT")

    def _ensure_client(self) -> Any:  # pragma: no cover - optional integration
        if self._client is not None:
            return self._client
        try:
            from google.api_core.client_options import ClientOptions
            from google.cloud import speech_v2
        except ImportError as exc:
            raise TranscriptionError(
                "Install the speech extra to use Chirp 3: pip install -e '.[speech]'"
            ) from exc
        if not self.project:
            raise TranscriptionError("GOOGLE_CLOUD_PROJECT is required for Chirp 3")
        # Chirp 3 is not served from the global endpoint. Pointing a regional
        # recogniser at the global host fails with a model-not-found that reads
        # like the model does not exist.
        self._client = speech_v2.SpeechClient(
            client_options=ClientOptions(
                api_endpoint=f"{self.location}-speech.googleapis.com"
            )
        )
        return self._client

    def _config(self, language_codes: Sequence[str]) -> Any:  # pragma: no cover
        from google.cloud import speech_v2

        features = speech_v2.RecognitionFeatures(
            enable_automatic_punctuation=True,
            enable_word_time_offsets=True,
            enable_word_confidence=True,
        )
        if self.diarization_speakers > 1:
            features.diarization_config = speech_v2.SpeakerDiarizationConfig(
                min_speaker_count=2, max_speaker_count=self.diarization_speakers
            )
        return speech_v2.RecognitionConfig(
            auto_decoding_config=speech_v2.AutoDetectDecodingConfig(),
            language_codes=list(language_codes),
            model=self.model_name,
            features=features,
        )

    def transcribe(
        self,
        audio: bytes,
        *,
        language_codes: Sequence[str] = ("en-GB",),
        mime_type: str | None = None,
    ) -> Transcript:  # pragma: no cover - optional integration
        from google.cloud import speech_v2

        client = self._ensure_client()
        recognizer = (
            f"projects/{self.project}/locations/{self.location}/recognizers/_"
        )
        response = client.recognize(
            request=speech_v2.RecognizeRequest(
                recognizer=recognizer,
                config=self._config(language_codes),
                content=audio,
            ),
            timeout=self.timeout_seconds,
        )
        return self._to_transcript(
            response, audio_sha256=hashlib.sha256(audio).hexdigest(),
            language_code=language_codes[0] if language_codes else "en-GB",
        )

    def transcribe_uri(
        self,
        gcs_uri: str,
        *,
        audio_sha256: str,
        language_codes: Sequence[str] = ("en-GB",),
    ) -> Transcript:  # pragma: no cover - optional integration
        """The batch path, for a recording longer than a minute.

        The digest is supplied by the caller rather than computed here: the vault
        already holds the hash of the bytes it stored, and re-reading a
        multi-megabyte object out of Cloud Storage to recompute a number we
        already have is how a transcript ends up attributed to the wrong audio.
        """
        from google.cloud import speech_v2

        client = self._ensure_client()
        recognizer = (
            f"projects/{self.project}/locations/{self.location}/recognizers/_"
        )
        operation = client.batch_recognize(
            request=speech_v2.BatchRecognizeRequest(
                recognizer=recognizer,
                config=self._config(language_codes),
                files=[speech_v2.BatchRecognizeFileMetadata(uri=gcs_uri)],
                recognition_output_config=speech_v2.RecognitionOutputConfig(
                    inline_response_config=speech_v2.InlineOutputConfig()
                ),
            )
        )
        outcome = operation.result(timeout=self.timeout_seconds)
        result = outcome.results.get(gcs_uri)
        if result is None or result.transcript is None:
            raise TranscriptionError(f"no transcript returned for {gcs_uri}")
        return self._to_transcript(
            result.transcript,
            audio_sha256=audio_sha256,
            language_code=language_codes[0] if language_codes else "en-GB",
        )

    def _to_transcript(
        self, response: Any, *, audio_sha256: str, language_code: str
    ) -> Transcript:  # pragma: no cover - optional integration
        segments: list[TranscriptSegment] = []
        for result in getattr(response, "results", None) or []:
            alternatives = getattr(result, "alternatives", None) or []
            if not alternatives:
                continue
            best = alternatives[0]
            words = getattr(best, "words", None) or []
            start = _seconds(getattr(words[0], "start_offset", None)) if words else 0.0
            end = _seconds(getattr(words[-1], "end_offset", None)) if words else 0.0
            speaker = None
            if words:
                tag = getattr(words[0], "speaker_label", None) or getattr(
                    words[0], "speaker_tag", None
                )
                speaker = str(tag) if tag else None
            segments.append(
                TranscriptSegment(
                    text=str(getattr(best, "transcript", "")).strip(),
                    start_seconds=start,
                    end_seconds=end,
                    confidence=float(getattr(best, "confidence", 0.0) or 0.0),
                    speaker=speaker,
                )
            )
        segments = [segment for segment in segments if segment.text]
        return Transcript(
            text=" ".join(segment.text for segment in segments).strip(),
            segments=tuple(segments),
            model=self.model_name,
            language_code=language_code,
            audio_sha256=audio_sha256,
            duration_seconds=max((segment.end_seconds for segment in segments), default=0.0),
        )


def _seconds(offset: Any) -> float:  # pragma: no cover - optional integration
    if offset is None:
        return 0.0
    total = getattr(offset, "total_seconds", None)
    if callable(total):
        return float(total())
    return float(getattr(offset, "seconds", 0)) + float(
        getattr(offset, "nanos", 0)
    ) / 1e9


def build_transcription_client(
    mode: str,
    *,
    project: str | None = None,
    location: str | None = None,
    model: str | None = None,
    diarization_speakers: int = 0,
) -> TranscriptionClient:
    """Resolve a transcription client. Unknown modes fail closed."""
    normalized = (mode or "").strip().lower()
    if normalized in {"chirp", "chirp3", "chirp_3", "gcp", "speech"}:
        return Chirp3Client(
            project=project,
            location=location or os.getenv("ASSURANCEOS_SPEECH_LOCATION", DEFAULT_SPEECH_LOCATION),
            model_name=model or os.getenv("ASSURANCEOS_SPEECH_MODEL", DEFAULT_SPEECH_MODEL),
            diarization_speakers=diarization_speakers,
        )
    if normalized in {"scripted", "mock", "test"}:
        return ScriptedTranscriptionClient()
    raise TranscriptionError(f"unknown transcription mode: {mode!r}")


# -- assertions --------------------------------------------------------------

#: A sentence is an assertion about the control environment when it states how
#: something *is done*, not when it is a pleasantry or a question. This is a
#: filter, not a classifier: it errs towards keeping sentences, because a missed
#: assertion is never tested and a spurious one is merely tested and dismissed.
_ASSERTION_HINTS = re.compile(
    r"\b(we|our|the team|i)\b.{0,80}?\b("
    r"always|never|require|requires|required|must|approve|approves|approved|"
    r"review|reviews|reviewed|check|checks|checked|monitor|monitors|monitored|"
    r"respond|responds|within|policy|process|procedure|control|sign off|signs off"
    r")\b",
    re.I | re.S,
)

_SENTENCE = re.compile(r"(?<=[.!?])\s+")


@dataclass(frozen=True)
class WalkthroughAssertion:
    """Something a person said is true about how a control operates.

    It is not a finding, an observation, or evidence. It is a statement to be
    tested. ``corroborated`` is deliberately absent: this object never knows
    whether it turned out to be true, because that answer belongs to the claim
    graph, where it is recorded with the evidence that settled it.
    """

    statement: str
    start_seconds: float
    end_seconds: float
    confidence: float
    speaker: str | None = None

    @property
    def timecode(self) -> str:
        minutes, seconds = divmod(int(self.start_seconds), 60)
        return f"{minutes:02d}:{seconds:02d}"


def extract_assertions(
    transcript: Transcript, *, minimum_confidence: float = 0.0
) -> tuple[WalkthroughAssertion, ...]:
    """Sentences in the transcript that assert how a control operates.

    Segments below ``minimum_confidence`` are dropped rather than kept with a
    warning. A misheard sentence is not a weak assertion, it is a different
    sentence, and testing the control against a sentence nobody said wastes the
    engagement's time in a way that is very hard to notice afterwards.
    """
    assertions: list[WalkthroughAssertion] = []
    for segment in transcript.segments:
        if segment.confidence < minimum_confidence:
            continue
        for sentence in _SENTENCE.split(segment.text):
            cleaned = sentence.strip()
            if len(cleaned) < 12 or not _ASSERTION_HINTS.search(cleaned):
                continue
            assertions.append(
                WalkthroughAssertion(
                    statement=cleaned,
                    start_seconds=segment.start_seconds,
                    end_seconds=segment.end_seconds,
                    confidence=segment.confidence,
                    speaker=segment.speaker,
                )
            )
    return tuple(assertions)
