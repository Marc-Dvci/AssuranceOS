# Chirp 3 walkthrough transcription profile

**Model:** `chirp_3` (Speech-to-Text v2) · **Implementation:**
[`src/assuranceos/governance/speech.py`](../../src/assuranceos/governance/speech.py)
and [`src/assuranceos/walkthrough.py`](../../src/assuranceos/walkthrough.py) ·
**Tests:** [`tests/test_walkthrough.py`](../../tests/test_walkthrough.py)

Half of an audit happens in a room. A process owner explains how a control is
meant to work, and everything downstream is aimed at what they said. It is also
the least reliable input in the engagement: people describe the process they
designed rather than the one that runs, and they do it in good faith.

This profile ingests that conversation without letting it pretend to be more than
it is.

## The chain

| Artefact | Kind | Accepted | Proves |
| --- | --- | --- | --- |
| the recording | original evidence | no | that a conversation happened, and what was in it |
| the transcript | **derivative** of the recording | no | what the recogniser heard |
| an assertion | a claim about *what was said* | n/a | nothing about the control |

The transcript never replaces the audio. A disputed sentence is settled by
listening, at the timecode carried on the segment, against the recording whose
hash is in the vault.

## Mandatory properties

* **The recording is ingested before transcription.** A recogniser that fails, or
  returns something the auditor disputes, leaves the audio in the vault with its
  custody chain intact.
* **The transcript's audio digest must match the stored bytes**, or the
  derivative is refused. Otherwise the lineage is a guess and "listen for
  yourself" plays the wrong recording.
* **Word confidence is requested and kept.** Segments below the threshold produce
  no assertion at all: a misheard sentence is a different sentence, not a weak
  one, and testing the control against a sentence nobody said wastes the
  engagement in a way that is hard to notice afterwards.
* **Claims are reported speech.** The claim graph stores *"at 00:02, the head of
  support stated: …"* — supported by the transcript, which genuinely supports it
  — never the bare assertion, which it does not.
* **Every assertion carries a standing uncorroborated limitation**, and it cannot
  be switched off by a caller.
* **Interviews default to `confidential`**, decided here rather than at the call
  site, because a walkthrough routinely contains named individuals describing
  their own work.
* **Local privacy mode refuses hosted transcription.** Interview audio is the
  most identifying artefact in an engagement; `Settings.validate` rejects
  `ASSURANCEOS_SPEECH_MODE=chirp` under that profile.

## What it demonstrates in the Asteria corpus

The head of support states that a priority-one incident gets a response within
eight hours, and that the Jira automation checks it. Both statements are true
descriptions of the documented process. The contract amendment signed four months
earlier says four hours. The assertion is recorded, tested against the incident
population by a signed deterministic control test, and contradicted.

## Running it

```bash
# offline, with the recorded walkthrough replayed from a fixture
python scripts/run_model_fleet_demo.py

# against Speech-to-Text v2
python scripts/run_model_fleet_demo.py --speech-mode chirp --audio walkthrough.wav
```

Recordings longer than a minute go through `BatchRecognize` against a Cloud
Storage URI; the synchronous inline path is limited to a minute by the API and
refuses rather than truncating.
