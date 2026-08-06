# WhatsApp Production Setup

How to take the screening flow from dev (HMAC-injection + test number) to
production, where any candidate receives messages directly.

There are **two** independent blockers; both must be cleared:

1. **App + business are in dev mode / on the test number** → real candidate
   numbers can't be messaged at all. Fixed by Business Verification + a real
   production phone number + Live app mode.
2. **The 24h customer-service window** → even when live, you cannot send
   free-form text to a candidate who hasn't messaged you first. The screening
   **invite is the first contact**, so it must be a **pre-approved template**.
   Fixed by submitting the template below and setting the env vars.

---

## Part A — the message template (submit this first; approval is independent)

Submit in **WhatsApp Manager → Message Templates → Create template**.

| Field | Value |
|---|---|
| **Name** | `screening_invite` (lowercase + underscores only — Meta enforces this) |
| **Category** | **Utility** (transactional follow-up to an application). If Meta rejects as Utility, resubmit as **Marketing**. |
| **Language** | **English** → language code **`en`** (must match `META_WA_INVITE_TEMPLATE_LANGUAGE`) |

**Body** (mirrors the in-app greeting so the HR transcript matches what's sent):

```
Hi {{1}}! 👋

Thanks for applying for the *{{2}}* role at *{{3}}*.

Are you still available and interested in this opportunity?
```

**Body sample values** (Meta requires examples to approve):
`{{1}}` = `Ahmed`, `{{2}}` = `Backend Engineer`, `{{3}}` = `Qabil AI`

**Buttons** → add **Quick reply**, two of them, **in this exact order**:

1. `Yes`  ← must be first (index 0 → routing payload `interest_yes`)
2. `No`   ← must be second (index 1 → routing payload `interest_no`)

> ⚠️ Button order is load-bearing. The code sends the Yes/No routing payloads
> by index (`send_template(..., quick_reply_payloads=[interest_yes, interest_no])`).
> If you create No first, taps will route inverted.

Variable mapping in code (`begin_screening`): `body_params=[candidate.full_name,
job.title, job.hiring_company]` → `{{1}}/{{2}}/{{3}}`.

### After approval

Set on the API service (Railway env):

```
META_WA_INVITE_TEMPLATE_NAME=screening_invite
META_WA_INVITE_TEMPLATE_LANGUAGE=en
```

When `META_WA_INVITE_TEMPLATE_NAME` is **unset** (dev), the invite still uses
the free-form interactive-button greeting (works inside an open window / on the
test number). When **set** (prod), the invite goes out as this template, so cold
candidates receive it. Everything after the candidate taps Yes (the questions,
scoring, closing) stays free-form — that's fine, the Yes tap opens the 24h
window.

> Note: today all three greeting contexts (fresh / reactivation / talent-pool)
> send this one template. Per-context templates are a later enhancement; the
> recorded transcript still shows the context-specific wording.

---

## Part B — go-live checklist (Meta dashboard; you do these)

In dependency order. A + B1/B2 run in parallel — start them the same day.

### B1. Business Verification — the long pole (hours to days)
- **business.facebook.com** → the Business that owns WABA `996720736447358`
- **Settings → Business settings → Security Center** → **Start Verification**
- Provide legal name + address + phone matching an official document
  (incorporation cert / business license / utility bill / bank statement), then
  complete the verification code step.
- This is what grants **Advanced Access** to `whatsapp_business_messaging`,
  which is what lets you message unlimited real numbers. Nothing ships without it.

### B2. Production phone number
- Needs a number **not currently active on the consumer WhatsApp or WhatsApp
  Business app** (fresh SIM, or a VoIP/landline that can receive an SMS/voice code).
- App Dashboard → **WhatsApp → API Setup** → **Add phone number** → set a
  **Display Name** (separate name review) → verify via the code.
- Record its new `phone_number_id` → set `META_WA_PHONE_NUMBER_ID` in prod.

### B3. Payment method
- App Dashboard → **WhatsApp → Configuration / Billing** → add a card to the
  WABA. Required before approved templates actually send beyond the free tier.

### B4. App → Live mode
- App Dashboard → top toggle **Development → Live**.

### B5. Webhook (prod URL + re-subscribe)
- Point the webhook callback to the **prod public URL** (Railway), not the
  ephemeral cloudflared tunnel. Set in prod env:
  `META_WA_VERIFY_TOKEN`, `META_WA_APP_SECRET`, `META_WA_ACCESS_TOKEN`.
- Subscribe the `messages` field at the app level.
- **Re-confirm the WABA→app subscription** (the bug that silently dropped real
  inbound last time): `GET /{WABA}/subscribed_apps` must list our app
  `1444079197766083` ("Qabil AI"). If not:
  `POST /{WABA}/subscribed_apps` with the system-user token. This can need
  redoing for the new production number.

### B6. Rotate the leaked secrets
- The App Secret + System User token were pasted in chat during dev. **Rotate
  both** before real traffic, and set the fresh values only in Railway env.

---

## Smoke test once live
1. From a phone that has **never** messaged the business number, have HR move an
   application to the `whatsapp` stage.
2. Confirm the candidate receives the **template** invite with Yes/No buttons.
3. Tap **Yes** → questions should flow (now inside the open window), each answer
   scored, conversation reaches `completed`.
4. Check the HR transcript (`GET /applications/{id}/whatsapp`) shows the invite
   row as `template_buttons` and the per-answer relevance/AI scores.
