"""Unit tests for the Claude SDK wrapper.

The ``ChatAnthropic`` model, Redis, and Jinja are all stubbed — no network,
no filesystem. The fakes capture inputs so we can assert on cache-key
behaviour, model construction, and JSON-parsing edge cases.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from jinja2 import DictLoader, Environment, StrictUndefined, TemplateNotFound, UndefinedError
from langchain_core.prompts import ChatPromptTemplate
from pydantic import BaseModel

from src.ai import claude
from src.enums.claude_models import ClaudeModel


class _FakeChatModel:
    """Stand-in for a ``ChatAnthropic`` instance returned by ``_chat_model``.

    ``ainvoke`` returns a message-like namespace exposing ``content`` (str)
    and ``usage_metadata`` (dict), matching the subset the wrapper reads.
    """

    def __init__(self, *, text: str | None = None, texts: list[str] | None = None) -> None:
        self._texts = texts if texts is not None else [text or ""]
        self._next = 0
        self.invocations: list[list[Any]] = []

    async def ainvoke(self, messages: list[Any]) -> SimpleNamespace:
        self.invocations.append(messages)
        text = self._texts[min(self._next, len(self._texts) - 1)]
        self._next += 1
        return SimpleNamespace(
            content=text,
            usage_metadata={"input_tokens": 12, "output_tokens": 34},
        )


class _FakeRedis:
    """In-memory async stand-in for the subset of redis.asyncio we use."""

    def __init__(self) -> None:
        self.store: dict[str, bytes] = {}
        self.get_calls = 0
        self.set_calls: list[tuple[str, bytes, int | None]] = []

    async def get(self, key: str) -> bytes | None:
        self.get_calls += 1
        return self.store.get(key)

    async def set(self, key: str, value: bytes | str, ex: int | None = None) -> None:
        v = value if isinstance(value, bytes) else value.encode("utf-8")
        self.store[key] = v
        self.set_calls.append((key, v, ex))


def _patch_clients(
    monkeypatch: pytest.MonkeyPatch,
    *,
    text: str | None = None,
    texts: list[str] | None = None,
) -> tuple[_FakeChatModel, _FakeRedis]:
    chat_model = _FakeChatModel(text=text, texts=texts)
    redis_client = _FakeRedis()
    # ``_chat_model`` is the per-call factory; return the same fake instance
    # regardless of (model, max_tokens, temperature) so invocation counts and
    # response scripting are deterministic.
    monkeypatch.setattr(claude, "_chat_model", lambda **_kwargs: chat_model)
    monkeypatch.setattr(claude, "_redis_client", lambda: redis_client)
    return chat_model, redis_client


def _msg(role: str, content: str) -> claude.ClaudeMessage:
    # Helper to keep test bodies readable. TypedDict cast is fine — tests
    # only construct ``user`` / ``assistant`` messages.
    return {"role": role, "content": content}  # type: ignore[typeddict-item]


# --- Return-type tests ------------------------------------------------------


async def test_returns_text_for_text_format(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_clients(monkeypatch, text="hello world")
    result = await claude.call_claude(
        model=ClaudeModel.SONNET,
        system="be helpful",
        messages=[_msg("user", "say hi")],
    )
    assert result == "hello world"


async def test_returns_dict_for_json_format(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_clients(monkeypatch, text='{"answer": 42, "ok": true}')
    result = await claude.call_claude(
        model=ClaudeModel.SONNET,
        system="return JSON",
        messages=[_msg("user", "give me JSON")],
        response_format=claude.ClaudeResponseFormat.JSON,
    )
    assert result == {"answer": 42, "ok": True}


async def test_json_strips_code_block_wrappers(monkeypatch: pytest.MonkeyPatch) -> None:
    # Claude commonly wraps JSON in ```json ... ``` or bare ``` ... ``` fences.
    for raw in (
        '```json\n{"k": 1}\n```',
        '```\n{"k": 1}\n```',
        '   ```json\n{"k": 1}\n```   ',
    ):
        _patch_clients(monkeypatch, text=raw)
        result = await claude.call_claude(
            model=ClaudeModel.SONNET,
            system="json please",
            messages=[_msg("user", "go")],
            response_format=claude.ClaudeResponseFormat.JSON,
        )
        assert result == {"k": 1}


async def test_bad_json_raises_parse_error(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_clients(monkeypatch, text="not actually json {{{")
    with pytest.raises(claude.ClaudeJSONParseError) as excinfo:
        await claude.call_claude(
            model=ClaudeModel.SONNET,
            system="json please",
            messages=[_msg("user", "go")],
            response_format=claude.ClaudeResponseFormat.JSON,
        )
    assert excinfo.value.raw == "not actually json {{{"


# --- Cache behaviour --------------------------------------------------------


async def test_cache_hit_short_circuits_api(monkeypatch: pytest.MonkeyPatch) -> None:
    chat_model, redis_client = _patch_clients(monkeypatch, text="cached-answer")

    first = await claude.call_claude(
        model=ClaudeModel.SONNET,
        system="sys",
        messages=[_msg("user", "u")],
    )
    assert len(chat_model.invocations) == 1
    assert len(redis_client.set_calls) == 1

    second = await claude.call_claude(
        model=ClaudeModel.SONNET,
        system="sys",
        messages=[_msg("user", "u")],
    )
    assert second == first == "cached-answer"
    assert len(chat_model.invocations) == 1, "Claude must not be called on cache hit"


async def test_cache_set_uses_seven_day_ttl(monkeypatch: pytest.MonkeyPatch) -> None:
    _, redis_client = _patch_clients(monkeypatch, text="ok")
    await claude.call_claude(
        model=ClaudeModel.SONNET,
        system="sys",
        messages=[_msg("user", "u")],
    )
    assert redis_client.set_calls
    _key, _value, ex = redis_client.set_calls[0]
    assert ex == claude.CACHE_TTL_SECONDS
    assert ex == 7 * 24 * 60 * 60


async def test_cache_key_includes_model(monkeypatch: pytest.MonkeyPatch) -> None:
    _, redis_client = _patch_clients(monkeypatch, texts=["sonnet-resp", "opus-resp"])

    await claude.call_claude(
        model=ClaudeModel.SONNET,
        system="sys",
        messages=[_msg("user", "u")],
    )
    await claude.call_claude(
        model=ClaudeModel.OPUS,
        system="sys",
        messages=[_msg("user", "u")],
    )

    keys = {entry[0] for entry in redis_client.set_calls}
    assert len(keys) == 2, "different models must produce different cache keys"
    for k in keys:
        assert k.startswith(claude.CACHE_KEY_PREFIX)


def test_chat_model_opus_omits_temperature_but_sonnet_sends_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Opus 4.7 400s if ``temperature`` is sent at all, so ``_chat_model``
    leaves it ``None`` for Opus (``ChatAnthropic`` then omits it from the
    request) while still passing the value for models that accept it."""
    monkeypatch.setattr(claude.settings, "anthropic_api_key", "test-key")

    sonnet = claude._chat_model(model=ClaudeModel.SONNET, max_tokens=128, temperature=0.0)
    opus = claude._chat_model(model=ClaudeModel.OPUS, max_tokens=128, temperature=0.0)

    assert sonnet.temperature == 0.0
    assert opus.temperature is None
    # max_retries and model id flow through unchanged.
    assert opus.max_retries == claude.ANTHROPIC_MAX_RETRIES
    assert sonnet.model == ClaudeModel.SONNET.value


async def test_cache_key_includes_prompt_version(monkeypatch: pytest.MonkeyPatch) -> None:
    _, redis_client = _patch_clients(monkeypatch, texts=["v-none", "v2"])

    await claude.call_claude(
        model=ClaudeModel.SONNET,
        system="sys",
        messages=[_msg("user", "u")],
        prompt_version=None,
    )
    await claude.call_claude(
        model=ClaudeModel.SONNET,
        system="sys",
        messages=[_msg("user", "u")],
        prompt_version="v2",
    )

    keys = {entry[0] for entry in redis_client.set_calls}
    assert len(keys) == 2, "different prompt_version must produce different cache keys"


# --- Input validation -------------------------------------------------------


async def test_empty_messages_raises_value_error(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_clients(monkeypatch, text="unused")
    with pytest.raises(ValueError, match="non-empty"):
        await claude.call_claude(
            model=ClaudeModel.SONNET,
            system="sys",
            messages=[],
        )


async def test_anthropic_key_missing_raises_clear_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """call_claude without an API key must raise AnthropicKeyMissingError,
    not crash inside the Anthropic SDK with a generic message."""
    monkeypatch.setattr(claude.settings, "anthropic_api_key", None)
    monkeypatch.setattr(claude, "_redis_client", lambda: _FakeRedis())
    with pytest.raises(claude.AnthropicKeyMissingError):
        await claude.call_claude(
            model=ClaudeModel.SONNET,
            system="sys",
            messages=[_msg("user", "u")],
        )


# --- Prompt rendering -------------------------------------------------------


def _patch_jinja(monkeypatch: pytest.MonkeyPatch, templates: dict[str, str]) -> None:
    env = Environment(
        loader=DictLoader(templates),
        autoescape=False,  # noqa: S701 — matches production env; prompts aren't HTML
        undefined=StrictUndefined,
        trim_blocks=True,
        lstrip_blocks=True,
        keep_trailing_newline=True,
    )
    monkeypatch.setattr(claude, "_jinja_env", lambda: env)


def test_render_prompt_renders_template(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_jinja(monkeypatch, {"greet.j2": "Hello, {{ name }}!"})
    assert claude.render_prompt("greet.j2", name="Kabil") == "Hello, Kabil!"


def test_render_prompt_missing_template_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_jinja(monkeypatch, {"only.j2": "x"})
    with pytest.raises(TemplateNotFound):
        claude.render_prompt("does-not-exist.j2")


def test_render_prompt_strict_undefined(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_jinja(monkeypatch, {"needs.j2": "Hello, {{ name }}!"})
    with pytest.raises(UndefinedError):
        claude.render_prompt("needs.j2")  # no ``name`` passed


# --- Step 4.4: use_cache=False bypasses Redis lookup AND write --------------


async def test_use_cache_false_skips_redis_lookup(monkeypatch: pytest.MonkeyPatch) -> None:
    """No cache get when use_cache=False; the Claude call still happens."""
    chat_model, redis_client = _patch_clients(monkeypatch, text="fresh response")

    result = await claude.call_claude(
        model=ClaudeModel.SONNET,
        system="sys",
        messages=[_msg("user", "u")],
        use_cache=False,
    )

    assert result == "fresh response"
    assert redis_client.get_calls == 0
    assert len(chat_model.invocations) == 1


async def test_use_cache_false_skips_redis_write(monkeypatch: pytest.MonkeyPatch) -> None:
    """No cache set when use_cache=False — the rescore stays out of cache."""
    _, redis_client = _patch_clients(monkeypatch, text="fresh response")

    await claude.call_claude(
        model=ClaudeModel.SONNET,
        system="sys",
        messages=[_msg("user", "u")],
        use_cache=False,
    )
    assert redis_client.set_calls == []


async def test_use_cache_false_does_not_serve_existing_cached_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A subsequent use_cache=False call ignores any cached entry from prior runs."""
    chat_model, redis_client = _patch_clients(monkeypatch, texts=["cached-original", "fresh"])

    # Prime the cache via a normal cached call.
    first = await claude.call_claude(
        model=ClaudeModel.SONNET,
        system="sys",
        messages=[_msg("user", "u")],
    )
    assert first == "cached-original"
    assert len(redis_client.set_calls) == 1

    # Bypass — should hit Claude again, NOT read from cache.
    second = await claude.call_claude(
        model=ClaudeModel.SONNET,
        system="sys",
        messages=[_msg("user", "u")],
        use_cache=False,
    )
    assert second == "fresh"
    assert len(chat_model.invocations) == 2
    # No second write (rescore doesn't pollute the cache with the
    # alternative response).
    assert len(redis_client.set_calls) == 1


async def test_use_cache_true_default_round_trips_via_redis(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Sanity guard: default behaviour still uses the cache."""
    chat_model, redis_client = _patch_clients(monkeypatch, text="hello")
    await claude.call_claude(
        model=ClaudeModel.SONNET,
        system="sys",
        messages=[_msg("user", "u")],
    )
    assert redis_client.get_calls == 1
    assert len(redis_client.set_calls) == 1
    assert len(chat_model.invocations) == 1


# --- load_chat_prompt -------------------------------------------------------


def test_load_chat_prompt_renders_jinja_and_literal_system() -> None:
    """The system turn is a literal; the human turn renders the .j2 file."""
    prompt = claude.load_chat_prompt(
        system="be precise",
        human_template_file="extract_contact_details.j2",
    )
    messages = prompt.invoke({"cv_text": "Jane Doe — jane@x.io"}).to_messages()
    assert messages[0].content == "be precise"
    assert "Jane Doe — jane@x.io" in messages[1].content


def test_load_chat_prompt_missing_variable_raises() -> None:
    """Fail-loud parity with StrictUndefined: a missing var raises, not empties."""
    prompt = claude.load_chat_prompt(
        system="sys",
        human_template_file="extract_contact_details.j2",
    )
    with pytest.raises(KeyError):
        prompt.invoke({})  # ``cv_text`` not supplied


# --- call_claude_structured -------------------------------------------------


class _DemoSchema(BaseModel):
    score: int
    label: str


class _FakeStructuredModel:
    """Stand-in for a ``ChatAnthropic`` used via ``with_structured_output``.

    ``with_structured_output`` returns ``self`` (acting as the runnable);
    ``ainvoke`` yields the ``include_raw`` envelope the wrapper expects.
    """

    def __init__(self, *, results: list[tuple[Any, ...]]) -> None:
        self._results = results
        self._next = 0
        self.invocations = 0
        self.schema: Any = None
        self.include_raw: bool | None = None

    def with_structured_output(
        self, schema: Any, *, include_raw: bool = False
    ) -> _FakeStructuredModel:
        self.schema = schema
        self.include_raw = include_raw
        return self

    async def ainvoke(self, messages: list[Any]) -> dict[str, Any]:
        self.invocations += 1
        entry = self._results[min(self._next, len(self._results) - 1)]
        parsed, error = entry[0], entry[1]
        tool_args = entry[2] if len(entry) > 2 else None
        self._next += 1
        tool_calls = [{"name": "schema", "args": tool_args}] if tool_args is not None else []
        return {
            "raw": SimpleNamespace(
                usage_metadata={"input_tokens": 5, "output_tokens": 7},
                tool_calls=tool_calls,
            ),
            "parsed": parsed,
            "parsing_error": error,
        }


def _patch_structured(
    monkeypatch: pytest.MonkeyPatch,
    *,
    results: list[tuple[Any, ...]],
) -> tuple[_FakeStructuredModel, _FakeRedis]:
    model = _FakeStructuredModel(results=results)
    redis_client = _FakeRedis()
    monkeypatch.setattr(claude, "_chat_model", lambda **_kwargs: model)
    monkeypatch.setattr(claude, "_redis_client", lambda: redis_client)
    return model, redis_client


def _demo_prompt() -> ChatPromptTemplate:
    return ChatPromptTemplate.from_messages(
        [("system", "judge"), ("human", "{{ q }}")],
        template_format="jinja2",
    )


async def test_structured_returns_validated_schema(monkeypatch: pytest.MonkeyPatch) -> None:
    model, _ = _patch_structured(monkeypatch, results=[(_DemoSchema(score=9, label="ok"), None)])
    result = await claude.call_claude_structured(
        model=ClaudeModel.HAIKU,
        prompt=_demo_prompt(),
        variables={"q": "rate this"},
        schema=_DemoSchema,
    )
    assert result == _DemoSchema(score=9, label="ok")
    assert model.schema is _DemoSchema
    assert model.include_raw is True


async def test_structured_caches_under_dedicated_namespace(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    model, redis_client = _patch_structured(
        monkeypatch, results=[(_DemoSchema(score=1, label="a"), None)]
    )
    first = await claude.call_claude_structured(
        model=ClaudeModel.HAIKU,
        prompt=_demo_prompt(),
        variables={"q": "x"},
        schema=_DemoSchema,
    )
    assert len(redis_client.set_calls) == 1
    key = redis_client.set_calls[0][0]
    assert key.startswith(claude.STRUCTURED_CACHE_KEY_PREFIX)

    # Second identical call is served from cache — model not invoked again.
    second = await claude.call_claude_structured(
        model=ClaudeModel.HAIKU,
        prompt=_demo_prompt(),
        variables={"q": "x"},
        schema=_DemoSchema,
    )
    assert second == first
    assert model.invocations == 1


async def test_structured_use_cache_false_bypasses_redis(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    model, redis_client = _patch_structured(
        monkeypatch, results=[(_DemoSchema(score=2, label="b"), None)]
    )
    await claude.call_claude_structured(
        model=ClaudeModel.HAIKU,
        prompt=_demo_prompt(),
        variables={"q": "x"},
        schema=_DemoSchema,
        use_cache=False,
    )
    assert redis_client.get_calls == 0
    assert redis_client.set_calls == []
    assert model.invocations == 1


async def test_structured_unbound_response_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    """parsed=None (model failed to call the tool) raises a clear error."""
    _patch_structured(monkeypatch, results=[(None, ValueError("no tool call"))])
    with pytest.raises(claude.ClaudeStructuredOutputError):
        await claude.call_claude_structured(
            model=ClaudeModel.HAIKU,
            prompt=_demo_prompt(),
            variables={"q": "x"},
            schema=_DemoSchema,
        )


async def test_structured_error_carries_raw_tool_args(monkeypatch: pytest.MonkeyPatch) -> None:
    """On bind failure the model's raw tool args ride on the error for salvage."""
    bad_args = {"score": "not-an-int", "label": "x", "extra": 1}
    _patch_structured(monkeypatch, results=[(None, ValueError("bad"), bad_args)])
    with pytest.raises(claude.ClaudeStructuredOutputError) as excinfo:
        await claude.call_claude_structured(
            model=ClaudeModel.HAIKU,
            prompt=_demo_prompt(),
            variables={"q": "x"},
            schema=_DemoSchema,
        )
    assert excinfo.value.raw_args == bad_args
    # The message itself stays content-free (safe to log).
    assert "not-an-int" not in str(excinfo.value)
