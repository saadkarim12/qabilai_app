"""HTML sanitization for user-supplied long text.

``strip_html`` is the single chokepoint between an HR-submitted
``job_description`` and the database. It uses ``bleach`` with an empty
allow-list and ``strip=True`` so every tag is removed (not escaped),
leaving plain text suitable for storage and downstream model prompts.

We deliberately keep this thin — sanitizing on write is sufficient
because we never render the field as HTML. Anything that needs richer
formatting later (e.g. preserving newlines for prompts) should be handled
where the field is consumed, not here.
"""

from __future__ import annotations

from typing import cast

import bleach  # type: ignore[import-untyped]


def strip_html(text: str) -> str:
    """Remove every HTML tag from ``text``, returning the inner plain text.

    ``bleach.clean(..., tags=[], attributes={}, strip=True)`` discards
    ``<script>``, ``<style>``, event-handler attributes, and any other
    tag — the contents (text nodes) are kept. Existing newlines are
    preserved.
    """
    # bleach has no type stubs in this project; the return is always ``str``
    # per its docs, so a cast is safer than silencing all of bleach.
    return cast(str, bleach.clean(text, tags=[], attributes={}, strip=True))
