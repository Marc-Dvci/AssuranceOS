"""Chirp 3 walkthrough interviews: a transcript is evidence of speech, not of fact."""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from pathlib import Path

import pytest

from assuranceos.db.models import Tenant
from assuranceos.db.repositories import TenantRepository
from assuranceos.db.session import Database
from assuranceos.governance.speech import (
    Chirp3Client,
    ScriptedTranscriptionClient,
    Transcript,
    TranscriptSegment,
    TranscriptionError,
    build_transcription_client,
    extract_assertions,
)
from assuranceos.vault import EvidenceVault
from assuranceos.walkthrough import (
    UNCORROBORATED_LIMITATION,
    assertion_claims,
    record_walkthrough,
)

AUDIO = b"RIFF\x00\x00\x00\x00WAVEfmt not-really-audio-but-stable-bytes"

# The walkthrough that matters in the Asteria corpus. The head of support
# describes the SLA from the incident response plan, which the contract amended
# four months earlier and nobody updated.
SEGMENTS = (
    TranscriptSegment(
        text="Thanks for making the time.",
        start_seconds=0.0,
        end_seconds=2.4,
        confidence=0.97,
        speaker="1",
    ),
    TranscriptSegment(
        text=(
            "So for a priority one incident we always respond within eight hours. "
            "That is what the incident response plan requires and the Jira "
            "automation checks it."
        ),
        start_seconds=2.4,
        end_seconds=13.8,
        confidence=0.94,
        speaker="2",
    ),
    TranscriptSegment(
        text="Mumbled aside nobody could make out.",
        start_seconds=13.8,
        end_seconds=15.1,
        confidence=0.21,
        speaker="2",
    ),
)


@pytest.fixture
def database(tmp_path: Path):
    db = Database.from_sqlite_path(tmp_path / "walkthrough.db")
    db.create_schema()
    with db.transaction() as session:
        TenantRepository(session).add(
            Tenant(tenant_id="tnt_a", slug="a", name="Asteria Systems")
        )
    try:
        yield db
    finally:
        db.dispose()


@pytest.fixture
def vault(database: Database, tmp_path: Path) -> EvidenceVault:
    return EvidenceVault.local(database, tmp_path / "objects")


@pytest.fixture
def client() -> ScriptedTranscriptionClient:
    return ScriptedTranscriptionClient(segments=SEGMENTS)


def record(vault: EvidenceVault, client, **overrides):
    values = {
        "tenant_id": "tnt_a",
        "audio": AUDIO,
        "client": client,
        "actor_id": "usr_auditor",
        "source_locator": "interview://asteria/support-lead/2026-07-14",
        "interviewee": "the head of support",
        "conducted_at": datetime(2026, 7, 14, 10, 0, tzinfo=timezone.utc),
        "original_filename": "support-walkthrough.wav",
    }
    values.update(overrides)
    return record_walkthrough(vault, **values)


class TestTranscription:
    def test_the_transcript_carries_the_recogniser_and_its_confidence(
        self, client: ScriptedTranscriptionClient
    ) -> None:
        transcript = client.transcribe(AUDIO)
        described = transcript.describe()
        assert described["model"] == "scripted-transcription"
        assert described["segments"] == 3
        assert described["minimum_confidence"] == pytest.approx(0.21)
        assert described["authoritative"] is False

    def test_the_transcript_is_bound_to_the_bytes_it_was_made_from(
        self, client: ScriptedTranscriptionClient
    ) -> None:
        assert client.transcribe(AUDIO).audio_sha256 == hashlib.sha256(AUDIO).hexdigest()

    def test_low_confidence_segments_are_reported_not_hidden(
        self, client: ScriptedTranscriptionClient
    ) -> None:
        weak = client.transcribe(AUDIO).low_confidence_segments(0.6)
        assert [segment.text for segment in weak] == [
            "Mumbled aside nobody could make out."
        ]

    def test_segments_expose_a_timecode_to_listen_back_to(self) -> None:
        assert SEGMENTS[1].timecode == "00:02"


class TestAssertionExtraction:
    def test_a_statement_about_how_a_control_operates_becomes_an_assertion(self) -> None:
        transcript = ScriptedTranscriptionClient(segments=SEGMENTS).transcribe(AUDIO)
        assertions = extract_assertions(transcript, minimum_confidence=0.6)
        statements = [assertion.statement for assertion in assertions]
        assert any("within eight hours" in statement for statement in statements)

    def test_pleasantries_are_not_assertions(self) -> None:
        transcript = ScriptedTranscriptionClient(segments=SEGMENTS).transcribe(AUDIO)
        assertions = extract_assertions(transcript, minimum_confidence=0.6)
        assert not any("making the time" in a.statement for a in assertions)

    def test_a_misheard_segment_produces_no_assertion(self) -> None:
        """A low-confidence sentence is a different sentence, not a weak one."""
        segments = SEGMENTS[:2] + (
            TranscriptSegment(
                text="We always approve every change in advance.",
                start_seconds=20.0,
                end_seconds=24.0,
                confidence=0.31,
            ),
        )
        transcript = ScriptedTranscriptionClient(segments=segments).transcribe(AUDIO)
        assertions = extract_assertions(transcript, minimum_confidence=0.6)
        assert not any("approve every change" in a.statement for a in assertions)

    def test_the_same_segment_passes_when_the_recogniser_was_sure(self) -> None:
        segments = (
            TranscriptSegment(
                text="We always approve every change in advance.",
                start_seconds=20.0,
                end_seconds=24.0,
                confidence=0.95,
            ),
        )
        transcript = ScriptedTranscriptionClient(segments=segments).transcribe(AUDIO)
        assert len(extract_assertions(transcript, minimum_confidence=0.6)) == 1


class TestVaultIntegration:
    def test_the_recording_and_the_transcript_are_both_stored(
        self, vault: EvidenceVault, client
    ) -> None:
        result = record(vault, client)
        audio = vault.get("tnt_a", result.audio_evidence_id)
        transcript = vault.get("tnt_a", result.transcript_evidence_id)
        assert audio.record_kind == "original"
        assert transcript.record_kind == "derived"

    def test_neither_is_accepted_evidence(self, vault: EvidenceVault, client) -> None:
        """The whole point. A recorded account of a control is not the control."""
        result = record(vault, client)
        assert vault.get("tnt_a", result.audio_evidence_id).accepted is False
        assert vault.get("tnt_a", result.transcript_evidence_id).accepted is False

    def test_the_transcript_is_a_derivative_of_the_recording(
        self, vault: EvidenceVault, client
    ) -> None:
        result = record(vault, client)
        lineage = vault.lineage("tnt_a", result.transcript_evidence_id)
        assert result.audio_evidence_id in {
            node.evidence_id for node in lineage.nodes
        }

    def test_the_recogniser_is_recorded_in_the_lineage(
        self, vault: EvidenceVault, client
    ) -> None:
        result = record(vault, client)
        transcript = vault.get("tnt_a", result.transcript_evidence_id)
        assert transcript.metadata["transcription"]["model"] == "scripted-transcription"
        assert transcript.metadata["evidence_nature"] == "management_assertion"

    def test_interviews_default_to_confidential(
        self, vault: EvidenceVault, client
    ) -> None:
        result = record(vault, client)
        assert vault.get("tnt_a", result.audio_evidence_id).classification == "confidential"
        assert (
            vault.get("tnt_a", result.transcript_evidence_id).classification
            == "confidential"
        )

    def test_the_stored_transcript_reads_back_with_its_timecodes(
        self, vault: EvidenceVault, client
    ) -> None:
        result = record(vault, client)
        body = vault.read_bytes(
            "tnt_a",
            result.transcript_evidence_id,
            actor_id="usr_auditor",
            purpose="workpaper_review",
        ).decode("utf-8")
        assert "[00:02]" in body
        assert "Evidence of what was said. Not evidence that it is so." in body
        assert "\r\n" not in body

    def test_a_transcript_of_different_audio_is_refused(
        self, vault: EvidenceVault
    ) -> None:
        """Otherwise the lineage is a guess and 'listen for yourself' plays the
        wrong recording."""

        class WrongAudioClient:
            model_name = "wrong"

            def transcribe(self, audio, *, language_codes=("en-GB",), mime_type=None):
                return Transcript(
                    text="something else entirely",
                    segments=(),
                    model="wrong",
                    language_code="en-GB",
                    audio_sha256=hashlib.sha256(b"different bytes").hexdigest(),
                )

        with pytest.raises(TranscriptionError):
            record(vault, WrongAudioClient())

    def test_the_recording_survives_a_refused_transcript(
        self, vault: EvidenceVault
    ) -> None:
        """Ingest first. A bad recogniser day must not cost the only unarguable
        artefact in the room."""

        class FailingClient:
            model_name = "failing"

            def transcribe(self, audio, *, language_codes=("en-GB",), mime_type=None):
                raise TranscriptionError("recogniser unavailable")

        with pytest.raises(TranscriptionError):
            record(vault, FailingClient())
        stored = vault.list("tnt_a")
        assert [item.source_type for item in stored] == ["walkthrough_interview"]


class TestClaims:
    def test_a_claim_reports_speech_rather_than_asserting_the_fact(
        self, vault: EvidenceVault, client
    ) -> None:
        result = record(vault, client)
        claims = assertion_claims(result)
        assert claims
        statement = claims[0].statement
        assert statement.startswith("At 00:02 in the recorded walkthrough")
        assert "the head of support stated" in statement

    def test_every_claim_carries_the_uncorroborated_limitation(
        self, vault: EvidenceVault, client
    ) -> None:
        result = record(vault, client)
        for claim in assertion_claims(result):
            assert UNCORROBORATED_LIMITATION in claim.limitations

    def test_a_claim_is_supported_by_the_transcript_and_nothing_else(
        self, vault: EvidenceVault, client
    ) -> None:
        result = record(vault, client)
        for claim in assertion_claims(result):
            assert claim.supporting_evidence_ids == [result.transcript_evidence_id]
            assert claim.contradicting_evidence_ids == []

    def test_assertions_are_never_material(self, vault: EvidenceVault, client) -> None:
        """A material claim is one a reader acts on. Nobody should act on hearsay."""
        result = record(vault, client)
        assert all(not claim.material for claim in assertion_claims(result))

    def test_claim_keys_are_stable_and_unique(
        self, vault: EvidenceVault, client
    ) -> None:
        result = record(vault, client)
        keys = [claim.key for claim in assertion_claims(result)]
        assert len(keys) == len(set(keys))
        assert keys == sorted(keys)


class TestClientResolution:
    def test_chirp_mode_resolves_to_a_regional_speech_client(self) -> None:
        client = build_transcription_client("chirp", project="demo-project")
        assert isinstance(client, Chirp3Client)
        assert client.model_name == "chirp_3"
        assert client.location == "us-central1"

    def test_an_unknown_mode_fails_closed(self) -> None:
        with pytest.raises(TranscriptionError):
            build_transcription_client("whisper")

    def test_an_empty_mode_does_not_fall_back_to_the_scripted_client(self) -> None:
        """A walkthrough that quietly stops being transcribed fills an engagement
        with assertions nobody made."""
        with pytest.raises(TranscriptionError):
            build_transcription_client("")
