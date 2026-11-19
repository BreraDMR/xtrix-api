# Visitor iPhone app: what the API already gives and what's missing

State of `feature/duty-backend-20261118` on 18.11.2026. One backend, one database: the app is just
another client of the visitor endpoints, same as the web cabinet. Sizes: **S** = under a day,
**M** = 1-3 days, **L** = a week or more (including tests).

Request/response shapes and error tables are in
[web-handoff-2026-11.md, section 2](web-handoff-2026-11.md#2-visitor-cabinet). Not repeated here.

## 1. Usable from the app today, no backend work

| Area | Endpoints | Notes for a native client |
|---|---|---|
| Email + password account | `POST /visitor/auth/register/request`, `/register/complete`, `/login`, `/password/reset/*`, `/logout`, `/logout-all`, `GET /visitor/me` | The session is an opaque token in the `X-Visitor-Token` header, not a cookie. Works as is from React Native; keep it in the Keychain (`expo-secure-store`). |
| Email change | `POST /visitor/auth/email/change/request`, `/confirm` | Needs a sign-in within the last 5 minutes, same as on web. |
| Visit history | `GET /visitor/visits`, `GET /visitor/visits/{id}` | Cursor paging, categories, salon filter, prices with currency. |
| Visit actions | `GET /visitor/visits/{id}/actions`, `POST .../confirm`, `/cancel`, `/reschedule` | Same rules as web, the server re-checks every command. |
| Attaching visits | `POST /visitor/claims/manage`, `POST /visitor/claims/client-invite` | The app gets the token from a deep link (see 2.5) or from its own booking response. |
| Booking in a salon | `GET /public/booking/{slug}`, `/services`, `/staff`, `/slots`, `POST /public/booking/{slug}/bookings` | Response carries `manage_token`; the app posts it to `/visitor/claims/manage` right after, so the visit shows up in the list. In the app since 19.11.2026 with no API change; the book-then-claim path is covered by `tests/test_visitor_guest_claim_flow.py`. The two calls aren't one transaction: if the claim fails, the visit stays a guest booking and the app keeps the token for a later claim. |
| Locale | `Accept-Language` | Same `en` / `ru` / `cz` messages as web. |

CORS doesn't matter for a native app (no browser), so nothing to change there.

## 2. Missing

### 2.1 Account deletion inside the app - implemented (19.11.2026)

`DELETE /visitor/me` returns `204` and requires `X-Visitor-Token` plus a sign-in less than five
minutes ago (`401` for an invalid session, `403` when reauthentication is needed). It deletes the
visitor identity, Google links, sessions, grants and bound authentication proofs/mail jobs in one
transaction. Registration proofs for the current email are erased too. Spent claim receipts retain
only their replay guard; account/grant links are cleared and the consumed invitation email is scrubbed.

Salon client cards, contact details, visits and bookings stay unchanged. Deletion does not cancel
bookings. The same email can register a fresh account, without regaining old grants or reusing spent
links. Existing foreign keys provide the cleanup, so there is no new migration or tombstone.

The mobile profile includes confirmation, recent-auth guidance and session/cache cleanup. Local API
and mobile tests cover isolation, rollback, stale responses and repeat registration; native-device
acceptance is still separate from these checks.

### 2.2 Native Google sign-in - **M**, optional for v1

Today Google sign-in only works from the web:

- `/visitor/auth/google/nonce`, `/login`, `/link*` require an `Origin` header from `FRONTEND_URL` /
  `CORS_ORIGINS`. A native app sends no `Origin`, so it gets `403`.
- The ID token's `aud` must equal `GOOGLE_CLIENT_ID` (the web client). A native Google SDK issues tokens
  for the iOS client ID, so they'd be rejected with `401`.

Fix: a separate list of accepted audiences (`GOOGLE_IOS_CLIENT_ID`), and a native variant of the nonce
challenge bound to a client kind instead of an origin. Plus a new OAuth client in Google Cloud (Damir's
account).

### 2.3 Sign in with Apple - **L**, only if 2.2 ships

If the app offers Google sign-in, Apple requires an equivalent privacy-friendly option, in practice
Sign in with Apple (guideline 4.8). That means: verifying Apple ID tokens (JWKS, `aud` = bundle id),
a new identity link table like the Google one, "hide my email" relay addresses, and revocation on account
deletion (Apple's `/auth/revoke`). Needs keys from the Apple Developer account.

**Cheapest path for v1:** email + password only in the app, no Google, so no Sign in with Apple either.
Add both later together.

### 2.4 Push notifications - **done for reminders** (19.11.2026)

Built: table `visitor_push_tokens` (one row per phone, tied to the visitor session),
`PUT /visitor/push-tokens` and `DELETE /visitor/push-tokens/{token}`; logout, logout-all, password reset
and account deletion drop the phone and erase its token from the outbox. The day-before reminder goes
through the Expo Push API as an outbox row with `channel=push`, behind `PUSH_NOTIFICATIONS_ENABLED`
(off by default); capture mode catches it like mail. See the API README, "Visit reminders on the
visitor's phone".

Not built yet: pushes for booking created / moved / cancelled (only the reminder), Expo receipts
(we only read the send ticket).

### 2.5 Deep links from emails - **S** (mostly web side)

Emails link to the web: `FRONTEND_URL/visitor/register#token=…`, `/visitor/reset-password#token=…`,
`/visitor/email-change#token=…`, plus the guest manage link and the salon invite link. With iOS Universal
Links the same URLs can open the app, no API change needed:

- the web host serves `/.well-known/apple-app-site-association` listing those `/visitor/*` paths
  (web repo);
- the app reads the token from the fragment and posts it to the same endpoint the web page uses.

If the app isn't installed the link keeps opening the web cabinet, so nothing breaks.

### 2.6 API version / forced update - **S**

There's no version in the paths (`/visitor/...`, not `/v1/...`), and no way to tell an old app build to
update. Web can redeploy any time, an installed app can't. Proposal: `GET /visitor/app-config` returning
`{"min_app_version": "1.0.0", "latest_app_version": "1.0.0"}` and the app shows "please update" below
the minimum. Breaking changes to `/visitor/*` after release then go behind a new field or a new path,
never an in-place change.

### 2.7 Session lifetime - **S**

Sessions live exactly 30 days and reads never extend them, so an app user gets logged out every month
for no visible reason. Options: slide the expiry on use (at most once a day, so we don't write on every
read), or a longer TTL only for sessions created by the app. Recommended: sliding renewal for all
visitor sessions.

### 2.8 Error codes - **S**

Many visitor-account errors have only a localized `message`. The app can branch on HTTP status like the
web does, but stable `detail.code` values (as visit actions already have) would make the UI less
fragile. Can be done endpoint by endpoint, non-breaking.

### 2.9 Not planned, just noting

- No salon search/discovery: booking needs the salon `slug`. The app gets it from a salon's link or
  from salons of the visitor's past visits. A public catalogue is a product decision, not an API gap.
- Login throttling is by email + IP. Mobile carriers share IPs (CGNAT); watch for false `429`s after
  launch before changing anything.

## 3. Order

For a first App Store build: 2.1 (required), 2.6, 2.7, 2.5. Then 2.4 push. 2.2 + 2.3 together only when
we want social login in the app.
