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
DEFAULT_GEMINI_MODEL = "gemini-3.7-flash"


@dataclass
class ModelResponse:
    """One model reply, separating the answer channel from the reasoning channel.

    Reasoning models emit two streams. ``text`` is the answer the runtime parses;
    ``reasoning`` is the model's private deliberation, which some servers return in
    a distinct field and others inline in ``<think>`` tags. The two are kept apart
    deliberately: reasoning is evidence about *how* a conclusion was reached and
    belongs in the trace, but it is never parsed as the conclusion itself.
    """

    text: str
    input_tokens: int = 0
    output_tokens: int = 0
    model: str = "unknown"
    finish_reason: str = "stop"
    reasoning: str = ""
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def truncated(self) -> bool:
        """The reply hit the output ceiling before the model finished."""
        return self.finish_reason == "length"

    @property
    def reasoning_only(self) -> bool:
        """The budget was spent entirely on deliberation, leaving no answer.

        This is the characteristic failure of a reasoning model given too small an
        output ceiling, and it is worth distinguishing: the prompt is fine, the
        budget is not.
        """
        return not self.text.strip() and bool(self.reasoning.strip())


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
    """Returns queued replies. Keeps demonstrations and tests deterministic.

    Replies pass through the same reasoning split as a live client, so a scripted
    reply may carry ``<think>`` tags to exercise the reasoning path offline.
    ``finish_reasons`` queues per-call finish reasons for truncation tests.
    """

    replies: list[str]
    model_name: str = "scripted"
    calls: list[str] = field(default_factory=list)
    finish_reasons: list[str] = field(default_factory=list)

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
        finish_reason = self.finish_reasons.pop(0) if self.finish_reasons else "stop"
        answer, reasoning = split_reasoning(text)
        return ModelResponse(
            text=answer,
            input_tokens=len(prompt.split()),
            output_tokens=len(text.split()),
            model=self.model_name,
            finish_reason=finish_reason,
            reasoning=reasoning,
        )


def gemini_location() -> str:
    """Where to *call* Gemini, which is not where the service is deployed.

    ``GOOGLE_CLOUD_LOCATION`` was doing two incompatible jobs. Cloud Run, Cloud
    SQL and Agent Engine need a real region — ``us-central1``. **Gemini 3.x is
    served only from the ``global`` endpoint on Vertex**, and asking a region for
    it returns a bare 404 that reads exactly like a typo in the model id:

        Publisher model `projects/<p>/locations/us-central1/publishers/google/
        models/gemini-3.7-flash` was not found

    It is listed by ``models.list`` in that region, which makes the failure worse
    than confusing — the model appears to exist and refuses to answer. So the
    model endpoint gets its own variable, defaulting to ``global`` because that
    is where every model this platform pins actually lives, and the deployment
    region is left alone.
    """
    return (
        os.getenv("ASSURANCEOS_GEMINI_LOCATION")
        or os.getenv("GOOGLE_CLOUD_LOCATION_MODELS")
        or "global"
    )


@dataclass
class GeminiClient:
    """Gemini through the Google GenAI SDK, on Vertex AI or the Gemini API."""

    model_name: str = DEFAULT_GEMINI_MODEL
    project: str | None = None
    location: str = ""
    use_vertex: bool | None = None
    # Gemini 3.x Flash serves a one-million-token window and the API rejects a
    # prompt that exceeds it rather than silently trimming, so this is a pre-flight
    # convenience rather than a safety net. The whole Asteria corpus is ~48k.
    context_window_tokens: int | None = 1_000_000
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
                location=self.location or gemini_location(),
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
            # Gemini 3.7 no longer accepts temperature/top-p/top-k. Keep the
            # protocol argument for other transports, but deliberately omit it
            # for the stable Gemini transport.
            config=types.GenerateContentConfig(
                system_instruction=system_instruction,
                max_output_tokens=max_output_tokens,
                response_mime_type="application/json",
            ),
        )
        usage = getattr(response, "usage_metadata", None)

        # Gemini returns deliberation as parts flagged `thought`. `response.text`
        # already excludes them, but they are the reasoning chain the Fortified
        # Enterprise Fleet track asks us to make auditable, so collect them
        # explicitly rather than discarding them.
        reasoning_parts: list[str] = []
        finish_reason = "stop"
        for candidate in getattr(response, "candidates", None) or []:
            if reason := getattr(candidate, "finish_reason", None):
                finish_reason = str(getattr(reason, "name", reason)).lower()
            content = getattr(candidate, "content", None)
            for part in getattr(content, "parts", None) or []:
                if getattr(part, "thought", False) and getattr(part, "text", None):
                    reasoning_parts.append(str(part.text))

        answer, inline_reasoning = split_reasoning(response.text or "")
        if inline_reasoning:
            reasoning_parts.append(inline_reasoning)

        return ModelResponse(
            text=answer,
            input_tokens=getattr(usage, "prompt_token_count", 0) or 0,
            output_tokens=getattr(usage, "candidates_token_count", 0) or 0,
            model=self.model_name,
            # Gemini spells the truncation reason `max_tokens`; the rest of the
            # system reasons about it under the OpenAI name.
            finish_reason="length" if finish_reason == "max_tokens" else finish_reason,
            reasoning="\n".join(reasoning_parts).strip(),
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
    # Reasoning models deliberate before answering. For the structured-extraction
    # tasks in this runtime that deliberation is a liability rather than an asset:
    # it is unbounded, it consumes the answer's token budget, and it is a channel
    # through which injected instructions can move text past the boundary. Set to
    # False to require a direct answer, True to keep the reasoning as trace
    # evidence, or None to leave the server's default alone.
    #
    # Measured on gemma-4-12b-it-IQ4_XS: with deliberation enabled the governed
    # audit prompt produced 16,602 characters of reasoning and no answer at all
    # within a 4096-token ceiling; with it disabled the same prompt answers well
    # inside the budget.
    enable_thinking: bool | None = None
    extra_body: dict[str, Any] = field(default_factory=dict)
    # What this deployment actually serves, which is a launch flag, not a property
    # of the weights. A server started with a window smaller than the prompt drops
    # the overflow and answers 200 anyway, so the runtime needs the real number to
    # refuse before the call rather than discover it from a confident wrong answer.
    # Measured, not assumed: probe the endpoint and set it, or leave it None and
    # rely on the runtime's post-call truncation arithmetic.
    context_window_tokens: int | None = None

    def generate(
        self,
        *,
        system_instruction: str,
        prompt: str,
        temperature: float = 0.0,
        max_output_tokens: int = 1024,
    ) -> ModelResponse:
        import httpx

        payload: dict[str, Any] = {
            "model": self.model_name,
            "messages": [
                {"role": "system", "content": system_instruction},
                {"role": "user", "content": prompt},
            ],
            "temperature": temperature,
            "max_tokens": max_output_tokens,
        }
        if self.enable_thinking is not None:
            payload["enable_thinking"] = self.enable_thinking
        payload.update(self.extra_body)
        response = httpx.post(
            f"{self.base_url.rstrip('/')}/chat/completions",
            json=payload,
            headers={"Authorization": f"Bearer {self.api_key}"},
            timeout=self.timeout_seconds,
        )
        response.raise_for_status()
        body = response.json()
        choice = (body.get("choices") or [{}])[0]
        message = choice.get("message") or {}
        usage = body.get("usage") or {}

        # Reasoning models split their output across two channels. llama.cpp and
        # text-generation-webui expose deliberation as `reasoning_content`; others
        # inline it in <think> tags inside `content`. Handle both, and never let
        # reasoning reach the JSON parser as if it were the answer.
        answer, inline_reasoning = split_reasoning(message.get("content") or "")
        reasoning = str(
            message.get("reasoning_content") or message.get("reasoning") or ""
        ).strip()
        if inline_reasoning:
            reasoning = f"{reasoning}\n{inline_reasoning}".strip()

        return ModelResponse(
            text=answer,
            input_tokens=int(usage.get("prompt_tokens") or 0),
            output_tokens=int(usage.get("completion_tokens") or 0),
            model=str(body.get("model") or self.model_name),
            finish_reason=str(choice.get("finish_reason") or "stop"),
            reasoning=reasoning,
            raw=body,
        )


_JSON_BLOCK = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.S)
_BARE_OBJECT = re.compile(r"\{.*\}", re.S)

# Reasoning servers either return deliberation in a separate field or inline it in
# one of these tag pairs. An unterminated opening tag means the budget ran out
# mid-thought, so everything after it is reasoning and there is no answer at all.
_THINK_TAGS = ("think", "thinking", "reasoning")
_THINK_BLOCK = re.compile(
    r"<(" + "|".join(_THINK_TAGS) + r")\s*>(.*?)</\1\s*>", re.S | re.I
)
_UNTERMINATED_THINK = re.compile(
    r"<(" + "|".join(_THINK_TAGS) + r")\s*>(.*)$", re.S | re.I
)


def split_reasoning(text: str) -> tuple[str, str]:
    """Separate an inline reasoning block from the answer that follows it.

    Returns ``(answer, reasoning)``. This must happen before any JSON extraction:
    a reasoning model routinely rehearses the output object inside its own
    scratchpad, so parsing the unsplit reply can lift a conclusion the model was
    still deliberating over and treat it as the committed answer. For an audit
    conclusion that distinction is the whole point.
    """
    if not text:
        return "", ""
    reasoning_parts = [match.group(2) for match in _THINK_BLOCK.finditer(text)]
    answer = _THINK_BLOCK.sub("", text)
    if leftover := _UNTERMINATED_THINK.search(answer):
        reasoning_parts.append(leftover.group(2))
        answer = answer[: leftover.start()]
    return answer.strip(), "\n".join(part.strip() for part in reasoning_parts).strip()


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


def _optional_bool_env(name: str) -> bool | None:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return None
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def probe_context_window(
    client: Any,
    *,
    lower: int = 1024,
    upper: int = 1_048_576,
    tolerance: int = 512,
) -> int | None:
    """Measure the context a server actually serves, rather than being told.

    The number that matters is not what the weights support, it is what the
    process was launched with. A llama.cpp-shaped server started with ``-c 16384``
    serves 16384 whatever the model card says, and it does not advertise the
    figure anywhere: ``/props``, ``/slots`` and ``/v1/models`` are all either
    absent or silent about it on the endpoints seen here.

    So it is measured. Oversized prompts come back with ``usage.prompt_tokens``
    pinned at the ceiling, which makes the window directly observable: send a
    prompt that is certainly too long, read what the server says it read, and the
    window is that plus the output it reserved. A binary search then confirms the
    boundary rather than trusting one reading.

    Returns ``None`` when the endpoint cannot be probed, which leaves the runtime
    with no declared window -- the honest state, and one the post-call truncation
    arithmetic still covers.
    """

    def reads(words: int, max_tokens: int = 1) -> int | None:
        try:
            response = client.generate(
                system_instruction="",
                prompt="word " * words,
                temperature=0.0,
                max_output_tokens=max_tokens,
            )
        except Exception:
            return None
        return response.input_tokens or None

    # One oversized call usually answers it outright: the reported count is the
    # ceiling. Everything after this is confirmation.
    ceiling = reads(upper // 8)
    if ceiling is None:
        return None
    if ceiling >= (upper // 8) * 0.9:
        # The server read essentially everything sent, so the window is larger
        # than the probe. Report what was demonstrated rather than guessing.
        return int(ceiling)

    window = int(ceiling) + 1
    # Confirm by bisection: the largest prompt read in full is the window.
    low, high = lower, window
    while high - low > tolerance:
        middle = (low + high) // 2
        observed = reads(middle)
        if observed is None:
            return None
        # `middle` words encode to at least `middle` tokens, so reading fewer
        # than that means the prompt was cut.
        if observed >= middle:
            low = middle
        else:
            high = middle
    return max(window, low)


def _optional_int_env(name: str) -> int | None:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return None
    try:
        value = int(raw.strip())
    except ValueError:
        return None
    return value if value > 0 else None


def build_client(
    mode: str,
    *,
    model: str | None = None,
    base_url: str | None = None,
    project: str | None = None,
    location: str | None = None,
    enable_thinking: bool | None = None,
    context_window_tokens: int | None = None,
) -> ModelClient:
    """Resolve a client from configuration. Unknown modes fail closed."""
    normalized = (mode or "mock").strip().lower()
    if normalized in {"gemini", "vertex"}:
        client = GeminiClient(
            model_name=model or DEFAULT_GEMINI_MODEL,
            project=project,
            location=location or gemini_location(),
            use_vertex=normalized == "vertex" or None,
        )
        if window := (context_window_tokens or _optional_int_env("ASSURANCEOS_MODEL_CONTEXT_TOKENS")):
            client.context_window_tokens = window
        return client
    if normalized in {"local", "openai-compatible", "llamacpp"}:
        return OpenAICompatibleClient(
            base_url=base_url or os.getenv(
                "ASSURANCEOS_LOCAL_MODEL_URL", "http://127.0.0.1:5000/v1"
            ),
            model_name=model or os.getenv("ASSURANCEOS_LOCAL_MODEL_NAME", "local"),
            enable_thinking=(
                enable_thinking
                if enable_thinking is not None
                else _optional_bool_env("ASSURANCEOS_LOCAL_MODEL_ENABLE_THINKING")
            ),
            context_window_tokens=(
                context_window_tokens
                or _optional_int_env("ASSURANCEOS_LOCAL_MODEL_CONTEXT_TOKENS")
                or _optional_int_env("ASSURANCEOS_MODEL_CONTEXT_TOKENS")
            ),
        )
    if normalized == "mock":
        return ScriptedClient(replies=[])
    raise ValueError(f"unknown model mode: {mode!r}")
