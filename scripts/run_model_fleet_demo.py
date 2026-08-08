"""Run the EmbeddingGemma and Chirp 3 demonstration over the Asteria corpus.

Offline and deterministic by default. The two live paths:

    # EmbeddingGemma on a loopback llama.cpp server, beside the data
    python scripts/run_model_fleet_demo.py \
        --embedding-mode local --embedding-url http://127.0.0.1:5001/v1

    # EmbeddingGemma on Vertex AI and Chirp 3 on Speech-to-Text v2
    python scripts/run_model_fleet_demo.py \
        --embedding-mode vertex --speech-mode chirp --audio path/to/walkthrough.wav
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from assuranceos.config import settings
from assuranceos.governance.embeddings import build_embedding_client
from assuranceos.governance.speech import build_transcription_client
from assuranceos.model_fleet_demo import run_model_fleet_demo

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--embedding-mode",
        default="deterministic",
        choices=["deterministic", "local", "vertex", "gemini"],
        help="deterministic keeps the run offline; local and vertex call EmbeddingGemma",
    )
    parser.add_argument("--embedding-url", default=None, help="OpenAI-compatible /v1 base URL")
    parser.add_argument("--embedding-model", default=None, help="embedding model override")
    parser.add_argument(
        "--dimensions",
        type=int,
        default=None,
        choices=[768, 512, 256, 128],
        help="Matryoshka output width; smaller is cheaper to store and compare",
    )
    parser.add_argument(
        "--speech-mode",
        default="scripted",
        choices=["scripted", "chirp"],
        help="scripted replays the recorded walkthrough; chirp calls Speech-to-Text v2",
    )
    parser.add_argument(
        "--audio",
        default=None,
        type=Path,
        help="walkthrough recording, required for --speech-mode chirp",
    )
    parser.add_argument("--limit", type=int, default=5, help="candidates to show")
    args = parser.parse_args()

    embedding_client = None
    if args.embedding_mode != "deterministic":
        embedding_client = build_embedding_client(
            args.embedding_mode,
            model=args.embedding_model or settings.embedding_model,
            base_url=args.embedding_url or settings.embedding_url,
            dimensions=args.dimensions or settings.embedding_dimensions,
        )

    transcription_client = None
    audio = None
    if args.speech_mode != "scripted":
        if args.audio is None:
            parser.error("--speech-mode chirp requires --audio")
        audio = args.audio.read_bytes()
        transcription_client = build_transcription_client(
            args.speech_mode,
            location=settings.speech_location,
            model=settings.speech_model,
            diarization_speakers=2,
        )

    result = run_model_fleet_demo(
        demo_root=ROOT / settings.demo_root,
        embedding_client=embedding_client,
        transcription_client=transcription_client,
        audio=audio,
        limit=args.limit,
    )
    print(json.dumps(result.to_dict(), indent=2))


if __name__ == "__main__":
    main()
