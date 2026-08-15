# Known Issues

Issues found during the Azure migration + trial-account planning (Aug 2026).
Recorded here — rather than only in chat — so anyone working on this repo or
looking at the VM sees them without having to rediscover them.

---

## 1. No per-account data isolation — every HR login sees every job/candidate

**Status:** confirmed directly against the live code on `kabil-app`. Not a
guess.

**What it is:** the system has no organization/tenant concept — `CLAUDE.md`
says so explicitly and accurately:

> "The system is a single shared workspace (no org/tenancy), so all producers
> publish here and all HR streams subscribe here. **Per-user channels are
> the retrofit point if tenancy is ever added.**" — `CLAUDE.md`, Realtime
> events (SSE) section

That's a deliberate, documented design choice for the original use case (one
company's internal HR team, all meant to see the same jobs). It becomes a
real problem the moment more than one independent company is using the
system at once — which is exactly what handing out trial accounts to
outside companies means.

**Where it's confirmed, concretely:**

- `src/api/routes/jobs.py::list_jobs_endpoint` — takes `_current_user`
  (underscore-prefixed = intentionally unused) and never passes it into the
  query.
- `src/services/job_service.py::list_jobs` — builds `sa.select(Job)` filtered
  only by `status`/`search`. No `created_by` predicate anywhere.
- The exact same `_current_user` (auth-only, not used for scoping) pattern
  repeats across `applications.py`, `talent_pool.py`, and `dashboard.py` —
  every list/detail/summary endpoint in all four files.
- The realtime SSE channel (`events:hr`, `src/enums/events.py`) is one shared
  channel for every connected HR client, confirmed by `CLAUDE.md` itself
  (quoted above). Combined with the unscoped REST endpoints above, a
  connected client doesn't just see *that an event happened* on someone
  else's application — refetching it through the normal authed endpoint
  succeeds too, since that endpoint has no owner check either.

**Practical impact today:** any two logged-in HR accounts — including two
unrelated trial companies — see the same jobs, candidates, and applications,
and can act on each other's data (status changes, stage moves), not just
view it.

**Note on an open discrepancy:** while testing existing accounts
(`admin.1`, `admin.2`, and a third), two showed the same job list (consistent
with the above) but one showed empty. That's not fully explained by "zero
scoping everywhere" — if there were truly no filter at all, every account
should see the same non-empty list. Possible explanations not yet checked:
a UI-level default filter, that account being tested before jobs existed, or
a scoping path that exists somewhere this audit didn't cover. **Worth
re-testing directly before finalizing a fix**, in case it reveals a partial
mechanism already in place.

### The fix isn't "just add scoping everywhere"

The existing shared-workspace behavior is almost certainly *intentional and
relied upon* for your own internal team (multiple internal HR/admin logins
collaborating on the same jobs today). A blanket fix that scopes every
account to only its own `created_by` rows would fix trial isolation but
break that internal collaboration.

The fix needs to **distinguish internal accounts (keep today's shared
visibility) from trial/external accounts (isolated to their own data)**.
`users.role` already exists (`admin` / `hiring_manager`, currently used only
for auth-gating, per `CLAUDE.md` "API surface" section — not for scoping) —
the most natural existing hook, though it currently conflates "allowed to
log in as HR" with nothing about visibility, so it likely needs a third
value or a separate flag rather than overloading the current two.

**Scope of a real fix:**
- Add an owner/tenant filter to every list/detail endpoint in `jobs.py`,
  `applications.py`, `talent_pool.py`, `dashboard.py` — reads *and* writes
  (status/stage-change endpoints need an ownership check too, not just
  lists).
- Scope the SSE `events:hr` channel per-account (the exact retrofit
  `CLAUDE.md` already anticipated).
- Design and migrate the internal-vs-scoped account distinction.
- Test both directions: trial accounts are isolated from each other, *and*
  the internal team's existing shared-view workflow still works.

**Estimated effort:** materially bigger than a single-endpoint change —
multiple files, both read and write paths, a role/tenancy design decision,
and a migration. Closer in size to standing up the whole staging environment
than to a quick config change. Recommended as a **prerequisite** before
issuing trial accounts to real outside companies, not a parallel nice-to-have
— the exposure is real candidate PII crossing between unrelated companies,
not just a UX rough edge.

---

*Found during: Azure migration + CV-quota/trial-account planning, Aug 2026.
Add new issues above this line as they're found, most recent first isn't
required — just keep entries dated and sourced.*
