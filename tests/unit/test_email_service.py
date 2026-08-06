"""Unit tests for the email rendering service (Step 6.4)."""

from __future__ import annotations

import pytest
from jinja2 import UndefinedError

from src.services.email_service import RenderedEmail, render_email

_INVITE_CONTEXT = {
    "candidate_name": "Ada Lovelace",
    "job_title": "Senior Engineer",
    "company_name": "Kabil",
    "booking_url": "https://calendly.com/d/abc-123?utm_content=tok",
}


def test_render_interview_invite_populates_all_parts() -> None:
    email = render_email("interview_invite", _INVITE_CONTEXT)

    assert isinstance(email, RenderedEmail)
    # Subject is single-line and trimmed (no leading/trailing whitespace).
    assert email.subject == "Book your interview for Senior Engineer"
    assert email.subject == email.subject.strip()

    # Both bodies carry the personal booking link and address the candidate.
    for body in (email.html_body, email.text_body):
        assert "Ada Lovelace" in body
        assert "https://calendly.com/d/abc-123?utm_content=tok" in body
        assert "Senior Engineer" in body


def test_render_interview_reminder_is_distinct() -> None:
    reminder = render_email("interview_reminder", _INVITE_CONTEXT)
    invite = render_email("interview_invite", _INVITE_CONTEXT)

    assert "Reminder" in reminder.subject
    assert reminder.subject != invite.subject
    assert "https://calendly.com/d/abc-123?utm_content=tok" in reminder.text_body


def test_html_body_escapes_but_text_does_not() -> None:
    context = dict(_INVITE_CONTEXT, company_name="Brown & Co <Hiring>")
    email = render_email("interview_invite", context)

    # HTML part is autoescaped so the raw markup can't break the layout...
    assert "Brown &amp; Co &lt;Hiring&gt;" in email.html_body
    assert "Brown & Co <Hiring>" not in email.html_body
    # ...while the plain-text part keeps the literal characters.
    assert "Brown & Co <Hiring>" in email.text_body


def test_optional_company_name_is_omitted_when_absent() -> None:
    context = {k: v for k, v in _INVITE_CONTEXT.items() if k != "company_name"}
    context["company_name"] = ""
    email = render_email("interview_invite", context)

    # No dangling "at" with an empty company name.
    assert "at  " not in email.text_body
    assert "Senior Engineer" in email.text_body


def test_missing_context_variable_fails_loud() -> None:
    # StrictUndefined: a forgotten variable must raise, not render blank.
    with pytest.raises(UndefinedError):
        render_email("interview_invite", {"job_title": "X"})
