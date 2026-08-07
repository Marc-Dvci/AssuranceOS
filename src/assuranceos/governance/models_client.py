"""Model clients for the governed agent runtime.

Three transports behind one contract:

* :class:`GeminiClient` — the primary path, Gemini 3.5 or newer through the
  Google GenAI SDK against either Vertex AI or the Gemini API.
* :class:`OpenAICompatibleClient` — any OpenAI-shaped endpoint. This covers the
  local ``llama.cpp`` privacy runtime and lets the whole governed path be
  exercised offline against a local Gemma build.
* :class:`ScriptedClient` — deterministic replies for tests and demonstrations.

The runtime above never learns which one it is talking to. Model choice is a
deployment decision; the governance guarantees are not.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from typing import Any, Protocol

# The hackathon mandates Gemini 3.5 or newer.
DEFAULT_GEMINI_MODEL = "gemini-3.5-flash"


@dataclass
class ModelResponse:
    text: str
    input_tokens: int = 0
    output_tokens: int = 0
    model: str = "unknown"
    finish_reason: str = "stop"
    raw: dict[str, Any] = field(default_factory=dict)


class ModelClient(Protocol):
    model_name: str

    def generate(
        self,
        *,
        system_instruction: str,
        prompt: str,
        temperature: float = 0.0,
        max_output_tokens: int = 1024,
    ) -> ModelResponse: ...


@dataclass
class ScriptedClient:
    """Returns queued replies. Keeps demonstrations and tests deterministic."""

    replies: list[str]
    model_name: str = "scripted"
    calls: list[str] = field(default_factory=list)

    def generate(
        self,
        *,
        system_instruction: str,
        prompt: str,
        temperature: float = 0.0,
        max_output_tokens: int = 1024,
    ) -> ModelResponse:
        self.calls.append(prompt)
        text = self.replies.pop(0) if self.replies else "{}"
        return ModelResponse(
            text=text,
            input_tokens=len(prompt.split()),
            output_tokens=len(text.split()),
            model=self.model_name,
        )


@dataclass
class GeminiClient:
    """Gemini through the Google GenAI SDK, on Vertex AI or the Gemini API."""

    model_name: str = DEFAULT_GEMINI_MODEL
    project: str | None = None
    location: str = "us-central1"
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
            raise RuntimeError(
                "Install the agent-cloud extra to use Gemini: pip install -e '.[agent-cloud]'"
            ) from exc
        if self.use_vertex:
            self._client = genai.Client(
                vertexai=True,
                project=self.project or os.getenv("GOOGLE_CLOUD_PROJECT"),
                location=self.location or os.getenv("GOOGLE_CLOUD_LOCATION", "us-central1"),
            )
        else:
            self._client = genai.Client(api_key=os.getenv("GOOGLE_API_KEY"))
        return self._client

    def generate(
        self,
        *,
        system_instruction: str,
        prompt: str,
        temperature: float = 0.0,
        max_output_tokens: int = 1024,
    ) -> ModelResponse:
        client = self._ensure_client()
        from google.genai import types  # pragma: no cover - optional integration

        response = client.models.generate_content(
            model=self.model_name,
            contents=prompt,
            config=types.GenerateContentConfig(
                system_instruction=system_instruction,
                temperature=temperature,
                max_output_tokens=max_output_tokens,
                response_mime_type="application/json",
            ),
        )
        usage = getattr(response, "usage_metadata", None)
        return ModelResponse(
            text=response.text or "",
            input_tokens=getattr(usage, "prompt_token_count", 0) or 0,
            output_tokens=getattr(usage, "candidates_token_count", 0) or 0,
            model=self.model_name,
        )


@dataclass
class OpenAICompatibleClient:
    """Any OpenAI-shaped chat endpoint, including a local llama.cpp server.

    Used for the local privacy runtime and to exercise the governed path offline.
    No fallback to a hosted model is possible from here: the base URL is explicit,
    so a local deployment cannot silently start sending evidence to a third party.
    """

    base_url: str = "http://127.0.0.1:5000/v1"
    model_name: str = "local"
    api_key: str = "not-needed"
    timeout_seconds: float = 300.0

    def generate(
        self,
        *,
        system_instruction: str,
        prompt: str,
        temperature: float = 0.0,
        max_output_tokens: int = 1024,
    ) -> ModelResponse:
        import httpx

        payload = {
            "model": self.model_name,
            "messages": [
                {"role": "system", "content": system_instruction},
                {"role": "user", "content": prompt},
            ],
            "temperature": temperature,
            "max_tokens": max_output_tokens,
        }
        response = httpx.post(
            f"{self.base_url.rstrip('/')}/chat/completions",
            json=payload,
            headers={"Authorization": f"Bearer {self.api_key}"},
            timeout=self.timeout_seconds,
        )
        response.raise_for_status()
        body = response.json()
        choice = (body.get("choices") or [{}])[0]
        usage = body.get("usage") or {}
        return ModelResponse(
            text=(choice.get("message") or {}).get("content") or "",
            input_tokens=int(usage.get("prompt_tokens") or 0),
            output_tokens=int(usage.get("completion_tokens") or 0),
            model=str(body.get("model") or self.model_name),
            finish_reason=str(choice.get("finish_reason") or "stop"),
            raw=body,
        )


_JSON_BLOCK = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.S)
_BARE_OBJECT = re.compile(r"\{.*\}", re.S)


def extract_json_object(text: str) -> dict[str, Any] | None:
    """Recover a JSON object from a model reply.

    Small local models wrap JSON in prose or fences even when told not to. Repair
    is bounded and structural: it never invents fields, so a reply that carries no
    object still fails closed upstream.
    """
    if not text:
        return None
    for candidate in (
        (_JSON_BLOCK.search(text) or _BARE_OBJECT.search(text) or None),
    ):
        if candidate is None:
            break
        blob = candidate.group(1) if candidate.re is _JSON_BLOCK else candidate.group(0)
        try:
            parsed = json.loads(blob)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed
    try:
        parsed = json.loads(text.strip())
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def build_client(
    mode: str,
    *,
    model: str | None = None,
    base_url: str | None = None,
    project: str | None = None,
    location: str | None = None,
) -> ModelClient:
    """Resolve a client from configuration. Unknown modes fail closed."""
    normalized = (mode or "mock").strip().lower()
    if normalized in {"gemini", "vertex"}:
        return GeminiClient(
            model_name=model or DEFAULT_GEMINI_MODEL,
            project=project,
            location=location or "us-central1",
            use_vertex=normalized == "vertex" or None,
        )
    if normalized in {"local", "openai-compatible", "llamacpp"}:
        return OpenAICompatibleClient(
            base_url=base_url or os.getenv(
                "ASSURANCEOS_LOCAL_MODEL_URL", "http://127.0.0.1:5000/v1"
            ),
            model_name=model or os.getenv("ASSURANCEOS_LOCAL_MODEL_NAME", "local"),
        )
    if normalized == "mock":
        return ScriptedClient(replies=[])
    raise ValueError(f"unknown model mode: {mode!r}")
