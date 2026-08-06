# Interview Scheduling (Calendly + email) — Step 6.4

When HR moves an application into the **`interview`** stage, the backend emails
the candidate a single-use Calendly link to self-book against the shared
"Interview" availability. When the candidate books, Calendly natively emails
both the candidate and the host the calendar invite (.ics); the backend records
the slot and nudges HR's live stream.

## Flow

```
HR: PATCH /applications/{id}/stage  (stage → interview)
      │  (only if INTERVIEW_SCHEDULING_ENABLED)
      ▼
send_interview_invite_task
      │  mint single-use Calendly link  → append ?utm_content=<booking_token>
      │  email candidate (SMTP)          → record InterviewBooking(state=invited)
      │  schedule reminder (+INTERVIEW_REMINDER_HOURS) and timeout (+INTERVIEW_REPLY_TIMEOUT_HOURS)
      ▼
candidate books in Calendly
      ▼
POST /webhooks/calendly  (invitee.created, HMAC-signed)
      │  verify signature → parse → match booking_token
      ▼
handle_calendly_event_task → InterviewBooking(state=booked, scheduled_start/end)
      │  Calendly emails the .ics to candidate + host (we send nothing)
      ▼
SSE: AppEventType.INTERVIEW  → FE refetches GET /applications/{id} → `interview` block
```

- **Booking keeps `stage=interview`** (booking ≠ interview completed); HR advances
  to `done` manually after the interview.
- **Reschedule / cancel** (`invitee.canceled`) updates the booking + nudges HR;
  the stage is left untouched.
- **Unbooked**: one reminder email at `INTERVIEW_REMINDER_HOURS`; at
  `INTERVIEW_REPLY_TIMEOUT_HOURS` the booking is flagged (`last_error=unbooked_timeout`)
  and an SSE nudge fires. No auto-rejection.
- **No candidate email**: the booking row is still created with
  `last_error=email_unavailable` so HR sees the stuck invite; nothing crashes.

## One-time setup

Requires a Calendly account on a **paid plan with API access** (single-use links
+ webhook subscriptions are gated behind it) and a public HTTPS URL for this
backend.

1. **Create the event type** in Calendly — one shared "Interview" event whose
   availability reflects the interview calendar.

2. **Mint a Personal Access Token**: Calendly → Integrations → API & Webhooks →
   Personal Access Tokens. Put it in `.env` as `CALENDLY_PERSONAL_ACCESS_TOKEN`.

3. **Discover URIs** (read-only):

   ```bash
   make calendly-setup        # = python -m scripts.calendly_setup discover
   ```

   Copy the printed `CALENDLY_ORGANIZATION_URI` and the "Interview" event's
   `CALENDLY_EVENT_TYPE_URI` into `.env`.

4. **Register the webhook** (writes to Calendly — run once per environment):

   ```bash
   .venv/bin/python -m scripts.calendly_setup register \
       --callback-url https://api.your-domain.com/webhooks/calendly
   ```

   Store the printed `CALENDLY_WEBHOOK_SIGNING_KEY` in `.env`.

5. **Configure SMTP** (`SMTP_HOST`/`SMTP_PORT`/`SMTP_USER`/`SMTP_PASS`/`SMTP_FROM`)
   for the invite + reminder emails.

6. Set **`INTERVIEW_SCHEDULING_ENABLED=true`** and restart the API + Celery
   worker.

While `INTERVIEW_SCHEDULING_ENABLED=false`, the stage→interview hook is a no-op
and `POST /webhooks/calendly` returns 404 — so the rest of the pipeline runs
unchanged on a deployment without Calendly configured.

## Config reference

| Env var | Purpose |
|---|---|
| `CALENDLY_PERSONAL_ACCESS_TOKEN` | Authorizes Calendly API calls |
| `CALENDLY_EVENT_TYPE_URI` | Shared "Interview" event type links are minted against |
| `CALENDLY_ORGANIZATION_URI` | Scope for the webhook subscription |
| `CALENDLY_WEBHOOK_SIGNING_KEY` | HMAC key validating inbound webhooks (we generate it) |
| `INTERVIEW_SCHEDULING_ENABLED` | Master switch for the whole feature |
| `INTERVIEW_REMINDER_HOURS` | Hours after invite before the single reminder (default 24) |
| `INTERVIEW_REPLY_TIMEOUT_HOURS` | Hours after invite before HR is flagged (default 48) |
| `SMTP_*` | Transport for the invite + reminder emails |

## Security notes

- The webhook is anonymous but authenticated by the `Calendly-Webhook-Signature`
  HMAC over the raw body (fail-closed in production, warn-and-allow in dev when
  the key is unset). The booking link carries an opaque random `booking_token`,
  not the internal application id.
- Webhook + email bodies carry candidate PII and are **never logged** — only
  event kinds, Calendly resource URIs, and recipient addresses appear.
