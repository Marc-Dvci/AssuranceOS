"""Prove the Gemini credential works, and say what one call costs.

Run this before anything that depends on a hosted model. It makes exactly one
small generation, then prints what the server said about it: which model
answered, how many tokens it read and wrote, and what those tokens price at.

The point is the diagnosis, not the answer. A credential can fail in four ways
that look identical from inside an application — missing, wrong project, API not
enabled, no billing attached — and each has a different fix. So a failure here is
reported as the specific cause rather than as a stack trace.

    python scripts/check_gemini.py
    python scripts/check_gemini.py --model gemini-3.7-flash
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from assuranceos.config import Settings  # noqa: E402
from assuranceos.economics import (  # noqa: E402
    INTRODUCTORY_PRICE_USD_PER_MILLION,
    LIST_PRICE_USD_PER_MILLION,
)
from assuranceos.governance.models_client import GeminiClient  # noqa: E402

#: Deliberately trivial, and deliberately asks for JSON: the transport sets
#: `response_mime_type=application/json`, so a prompt that invites prose would
#: fail for a reason that has nothing to do with the credential.
PROBE = 'Reply with exactly this JSON object and nothing else: {"ok": true}'


def _diagnose(error: Exception) -> str:
    text = f"{type(error).__name__}: {error}"
    lowered = text.lower()
    if "api key not valid" in lowered or "api_key_invalid" in lowered:
        return (
            "The key was rejected. Re-copy it from https://aistudio.google.com/apikey "
            "— a trailing space or a pair of quotes in .env.local is the usual cause."
        )
    if "has not been used" in lowered or "service_disabled" in lowered:
        return (
            "The key is valid but the Generative Language API is not enabled on its "
            "project. Enable it in the console, then wait about two minutes."
        )
    if "resource_exhausted" in lowered or "429" in lowered:
        return (
            "Quota refused the call. This is what an unbilled project looks like: "
            "link your billing account to the project the key belongs to."
        )
    if "defaultcredentials" in lowered:
        return (
            "The Vertex path was selected because GOOGLE_CLOUD_PROJECT is set, and "
            "there are no application-default credentials. Either run "
            "`gcloud auth application-default login`, or clear GOOGLE_CLOUD_PROJECT "
            "to use the plain API-key path."
        )
    if "publisher model" in lowered and "not found" in lowered:
        location = os.getenv("ASSURANCEOS_GEMINI_LOCATION") or "global"
        return (
            "The model was not found *in this location*, which is not the same as "
            "not existing. Gemini 3.x is served from the `global` Vertex endpoint "
            "only, and a region lists it in models.list while refusing to run it — "
            "so the 404 reads like a typo in the model id when it is a routing "
            f"problem. Set ASSURANCEOS_GEMINI_LOCATION=global (currently {location!r}). "
            "Leave GOOGLE_CLOUD_LOCATION on a real region; Cloud Run and Agent "
            "Engine need one."
        )
    if "install the agent-cloud extra" in lowered:
        return "Install the SDK: pip install -e '.[agent-cloud]'"
    return "Unrecognised failure. The message above is the server's own."


def _list_models(client: Any) -> int:
    """Print the model ids this credential can actually call.

    Worth its own flag: a configured model id that does not exist fails at the
    first real call with the same 404 a typo produces, and the fix is different.
    """
    try:
        models = list(client.models.list())
    except Exception as error:  # noqa: BLE001
        print(f"FAILED  {type(error).__name__}: {error}\n")
        print(_diagnose(error))
        return 1
    names = sorted(
        str(getattr(model, "name", "")).removeprefix("models/")
        for model in models
        if "generateContent" in (getattr(model, "supported_actions", None) or ["generateContent"])
    )
    print(f"{len(names)} models available to this credential:\n")
    for name in names:
        print(f"  {name}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", default=None, help="model id (default: from settings)")
    parser.add_argument(
        "--list-models", action="store_true", help="print the ids this key can call"
    )
    arguments = parser.parse_args()

    settings = Settings.from_env()
    model = arguments.model or settings.gemini_model
    project = os.getenv("GOOGLE_CLOUD_PROJECT") or ""
    key = os.getenv("GOOGLE_API_KEY") or ""

    print(f"model              {model}")
    print(f"transport          {'Vertex AI' if project else 'Gemini API (key)'}")
    if project:
        print(f"project            {project}")
    else:
        # Never the key itself. Enough to tell 'set' from 'set to the wrong thing'.
        print(f"GOOGLE_API_KEY     {'set, ' + str(len(key)) + ' chars' if key else 'NOT SET'}")
        if not key:
            print(
                "\nNo credential found. Put GOOGLE_API_KEY=AIza... in a file named "
                ".env.local next to .env (untracked), then run this again."
            )
            return 2

    client = GeminiClient(model_name=model, project=project or None)
    if arguments.list_models:
        return _list_models(client._ensure_client())
    try:
        response = client.generate(
            system_instruction="You are a connectivity probe.",
            prompt=PROBE,
            max_output_tokens=64,
        )
    except Exception as error:  # noqa: BLE001 - the diagnosis is the product here
        print(f"\nFAILED  {type(error).__name__}: {error}\n")
        print(_diagnose(error))
        return 1

    list_in, list_out = LIST_PRICE_USD_PER_MILLION.get(model, (0.0, 0.0))
    intro_in, intro_out = INTRODUCTORY_PRICE_USD_PER_MILLION.get(model, (0.0, 0.0))
    cost = (response.input_tokens / 1e6) * list_in + (response.output_tokens / 1e6) * list_out
    intro = (response.input_tokens / 1e6) * intro_in + (response.output_tokens / 1e6) * intro_out

    print("\nOK")
    print(f"answered by        {response.model}")
    print(f"tokens             {response.input_tokens} in / {response.output_tokens} out")
    print(f"finish reason      {response.finish_reason}")
    print(f"reply              {response.text.strip()[:120] or '(empty)'}")
    if response.reasoning:
        print(f"reasoning          {len(response.reasoning)} characters (a second channel)")
    if list_in:
        print(f"this call cost     ${cost:.8f} at the permanent rate (${intro:.8f} introductory)")
    else:
        print(f"this call cost     unknown — {model} is not in the published price table")
    print("\nThe credential works. Seeding and the demo re-capture can run on Gemini.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
