"""Claude SDK wrapper.

Single public entry point: ``call_claude``. It

1. Builds a deterministic cache key from ``(model, system, messages,
   max_tokens, response_format, prompt_version, temperature)`` and checks
   Redis. Identical re-prompts return instantly without an Anthropic call.
2. Otherwise invokes a ``langchain_anthropic.ChatAnthropic`` model
   (``ainvoke``). The underlying Anthropic SDK's internal retry handles
   transient errors — rate-limit / overloaded / connection — via
   ``max_retries=ANTHROPIC_MAX_RETRIES``.
3. For ``response_format=JSON`` it strips the ```json ... ``` (or bare ``` ... ```)
   fences Claude commonly emits and ``json.loads`` the result, raising
   ``ClaudeJSONParseError`` if the payload isn't valid JSON.
4. Caches the (parsed) result in Redis with a 7-day TTL.

Prompt content and model responses are *never* logged — they're either PII
(candidate prose), confidential JD detail, or both. Logs carry only model +
token counts + an optional ``prompt_version`` tag.

Templates live in ``src/ai/prompts/``. ``render_prompt`` loads them with
``StrictUndefined`` so a missing variable raises rather than silently
rendering an empty string into a prompt.
"""

from __future__ import annotations

import hashlib
import json
import logging
from enum import StrEnum
from functools import lru_cache
from pathlib import Path
from typing import Any, Final, Literal, TypedDict, cast

import redis.asyncio as aioredis
from jinja2 import Environment, FileSystemLoader, StrictUndefined
from langchain_anthropic import ChatAnthropic
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from langchain_core.prompts import ChatPromptTemplate, HumanMessagePromptTemplate
from pydantic import BaseModel

from src.config import settings
from src.enums.claude_models import ClaudeModel

logger = logging.getLogger(__name__)

# --- Constants pinned by the architecture doc -------------------------------

# Number of times the Anthropic SDK should retry transient errors internally.
ANTHROPIC_MAX_RETRIES: Final[int] = 3

# Redis TTL for cached completions — 7 days, per doc.
CACHE_TTL_SECONDS: Final[int] = 7 * 24 * 60 * 60

# Redis key shape. Bump the ``v1`` segment when the model set or cache-key
# preprocessing changes so stale entries are not served.
CACHE_KEY_PREFIX: Final[str] = "claude:v1:sha256:"

# Separate namespace for ``call_claude_structured`` so a structured call and a
# text/JSON call that happen to render identical messages never share a slot
# (their cached payloads have different shapes). Bump ``v1`` on key changes.
STRUCTURED_CACHE_KEY_PREFIX: Final[str] = "claude:struct:v1:sha256:"

# Directory holding Jinja2 prompt templates.
PROMPTS_DIR: Final[Path] = Path(__file__).parent / "prompts"

# Default completion length. Override per call when a task genuinely needs more.
DEFAULT_MAX_TOKENS: Final[int] = 4096

# Default temperature. Pinned to 0.0 so identical inputs yield identical
# outputs — required for the Redis cache to be a correctness win, not a lie.
DEFAULT_TEMPERATURE: Final[float] = 0.0

# Models that deprecate the ``temperature`` request parameter and 400 if it
# is sent at all (even 0.0). For these we omit ``temperature`` from the API
# call — the cache key still carries it, which is harmless since the key only
# needs to be stable, and we cache the actual response either way.
MODELS_WITHOUT_TEMPERATURE: Final[frozenset[ClaudeModel]] = frozenset({ClaudeModel.OPUS})


class ClaudeResponseFormat(StrEnum):
    """How ``call_claude`` should interpret the model's response."""

    TEXT = "text"
    JSON = "json"


class ClaudeMessage(TypedDict):
    """Shape of a single message in the ``messages`` list."""

    role: Literal["user", "assistant"]
    content: str


class AnthropicKeyMissingError(RuntimeError):
    """Raised when ``call_claude`` is invoked but ``ANTHROPIC_API_KEY`` is not set."""


class ClaudeJSONParseError(RuntimeError):
    """Raised when ``response_format=JSON`` but the response isn't valid JSON."""

    def __init__(self, message: str, *, raw: str) -> None:
        super().__init__(message)
        self.raw = raw


class ClaudeStructuredOutputError(RuntimeError):
    """Raised when ``call_claude_structured`` cannot bind the response to its schema.

    With tool-use structured output the model is supposed to call the tool
    whose arguments match the schema; if it doesn't (or the arguments fail
    validation) LangChain surfaces a ``parsing_error`` and ``parsed`` is
    ``None``.

    ``raw_args`` holds the model's tool-call arguments (a plain dict, possibly
    empty) so callers that want best-effort *salvage* — keep whatever fields
    validate in isolation — can inspect the unbound output. ``parsing_error``
    is the underlying validation error. Neither is included in the exception
    message (the message is safe to log; ``raw_args``/``parsing_error`` may
    carry response content, which must never be logged).
    """

    def __init__(
        self,
        message: str,
        *,
        raw_args: dict[str, Any] | None = None,
        parsing_error: Any = None,
    ) -> None:
        super().__init__(message)
        self.raw_args: dict[str, Any] = raw_args or {}
        self.parsing_error = parsing_error


# --- Lazy factories ---------------------------------------------------------
# Async clients are NOT cached: their internal httpx / aioredis connection
# pool is bound to the event loop that first used them. Celery tasks each
# call ``asyncio.run(...)`` which creates a fresh loop, so a cached client
# from a previous task would raise "Event loop is closed" on the next task.
# Constructing per call costs ~1 TLS handshake — acceptable at queue scale.
# Tests patch these via ``monkeypatch``.


def _chat_model(
    *,
    model: ClaudeModel,
    max_tokens: int,
    temperature: float,
) -> ChatAnthropic:
    """Construct a per-call ``ChatAnthropic`` chat model.

    Built fresh on every call (never cached): under Celery each task runs in
    its own ``asyncio.run`` loop, and a model whose httpx pool is bound to a
    prior, now-closed loop would raise "Event loop is closed" on reuse.

    Opus deprecates ``temperature`` and 400s if it is sent. ``ChatAnthropic``
    omits the parameter from the request when it is left as ``None``, so for
    ``MODELS_WITHOUT_TEMPERATURE`` we pass ``None`` rather than a value — the
    same effect as today's ``create_kwargs`` guard, but model-side.
    """
    if not settings.anthropic_api_key:
        raise AnthropicKeyMissingError("ANTHROPIC_API_KEY is not configured; cannot call Claude")
    effective_temperature = None if model in MODELS_WITHOUT_TEMPERATURE else temperature
    return ChatAnthropic(
        model=model.value,  # type: ignore[call-arg]  # langchain alias: model -> model_name
        api_key=settings.anthropic_api_key,
        max_tokens=max_tokens,
        max_retries=ANTHROPIC_MAX_RETRIES,
        temperature=effective_temperature,
    )


def _redis_client() -> aioredis.Redis:
    # redis.asyncio's stubs leave ``from_url`` untyped; cast + ignore so the
    # rest of the module stays under ``mypy --strict``.
    client = aioredis.from_url(  # type: ignore[no-untyped-call]
        settings.redis_url, decode_responses=False
    )
    return cast(aioredis.Redis, client)


@lru_cache(maxsize=1)
def _jinja_env() -> Environment:
    return Environment(
        loader=FileSystemLoader(PROMPTS_DIR),
        # Prompts are sent to Claude as plain text — HTML escaping would
        # mangle apostrophes, quotes, and angle brackets in JD/CV content.
        # This is not a web-rendering context, so XSS does not apply.
        autoescape=False,  # noqa: S701
        undefined=StrictUndefined,  # fail loudly on missing template vars
        trim_blocks=True,
        lstrip_blocks=True,
        keep_trailing_newline=True,
    )


# --- Helpers ----------------------------------------------------------------


def _cache_key(
    *,
    model: ClaudeModel,
    system: str,
    messages: list[ClaudeMessage],
    max_tokens: int,
    response_format: ClaudeResponseFormat,
    prompt_version: str | None,
    temperature: float,
) -> str:
    payload = json.dumps(
        {
            "model": model.value,
            "system": system,
            "messages": messages,
            "max_tokens": max_tokens,
            "response_format": response_format.value,
            "prompt_version": prompt_version,
            "temperature": temperature,
        },
        sort_keys=True,
        ensure_ascii=False,
    )
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    return CACHE_KEY_PREFIX + digest


def _structured_cache_key(
    *,
    model: ClaudeModel,
    messages: list[BaseMessage],
    max_tokens: int,
    schema: type[BaseModel],
    prompt_version: str | None,
    temperature: float,
) -> str:
    """Cache key for a structured call.

    Keys on the rendered messages (by type + content), the model params, the
    fully-qualified schema name (so a prompt rebound to a different schema is a
    different slot), and ``prompt_version``. Mirrors ``_cache_key`` but lives
    in the ``STRUCTURED_CACHE_KEY_PREFIX`` namespace.
    """
    payload = json.dumps(
        {
            "model": model.value,
            "messages": [{"type": m.type, "content": m.content} for m in messages],
            "max_tokens": max_tokens,
            "schema": f"{schema.__module__}.{schema.__qualname__}",
            "prompt_version": prompt_version,
            "temperature": temperature,
        },
        sort_keys=True,
        ensure_ascii=False,
    )
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    return STRUCTURED_CACHE_KEY_PREFIX + digest


def _strip_code_fence(text: str) -> str:
    """Remove a leading/trailing ```json ... ``` (or bare ``` ... ```) fence."""
    s = text.strip()
    if not s.startswith("```"):
        return s
    # Drop the opening fence line (``` or ```json or ```anything).
    newline = s.find("\n")
    if newline == -1:
        return s
    body = s[newline + 1 :]
    # Drop the trailing fence, if present.
    if body.rstrip().endswith("```"):
        body = body.rstrip()[: -len("```")]
    return body.strip()


def _parse_json_response(raw: str) -> dict[str, Any]:
    stripped = _strip_code_fence(raw)
    try:
        parsed = json.loads(stripped)
    except json.JSONDecodeError as exc:
        raise ClaudeJSONParseError(
            f"Claude response was not valid JSON: {exc.msg}",
            raw=raw,
        ) from exc
    if not isinstance(parsed, dict):
        raise ClaudeJSONParseError(
            f"Claude JSON response was {type(parsed).__name__}, expected object",
            raw=raw,
        )
    return cast(dict[str, Any], parsed)


def _extract_tool_args(raw: Any) -> dict[str, Any]:
    """Pull the model's tool-call arguments from an ``include_raw`` response.

    On a structured-output bind failure the parsed model is ``None`` but the
    raw ``AIMessage`` still carries the tool call the model attempted; its
    ``args`` is the dict we hand to salvage. Returns ``{}`` when the model made
    no tool call (e.g. answered with plain text).
    """
    tool_calls = getattr(raw, "tool_calls", None) or []
    if tool_calls and isinstance(tool_calls[0], dict):
        args = tool_calls[0].get("args")
        if isinstance(args, dict):
            return cast(dict[str, Any], args)
    return {}


def _to_lc_messages(system: str, messages: list[ClaudeMessage]) -> list[BaseMessage]:
    """Convert our ``(system, messages)`` shape into LangChain messages.

    The system prompt becomes a leading ``SystemMessage``; each conversation
    turn maps to ``HumanMessage`` (user) or ``AIMessage`` (assistant).
    """
    lc_messages: list[BaseMessage] = [SystemMessage(content=system)]
    for message in messages:
        if message["role"] == "user":
            lc_messages.append(HumanMessage(content=message["content"]))
        else:
            lc_messages.append(AIMessage(content=message["content"]))
    return lc_messages


def _extract_text(response: BaseMessage) -> str:
    """Concatenate all text from a LangChain message response.

    ``content`` is a plain ``str`` for simple text completions, or a list of
    content blocks (str or ``{"type": "text", "text": ...}`` dicts) when the
    model returns structured blocks. Non-text blocks are skipped — we only
    request text completions.
    """
    content = response.content
    if isinstance(content, str):
        return content
    pieces: list[str] = []
    for block in content:
        if isinstance(block, str):
            pieces.append(block)
        elif isinstance(block, dict):
            text = block.get("text")
            if isinstance(text, str):
                pieces.append(text)
    return "".join(pieces)


# --- Public API -------------------------------------------------------------


def render_prompt(template_name: str, **variables: Any) -> str:
    """Render a Jinja2 template from ``src/ai/prompts/``.

    ``StrictUndefined`` means any missing variable raises ``UndefinedError``
    rather than silently rendering as an empty string — we'd rather crash a
    request than send Claude a half-built prompt.
    """
    env = _jinja_env()
    template = env.get_template(template_name)
    return template.render(**variables)


def load_chat_prompt(*, system: str, human_template_file: str) -> ChatPromptTemplate:
    """Build a ``ChatPromptTemplate`` from a system string + a ``.j2`` file.

    The human-turn text is read verbatim from ``src/ai/prompts/<file>`` and
    rendered with ``template_format="jinja2"`` so existing prompt wording —
    loops, conditionals, includes — carries over unchanged. The prompt files
    stay the single source of truth for prompt content; only the rendering
    engine moves from raw Jinja to LangChain.

    Fail-loud parity with ``StrictUndefined`` is preserved: LangChain
    validates ``input_variables`` before rendering and raises ``KeyError`` if
    a declared variable is missing at invoke time, rather than silently
    rendering an empty string.

    The ``system`` turn is passed through as a literal (no templating) — it is
    a constant per call site, never interpolated — so any stray braces in it
    cannot be misread as template variables.
    """
    human_template = (PROMPTS_DIR / human_template_file).read_text(encoding="utf-8")
    return ChatPromptTemplate.from_messages(
        [
            # Literal system turn (no templating) so braces in it can't be
            # mistaken for variables.
            SystemMessage(content=system),
            HumanMessagePromptTemplate.from_template(human_template, template_format="jinja2"),
        ]
    )


async def call_claude(
    *,
    model: ClaudeModel,
    system: str,
    messages: list[ClaudeMessage],
    max_tokens: int = DEFAULT_MAX_TOKENS,
    response_format: ClaudeResponseFormat = ClaudeResponseFormat.TEXT,
    prompt_version: str | None = None,
    temperature: float = DEFAULT_TEMPERATURE,
    use_cache: bool = True,
) -> str | dict[str, Any]:
    """Call Claude with caching, retries, and optional JSON parsing.

    Args:
        model: Which Claude model to use. Pick ``ClaudeModel.SONNET`` for
            routine work; ``ClaudeModel.OPUS`` for judgment-heavy reasoning.
        system: System prompt.
        messages: Conversation turns. Must be non-empty.
        max_tokens: Hard cap on response length.
        response_format: ``TEXT`` returns a ``str``; ``JSON`` parses the
            response (stripping code fences) and returns a ``dict``.
        prompt_version: Optional tag included in the cache key. Bump when a
            prompt's semantics change so old cached responses aren't served.
        temperature: Sampling temperature. Defaults to 0.0 so caching is sound.
        use_cache: When ``False`` skip BOTH the Redis lookup and the
            Redis write. Step 4.4's rescore endpoint uses this to force
            a fresh Claude call without polluting the cache with the
            duplicate; the next non-rescore call still gets the
            originally-cached response.

    Raises:
        ValueError: if ``messages`` is empty.
        AnthropicKeyMissingError: if ``ANTHROPIC_API_KEY`` is not configured.
        ClaudeJSONParseError: if ``response_format=JSON`` and the response
            cannot be parsed as a JSON object.
    """
    if not messages:
        raise ValueError("messages must be non-empty")

    key = _cache_key(
        model=model,
        system=system,
        messages=messages,
        max_tokens=max_tokens,
        response_format=response_format,
        prompt_version=prompt_version,
        temperature=temperature,
    )

    redis = _redis_client()
    if use_cache:
        cached = await redis.get(key)
        if cached is not None:
            logger.info(
                "claude.cache_hit",
                extra={
                    "model": model.value,
                    "prompt_version": prompt_version,
                    "key_prefix": CACHE_KEY_PREFIX,
                },
            )
            decoded = cached.decode("utf-8") if isinstance(cached, bytes) else cached
            payload = json.loads(decoded)
            if response_format is ClaudeResponseFormat.JSON:
                return cast(dict[str, Any], payload)
            return cast(str, payload)

    # ``_chat_model`` bakes the per-model temperature handling (Opus omits it)
    # into the ``ChatAnthropic`` instance; ``ainvoke`` issues the call with the
    # underlying SDK's retry on transient errors.
    chat = _chat_model(model=model, max_tokens=max_tokens, temperature=temperature)
    response = await chat.ainvoke(_to_lc_messages(system, messages))

    raw_text = _extract_text(response)
    usage = getattr(response, "usage_metadata", None)
    logger.info(
        "claude.api_call",
        extra={
            "model": model.value,
            "max_tokens": max_tokens,
            "prompt_version": prompt_version,
            "input_tokens": usage.get("input_tokens") if isinstance(usage, dict) else None,
            "output_tokens": usage.get("output_tokens") if isinstance(usage, dict) else None,
            "cache_bypassed": not use_cache,
        },
    )

    result: str | dict[str, Any]
    if response_format is ClaudeResponseFormat.JSON:
        result = _parse_json_response(raw_text)
    else:
        result = raw_text

    if use_cache:
        await redis.set(key, json.dumps(result), ex=CACHE_TTL_SECONDS)
    return result


async def call_claude_structured[StructuredT: BaseModel](
    *,
    model: ClaudeModel,
    prompt: ChatPromptTemplate,
    variables: dict[str, Any],
    schema: type[StructuredT],
    max_tokens: int = DEFAULT_MAX_TOKENS,
    prompt_version: str | None = None,
    temperature: float = DEFAULT_TEMPERATURE,
    use_cache: bool = True,
) -> StructuredT:
    """Call Claude and return a validated ``schema`` instance via structured output.

    Renders ``prompt`` with ``variables`` into chat messages, then invokes
    ``ChatAnthropic.with_structured_output(schema)`` so Claude returns its
    answer as a tool call whose arguments match ``schema`` — replacing the
    free-text-JSON + code-fence-stripping path with validated tool output.

    Caching, retries, temperature handling, and PII-safe logging are identical
    in spirit to :func:`call_claude`: the result is cached as the schema's JSON
    under a dedicated key namespace, ``use_cache=False`` bypasses both lookup
    and write (for force-rescore), and only model + token counts +
    ``prompt_version`` are logged — never prompt or response content.

    Args:
        prompt: A ``ChatPromptTemplate`` (e.g. from :func:`load_chat_prompt`).
        variables: Values for the template's declared variables. A missing
            variable raises ``KeyError`` at render time (fail-loud parity with
            ``StrictUndefined``).
        schema: The Pydantic model the response must conform to; also returned.

    Raises:
        AnthropicKeyMissingError: if ``ANTHROPIC_API_KEY`` is not configured.
        ClaudeStructuredOutputError: if the model's response cannot be bound to
            ``schema``.
    """
    lc_messages = prompt.invoke(variables).to_messages()

    key = _structured_cache_key(
        model=model,
        messages=lc_messages,
        max_tokens=max_tokens,
        schema=schema,
        prompt_version=prompt_version,
        temperature=temperature,
    )

    redis = _redis_client()
    if use_cache:
        cached = await redis.get(key)
        if cached is not None:
            logger.info(
                "claude.cache_hit",
                extra={
                    "model": model.value,
                    "prompt_version": prompt_version,
                    "key_prefix": STRUCTURED_CACHE_KEY_PREFIX,
                },
            )
            decoded = cached.decode("utf-8") if isinstance(cached, bytes) else cached
            return schema.model_validate_json(decoded)

    chat = _chat_model(model=model, max_tokens=max_tokens, temperature=temperature)
    # ``include_raw=True`` returns {"raw": AIMessage, "parsed": schema | None,
    # "parsing_error": ...} so we keep token-count logging from the raw message
    # while getting the validated model.
    structured = chat.with_structured_output(schema, include_raw=True)
    response = cast(dict[str, Any], await structured.ainvoke(lc_messages))

    raw = response.get("raw")
    usage = getattr(raw, "usage_metadata", None)
    logger.info(
        "claude.api_call",
        extra={
            "model": model.value,
            "max_tokens": max_tokens,
            "prompt_version": prompt_version,
            "input_tokens": usage.get("input_tokens") if isinstance(usage, dict) else None,
            "output_tokens": usage.get("output_tokens") if isinstance(usage, dict) else None,
            "cache_bypassed": not use_cache,
        },
    )

    parsed = response.get("parsed")
    if not isinstance(parsed, schema):
        # Message stays content-free (safe to log); the failure detail and the
        # model's raw tool args ride on attributes for salvage / debugging.
        raise ClaudeStructuredOutputError(
            f"Claude response did not bind to {schema.__name__}",
            raw_args=_extract_tool_args(raw),
            parsing_error=response.get("parsing_error"),
        )

    if use_cache:
        await redis.set(key, parsed.model_dump_json(), ex=CACHE_TTL_SECONDS)
    return parsed
