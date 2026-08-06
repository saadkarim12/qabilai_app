"""Bootstrap script: one-time Calendly setup for interview scheduling (Step 6.4).

Two subcommands, run with ``CALENDLY_PERSONAL_ACCESS_TOKEN`` set in the
environment (`.env`):

    # 1. Read-only discovery — print the org/user URIs + event types so you can
    #    pick the shared "Interview" event type and fill the env vars.
    .venv/bin/python -m scripts.calendly_setup discover

    # 2. Register the inbound webhook (writes to Calendly). Generates a signing
    #    key, subscribes to invitee.created / invitee.canceled, and prints the
    #    key for you to store as CALENDLY_WEBHOOK_SIGNING_KEY.
    .venv/bin/python -m scripts.calendly_setup register \
        --callback-url https://api.your-domain.com/webhooks/calendly

``discover`` makes no changes. ``register`` creates a webhook subscription —
re-running it creates a *second* subscription, so register once per environment
(delete stale ones in the Calendly UI / API if you re-run).

Everything printed here is operator configuration (URIs, event-type names, the
signing key) — never candidate PII.
"""

from __future__ import annotations

import argparse
import asyncio
import secrets
import sys
from typing import Final

from src.config import settings
from src.integrations import calendly_client
from src.integrations.calendly_client import CalendlyApiError, CalendlyConfigError

SIGNING_KEY_BYTES: Final[int] = 32


async def _discover() -> int:
    """Print the user/organization URIs and active event types."""
    user = await calendly_client.get_current_user()
    user_uri = user.get("uri")
    org_uri = user.get("current_organization")
    if not isinstance(user_uri, str) or not isinstance(org_uri, str):
        print("ERROR: /users/me did not return a uri + current_organization", file=sys.stderr)
        return 1

    print("Calendly account discovered:\n")
    print(f"  CALENDLY_ORGANIZATION_URI={org_uri}")
    print(f"  (user uri: {user_uri})\n")

    event_types = await calendly_client.list_event_types(user_uri)
    if not event_types:
        print("No active event types found. Create an 'Interview' event type first.")
        return 0

    print("Active event types — copy the right one into CALENDLY_EVENT_TYPE_URI:\n")
    for et in event_types:
        name = et.get("name", "(unnamed)")
        uri = et.get("uri", "(no uri)")
        duration = et.get("duration", "?")
        print(f"  - {name}  ({duration} min)")
        print(f"      CALENDLY_EVENT_TYPE_URI={uri}\n")
    return 0


async def _register(callback_url: str) -> int:
    """Create the invitee.* webhook subscription and print the signing key."""
    org_uri = settings.calendly_organization_uri
    if not org_uri:
        print(
            "ERROR: CALENDLY_ORGANIZATION_URI is not set. Run `discover` first and "
            "add it to your .env.",
            file=sys.stderr,
        )
        return 1
    if not callback_url.startswith("https://"):
        print("ERROR: --callback-url must be https (Calendly rejects non-TLS).", file=sys.stderr)
        return 1

    signing_key = secrets.token_urlsafe(SIGNING_KEY_BYTES)
    resource = await calendly_client.create_webhook_subscription(
        callback_url=callback_url,
        organization_uri=org_uri,
        signing_key=signing_key,
    )

    print("Webhook subscription created:\n")
    print(f"  subscription uri: {resource.get('uri', '(unknown)')}")
    print(f"  callback url:     {callback_url}\n")
    print("Store this signing key in your environment (shown once):\n")
    print(f"  CALENDLY_WEBHOOK_SIGNING_KEY={signing_key}\n")
    print("Then set INTERVIEW_SCHEDULING_ENABLED=true and restart the API + worker.")
    return 0


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="calendly_setup", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("discover", help="Print org/user URIs and event types (read-only).")
    register = sub.add_parser("register", help="Create the inbound webhook subscription.")
    register.add_argument(
        "--callback-url",
        required=True,
        help="Public https URL of this backend's /webhooks/calendly endpoint.",
    )
    return parser.parse_args(argv)


async def _run(args: argparse.Namespace) -> int:
    if not settings.calendly_personal_access_token:
        print(
            "ERROR: CALENDLY_PERSONAL_ACCESS_TOKEN is not set. Add it to your .env.",
            file=sys.stderr,
        )
        return 1
    try:
        if args.command == "discover":
            return await _discover()
        return await _register(args.callback_url)
    except (CalendlyConfigError, CalendlyApiError) as exc:
        print(f"ERROR: Calendly call failed: {exc}", file=sys.stderr)
        return 1


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(sys.argv[1:] if argv is None else argv)
    return asyncio.run(_run(args))


if __name__ == "__main__":
    raise SystemExit(main())
