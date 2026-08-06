"""Email rendering + send orchestration (Step 6.4).

Sits between the transport (:mod:`src.integrations.email_client`) and the
callers (Celery tasks). It owns the Jinja2 environment over
``src/emails/templates`` and turns a template *base name* + context into a
:class:`RenderedEmail` (subject + HTML + plain text), then hands it to the
transport.

Each logical email is three sibling templates sharing a base name:

    <base>.subject.txt   one-line subject (whitespace-trimmed)
    <base>.html          HTML body (autoescaped)
    <base>.txt           plain-text body (not autoescaped)

Templates use :class:`~jinja2.StrictUndefined`, so a missing context variable
fails loud at render time rather than silently producing an empty span — the
same contract the AI prompt loader uses.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

from jinja2 import Environment, FileSystemLoader, StrictUndefined, select_autoescape

from src.integrations import email_client

_TEMPLATES_DIR: Final[Path] = Path(__file__).resolve().parent.parent / "emails" / "templates"

# Autoescape only the HTML templates (by ``.html`` extension); the ``.txt`` and
# ``.subject.txt`` parts are plain text and must not be HTML-escaped, or an
# ampersand in a job title would render as ``&amp;`` in the inbox.
_env: Final[Environment] = Environment(
    loader=FileSystemLoader(_TEMPLATES_DIR),
    undefined=StrictUndefined,
    autoescape=select_autoescape(enabled_extensions=("html",), default=False),
    trim_blocks=True,
    lstrip_blocks=True,
)


@dataclass(frozen=True, slots=True)
class RenderedEmail:
    """A fully-rendered message ready for the transport."""

    subject: str
    html_body: str
    text_body: str


def render_email(template_base: str, context: dict[str, Any]) -> RenderedEmail:
    """Render the three sibling templates for ``template_base`` into one email.

    ``template_base`` is the shared stem, e.g. ``"interview_invite"`` →
    ``interview_invite.subject.txt`` / ``.html`` / ``.txt``. The subject is
    stripped of surrounding whitespace so a trailing template newline doesn't
    leak into the header.
    """
    subject = _env.get_template(f"{template_base}.subject.txt").render(context).strip()
    html_body = _env.get_template(f"{template_base}.html").render(context)
    text_body = _env.get_template(f"{template_base}.txt").render(context)
    return RenderedEmail(subject=subject, html_body=html_body, text_body=text_body)


async def send_rendered_email(*, to: str, rendered: RenderedEmail, category: str) -> None:
    """Deliver a :class:`RenderedEmail` via the SMTP transport.

    ``category`` is the PII-free log tag forwarded to the transport (the
    subject/bodies are never logged). Propagates
    :class:`~src.integrations.email_client.EmailConfigError` /
    :class:`~src.integrations.email_client.EmailSendError` to the caller.
    """
    await email_client.send_email(
        to=to,
        subject=rendered.subject,
        html_body=rendered.html_body,
        text_body=rendered.text_body,
        category=category,
    )
