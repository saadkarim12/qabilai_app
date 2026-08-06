"""Unit tests for ``src.utils.sanitization``."""

from __future__ import annotations

from src.utils.sanitization import strip_html


def test_plain_text_unchanged() -> None:
    text = "Senior Backend Engineer based in Dubai."
    assert strip_html(text) == text


def test_script_tag_stripped() -> None:
    out = strip_html('Hello <script>alert("xss")</script> world')
    assert "<script>" not in out
    assert "</script>" not in out
    assert "Hello" in out
    assert "world" in out


def test_style_tag_stripped() -> None:
    out = strip_html("<style>body{color:red}</style>Real text")
    assert "<style>" not in out
    assert "Real text" in out


def test_iframe_stripped() -> None:
    out = strip_html('<iframe src="http://evil"></iframe>safe')
    assert "<iframe" not in out
    assert "safe" in out


def test_inline_event_handler_attr_stripped() -> None:
    # bleach removes the whole element (including attributes) when the tag
    # isn't in the allow-list; ``strip=True`` keeps text content.
    out = strip_html('<a href="x" onclick="evil()">Click me</a>')
    assert "onclick" not in out
    assert "Click me" in out


def test_newlines_preserved() -> None:
    out = strip_html("line1\nline2\nline3")
    assert out == "line1\nline2\nline3"


def test_empty_string_passes_through() -> None:
    assert strip_html("") == ""


def test_unknown_tag_like_strings_are_dropped() -> None:
    # Pins the trade-off: bleach treats anything that *looks* like a tag
    # (e.g. ``<str>``) as one and drops it under ``strip=True``. Authors
    # writing JDs that contain ``List<str>`` will see the generic stripped.
    # If that becomes painful, switch to escaping rather than stripping in
    # ``strip_html`` — for now we err on the side of safety.
    out = strip_html("Use generics like List<str> in your code.")
    assert "<str>" not in out
    assert "Use generics" in out
    assert "in your code." in out
