# Web handoff, November 2026

What changed in the API since 25.10.2026 (`7c58543`) and what the web app needs to do about it.
Source of truth is still `openapi.json` in the repo root; this file explains the parts the schema
can't: who may call what, which error codes to expect and what to show the user.

Grouped by screen:

1. [General rules](#1-general-rules)
2. [Visitor cabinet](#2-visitor-cabinet)
3. [Price correction](#3-price-correction)
4. [Balance due on a visit](#4-balance-due-on-a-visit)
5. [Finances by currency](#5-finances-by-currency)
6. [Currency of old visits](#6-currency-of-old-visits)
7. [Cashback wallets](#7-cashback-wallets)
8. [Payroll by currency](#8-payroll-by-currency)
9. [Smaller changes in existing endpoints](#9-smaller-changes-in-existing-endpoints)
10. [Stop sending](#10-stop-sending)

## 1. General rules

**Error body.** Every error looks like this:

```json
{"error": {"message": "Localized text for the user", "status_code": 409,
           "detail": {"code": "visit_price_quote_changed", "hint": "..."}, "type": "HTTPException"}}
```

- New money endpoints put a stable machine code into `error.detail.code`. Branch on that, show
  `error.message` as is (it's already translated by `Accept-Language`: `en`, `ru`, `cz`).
- Payroll endpoints use `error.detail.reason` instead of `code` (older convention, kept as is), plus
  extra fields, see [section 8](#8-payroll-by-currency).
- Many visitor-account errors only have a localized `message` and no code. Branch on the HTTP status
  there; the tables below say which status means what.
- `422` from body validation keeps the existing format (`error.detail` is a list of
  `{field, code, params, message}`).

**Money.** No currency conversion anywhere. Every amount belongs to one currency.

- New money fields are **strings with exact cents** (`"1250.00"`). Don't do float math on them;
  show them as is or parse with a decimal library.
- `currency` is an ISO 4217 code (`"CZK"`, `"EUR"`). `currency: null` next to an amount means "visits
  saved without a currency" - show the amount with a "currency unknown" label, never with the
  salon's current currency.
- `requires_review: true` on a row = the backend can't vouch for the number. Show it greyed out or
  with a warning icon, not as a plain figure.
- Old scalar fields (`total_revenue`, `cashback`, `revenue`, ...) are still there for compatibility,
  but are now `null` when the data has more than one currency or an unknown one. When the old field
  is `null`, render `by_currency` instead. Best: always render `by_currency` and drop the scalar.

**Private responses.** All new endpoints and the analytics/loyalty/expense ones send
`Cache-Control: no-store`. Don't put them in a shared/persistent cache on the client either.

## 2. Visitor cabinet

A visitor is the salon's customer with their own login, completely separate from CRM users. All
endpoints live under `/visitor`. The session token goes into the **`X-Visitor-Token`** header
(never `Authorization`). Sessions live 30 days. A CRM token doesn't work here and vice versa.

### 2.1 Sign up, sign in, sign out

| Endpoint | Auth | Request | Response |
|---|---|---|---|
| `POST /visitor/auth/register/request` | none | `{"email": "jana@example.com"}` | `202 {"message": "..."}` |
| `POST /visitor/auth/register/complete` | none | `{"token": "<from email link>", "new_password": "..."}` | `201 {"session_token": "...", "expires_at": "..."}` |
| `POST /visitor/auth/login` | none | `{"email": "...", "password": "..."}` | `200 {"session_token": "...", "expires_at": "..."}` |
| `POST /visitor/auth/password/reset/request` | none | `{"email": "..."}` | `202 {"message": "..."}` |
| `POST /visitor/auth/password/reset/confirm` | none | `{"token": "<from email link>", "new_password": "..."}` | `204` |
| `POST /visitor/auth/logout` | token | - | `204` (also for an already dead token) |
| `POST /visitor/auth/logout-all` | token | - | `204`, kills every session of the account |
| `GET /visitor/me` | token | - | `{"id": 7, "email": "jana@example.com", "email_verified_at": "...", "created_at": "...", "has_password": true, "google_connected": false}` |

UI notes:

- `register/request` and `password/reset/request` answer `202` with the same text whether the email
  exists or not. Show "check your inbox" and nothing else - don't hint whether the account exists.
- The email link carries the token; the page reads it from the URL and posts it with the new password.

Errors:

| Status | When | Show |
|---|---|---|
| 400 | link token invalid/expired/used (`register/complete`, `password/reset/confirm`) | "The link is no longer valid", offer to request a new one |
| 401 | wrong email/password; missing/expired `X-Visitor-Token` on any protected endpoint | login form error / drop the stored token and go to login |
| 409 | `register/complete`: account can't be created from this link | the message, link to sign in |
| 422 | new password too weak | the message under the password field |
| 429 | too many login attempts; `Retry-After` header in seconds | "try again in N minutes" |
| 503 | email sending is not configured on this server | the message; nothing to retry right now |

### 2.2 Google sign-in and linking

1. `GET /visitor/auth/google/config` -> `{"enabled": true, "client_id": "..."}`. `enabled: false` = hide the Google button.
2. `POST /visitor/auth/google/nonce` with body `{}` -> `{"nonce": "...", "expires_in": 600}`. The request
   must come from an allowed `Origin`.
3. Give the nonce to Google Identity Services, get the ID token (`credential`).
4. `POST /visitor/auth/google/login` `{"credential": "...", "nonce": "..."}` -> `{"session_token", "expires_at"}`.

Linking Google to an existing account (logged-in visitor, signed in within the **last 5 minutes**):
`POST /visitor/auth/google/link/nonce` `{}` then `POST /visitor/auth/google/link`
`{"credential", "nonce"}` -> `204`.

| Status | Meaning | Show |
|---|---|---|
| 401 | Google token or nonce invalid | "Google sign-in failed, try again" |
| 403 | wrong `Origin`, or (link only) the login is older than 5 minutes | for the 5-minute case: ask to sign in again, then retry |
| 409 on login | an account with this email already exists without Google, **or** a race | the message; for "link required" tell them to sign in with password and link Google in the profile |
| 409 on link | this Google account is already used by another visitor | the message |
| 503 | Google is not configured or not reachable | hide/disable the button |

### 2.3 Email change

`POST /visitor/auth/email/change/request` `{"email": "new@example.com"}` -> `202 {"message"}`, then the
link from the new mailbox -> `POST /visitor/auth/email/change/confirm` `{"token": "..."}` -> `204`.
Both need `X-Visitor-Token` and a sign-in within the last 5 minutes.

| Status | Meaning |
|---|---|
| 400 | confirm link invalid/expired |
| 403 | sign in again first (5-minute rule) |
| 409 | the new email got taken meanwhile |
| 422 | the new email can't be used (show the message) |
| 503 | email sending not configured |

### 2.4 Attaching visits to the account

Two ways a visitor gets access to visits:

- **Guest booking link.** The public booking confirmation already gives a manage token. A logged-in
  visitor posts it: `POST /visitor/claims/manage` `{"token": "<manage token>"}` -> `204`.
  Errors: `400` link invalid, `401` session invalid, `409` conflict (already claimed, or a race - show the message).
- **Invitation from the salon.** CRM side: `POST /clients/{client_id}/visitor-invitations`
  `{"email": "jana@example.com"}` -> `201 {"token": "...", "expires_at": "..."}`. Callers: owner,
  admin, and staff who can edit clients and see this client (same business). Errors: `403` no right
  to this client, `409` busy/retry, `422` bad recipient. The CRM shows or sends the link with the token;
  the visitor opens it while logged in and the web app posts
  `POST /visitor/claims/client-invite` `{"token": "..."}` -> `204`.
  Visitor errors: `400` invite invalid/expired, `401` session invalid, `403` the salon no longer allows it,
  `409` already used / race (retry once).

### 2.5 Visit history

`GET /visitor/visits?category=all|upcoming|past|cancelled&business_id=&limit=25` (limit 1..100):

```json
{"entries": [{
   "id": 812, "business_id": 3, "start": "2026-11-20T09:00:00Z", "end": "2026-11-20T10:00:00Z",
   "status": "scheduled", "confirmed_at": null, "service_name": "Haircut", "staff_name": "Eva",
   "price": {"amount": "650.00", "currency": "CZK"},
   "items": [{"id": 1, "position": 0, "service_name": "Haircut", "staff_name": "Eva",
              "start": "2026-11-20T09:00:00Z", "end": "2026-11-20T10:00:00Z",
              "duration_minutes": 60, "price": {"amount": "650.00", "currency": "CZK"}}],
   "business": {"name": "Salon Anna", "address": "...", "phone": "...", "email": "...", "timezone": "Europe/Prague"}}],
 "next_cursor": {"date_start": "2026-11-20T09:00:00Z", "id": 812, "as_of": "2026-11-13T08:00:00Z"}}
```

- Next page: pass `after_start=next_cursor.date_start&after_id=next_cursor.id`, and for any category
  other than `all` also `as_of=next_cursor.as_of`. Missing pair -> `422`.
- `price: null` = the salon hasn't got a price with a known currency for it. Show "price on request",
  not `0`.
- `GET /visitor/visits/{id}` - one visit, same shape. `404` = not yours or gone.

### 2.6 Confirm, cancel, reschedule

First ask what's allowed: `GET /visitor/visits/{id}/actions`

```json
{"confirm": {"allowed": true, "reason": null},
 "cancel": {"allowed": false, "reason": "booking_cancel_too_late"},
 "reschedule": {"allowed": false, "reason": "booking_reschedule_too_late"}}
```

Show only allowed buttons; for disallowed ones you may show a hint by `reason` (enum in the schema:
`booking_*_completed`, `*_in_progress`, `*_cancelled`, `*_unavailable`, `*_too_late`, `*_already`,
`*_started`, `visitor_actions_unavailable`). The hints are not a reservation - the command checks again.

Commands, all `204` on success:

- `POST /visitor/visits/{id}/confirm`
- `POST /visitor/visits/{id}/cancel`
- `POST /visitor/visits/{id}/reschedule` `{"start": "2026-11-21T10:00:00Z"}` - price stays as stored.

| Status | `detail.code` | Show |
|---|---|---|
| 401 | - | session expired, go to login |
| 404 | - | visit not available any more, reload the list |
| 409 | `visitor_action_retry` | "try again" (one silent retry is fine) |
| 409 | `booking_reschedule_unavailable` | the new time is taken, pick another |
| 409 | `booking_cancel_unavailable` | a payment is in progress, can't cancel online, contact the salon |
| 409 | any `booking_*` reason above | the message, then reload `/actions` |

## 3. Price correction

Staff screen on one visit. All endpoints below use the same access rule: the caller must see the
visit **and** have "view financials" (owner, admin, staff with that permission). Otherwise `403`;
foreign or missing visit `404`.

### 3.1 Read the stored price

`GET /visits/{id}/price`

```json
{"visit_id": 812, "currency": "CZK", "total": "650.00",
 "expected_quote": "vprice1_3f5c...64 hex",
 "items": [{"item_id": 1, "position": 0, "service_id": 4, "specialist_id": 9, "price": "650.00"}]}
```

`expected_quote` is a fingerprint of what you just read. Send it back with any change so the server
can refuse if somebody changed the price in between.

### 3.2 Change a price nothing has been paid on

`PUT /visits/{id}/price`

```json
{"expected_quote": "vprice1_3f5c...", "currency": "CZK",
 "items": [{"item_id": 1, "price": "700.00"}]}
```

- Price **every** stored item exactly once. A visit without items (old data) sends
  `"legacy_price": "700.00"` instead of `items` - never both.
- Response: the new quote, same shape as 3.1.

| Status | `detail.code` | Show |
|---|---|---|
| 409 | `visit_price_quote_changed` | "the price changed meanwhile", reload 3.1 and let them redo it |
| 409 | `visit_price_settlement_required` | something is already paid/redeemed: switch the form to **correction** (3.3) |
| 409 | `visit_price_payment_pending` | an online payment is open; finish or cancel it first |
| 409 | `payment_method_required_for_completed_visits` | completed visit needs a payment method first |
| 409 | `visit_price_retry` | busy, retry |
| 422 | `visit_price_items_invalid` | items don't match the visit (reload) |

### 3.3 Correct a price that already has money on it

`POST /visits/{id}/price-corrections`

```json
{"expected_quote": "vprice1_3f5c...", "currency": "EUR",
 "items": [{"item_id": 1, "price": "28.00"}],
 "prior_payment_action": "refund_due",
 "reason": "Client was charged in the wrong currency",
 "idempotency_key": "corr-812-20261113-01"}
```

- `prior_payment_action`: `retained` (the old money stays, counts for nothing new) or `refund_due`
  (salon owes it back). Ask the user explicitly; there's no default.
- `reason` is required, show it as a textarea.
- `idempotency_key`: 8-80 chars `[A-Za-z0-9_-]`. Generate once per form submit and reuse it on
  retries. Same key + same body returns the saved correction with `"replayed": true`.
- The old payments **stay in their old currency**, no conversion.

Response:

```json
{"correction": {"id": 5, "visit_id": 812, "old_currency": "CZK", "old_total": "650.00",
                "new_currency": "EUR", "new_total": "28.00", "prior_payment_action": "refund_due",
                "reason": "...", "created_at": "..."},
 "quote": {"visit_id": 812, "currency": "EUR", "total": "28.00", "expected_quote": "vprice1_...", "items": [...]},
 "replayed": false}
```

| Status | `detail.code` | Show |
|---|---|---|
| 409 | `visit_price_correction_not_needed` | nothing is paid yet - use the plain editor (3.2) |
| 409 | `visit_price_correction_unchanged` | the new price equals the stored one |
| 409 | `visit_price_correction_key_conflict` | same key with a different body - generate a new key |
| 409 | `visit_price_correction_unavailable` | correction not possible for this visit, show the message |
| 409 | `visit_price_quote_changed` / `visit_price_payment_pending` / `visit_price_retry` | as in 3.2 |
| 422 | `visit_price_items_invalid` | as in 3.2 |

History: `GET /visits/{id}/price-corrections` - list of the `correction` objects above, oldest first.
Show it as a timeline under the price.

### 3.4 Editing a visit (`PUT /visits/{id}`)

Changing services, staff or times on a visit with recorded money now fails with `409`:

| `detail.code` | Show |
|---|---|
| `visit_price_settlement_required` | "This visit has payments; correct the price separately" + link to 3.3 |
| `visit_price_payment_pending` | finish or cancel the pending payment first |
| `visit_price_currency_explicit_required` | the visit has a different saved currency; ask for an explicit new price and currency (3.2/3.3) |
| `visit_price_retry` | retry |

`VisitOut` has a new optional `loyalty_warning` (localized text). It's set when the visit was saved
but awarding bonuses on completion failed. Show it as a non-blocking warning toast.

### 3.5 Price after bonuses

`GET /visits/{id}/loyalty-price`

```json
{"visit_id": 812, "currency": "CZK", "quoted_total": "650.00", "loyalty_discount": "50.00",
 "price_after_loyalty": "600.00", "requires_review": false, "message": null}
```

`requires_review: true` -> amounts may be `null`; show `message` instead of numbers.

## 4. Balance due on a visit

### 4.1 What's paid and what's left

`GET /visits/{id}/settlement` (same access as section 3)

```json
{"visit_id": 812, "currency": "CZK",
 "gross_quote": "650.00", "loyalty_discount": "50.00", "price_after_loyalty": "600.00",
 "gift_money_coverage": "0.00", "gift_units_coverage": null, "gift_package_coverage": null,
 "provider_received": "300.00", "provider_refunded": "0.00", "provider_net": "300.00",
 "charges": "0.00", "manual_received": "100.00", "manual_refunded": "0.00",
 "outstanding": "200.00", "overpayment": "0.00", "outstanding_reason": null,
 "has_pending_checkout": false, "manual_payment_recorded": false,
 "requires_review": false, "reasons": [], "prior_currency_settlements": [], "message": null}
```

- Show `outstanding` as "to pay" and `overpayment` as "paid too much" (only if > 0).
- When anything is unknown, `outstanding` and `overpayment` are **`null`** and `outstanding_reason`
  says why; `message` carries a localized explanation. Show the message, not `0`.
- A cancelled visit doesn't create a debt by itself.
- `prior_currency_settlements` lists money left in a currency the price was corrected away from
  (`{"currency": "CZK", "provider_received": "650.00", "provider_refunded": "0.00", "provider_net": "650.00", "entry_rows": 0, "benefit_rows": 0}`).
  Show it as a separate line "in CZK: 650.00 (old currency)", never added to the main total.
- `manual_payment_recorded: true` = the old "paid manually" checkbox without an amount. The amount
  is unknown on purpose; we don't guess it.
- An online payment counts with the amount it was **sent** with. While it's open
  (`has_pending_checkout: true`, reason `payment_pending`) the price can't be corrected (`409`
  `visit_price_payment_pending`); when it arrives later, that sent amount is what's paid, and a
  correction after it only changes `outstanding`/`overpayment`.
- Two money writes on one visit at the same moment: the second gets `409` with the "try again"
  message (`visit_price_retry`) and nothing of it is saved - just repeat it with the same
  `idempotency_key`. A receipt in the old currency after a currency correction gets `409`
  `visit_settlement_currency_mismatch`.

`outstanding_reason` codes (stable, safe to map to your own texts):

| Code | Meaning |
|---|---|
| `quote_currency_unknown`, `quote_amount_invalid` | the price itself isn't known |
| `manual_amount_unknown` | old "paid manually" marker without an amount |
| `payment_pending` | an online checkout is open |
| `payment_reconciliation` | provider data is being reconciled |
| `provider_history_review`, `gift_history_review`, `loyalty_history_review`, `settlement_entry_review` | some record needs checking |
| `gift_package_value_unknown` | a package/units certificate was used and its money value is unknown |
| `mixed_settlement_history` | several old payment kinds mixed together |
| `prior_currency_settlement`, `prior_currency_benefits`, `prior_currency_entries` | money left in an old currency after a correction |
| `cancelled_coverage_review` | cancelled visit with coverage on it |
| `coverage_exceeds_price` | bonuses and certificates together exceed the price |
| `refund_balance_review` | refunds don't add up |
| `outstanding_review`, `outstanding_not_projected` | generic fallback |

### 4.2 Record a charge or a manual receipt/refund

`POST /visits/{id}/settlement-entries` (same access)

```json
{"kind": "manual_receipt", "amount": "100.00", "currency": "CZK",
 "reason": "Paid cash at the desk", "reference": "R-2026-0153",
 "idempotency_key": "entry-812-20261113-01"}
```

- `kind`: `charge` (extra amount owed), `manual_receipt` (money received outside the online
  payment), `manual_refund` (money given back).
- `currency` must be the currency of the **current** visit price. Default the field to it and keep it
  read-only.
- `reference` optional (receipt number). `reason` required.
- `idempotency_key` - same rules as in 3.3. Response `{"entry": {...}, "replayed": false}`.
- Entries can't be edited or deleted. A mistake is fixed by an opposite entry.

| Status | `detail.code` | Show |
|---|---|---|
| 409 | `visit_settlement_currency_mismatch` | currency differs from the visit price - fix the price first (3.3) |
| 409 | `visit_settlement_currency_unknown` | the visit price has no currency yet - set it first |
| 409 | `visit_settlement_entry_key_conflict` | key reused with another body - new key |
| 409 | `visit_settlement_unavailable` | entries not possible for this visit |
| 409 | `visit_price_retry` | retry |

List: `GET /visits/{id}/settlement-entries` - entries oldest first
(`{"id", "visit_id", "kind", "amount", "currency", "reason", "reference", "actor_user_id", "created_at"}`).

## 5. Finances by currency

All analytics endpoints that show money now require "view financials" (`403` otherwise):
`/analytics/financial/report`, `/analytics/services/popular`, `/analytics/clients/statistics`,
`/analytics/payments/statistics`, `/analytics/services-specialists`, `/analytics/insights`,
`/analytics/benchmarks`, `/analytics/trends` (metrics `revenue`, `average_check`),
`/analytics/forecast` (type `revenue`), `/analytics/reports/export` (types `financial`, `services`,
`clients`). Hide those screens for staff without the permission.

Common pattern in every report: the old number plus a new `currency` and `by_currency` list.

```json
{"total_revenue": null, "currency": null, "value_basis": "quoted_service_price",
 "by_currency": [
   {"currency": "CZK", "amount": "18450.00", "average": "615.00", "count": 30, "requires_review": false},
   {"currency": "EUR", "amount": "240.00", "average": "60.00", "count": 4, "requires_review": false},
   {"currency": null, "amount": "1200.00", "average": "600.00", "count": 2, "requires_review": true}]}
```

- One currency known -> `total_revenue` is filled and `currency` = that code; same number as in
  `by_currency[0]`.
- Several currencies or any unknown -> scalar `null`, `currency` `null`. Show one card/column per
  `by_currency` row. Never sum them.
- `value_basis: "quoted_service_price"` = revenue is the stored visit price, not money received.
  Label it "Service value" rather than "Cash received".

Per endpoint:

| Endpoint | Old scalar fields now nullable | New fields |
|---|---|---|
| `GET /analytics/financial/report` | `total_revenue`; `revenue_by_period[].revenue` | `currency`, `by_currency`, `top_clients_by_currency`, `value_basis`; `revenue_by_period[]` items now have `by_currency`, `currency`, `count` |
| `GET /analytics/services/popular` | `services[].total_revenue`, `average_price` | `services[].currency`, `services[].by_currency` |
| `GET /analytics/clients/statistics` | - | `currency`, `average_spending_by_currency`, `top_spenders_by_currency` |
| `GET /analytics/payments/statistics` | `total_payments`; `payments_by_method.*.total`, `percentage` | `currency`, `by_currency` (with its own `payments_by_method`) |
| `GET /analytics/trends` | `current_value`, `previous_value`, `change`, `change_percent`, `trend_direction` | `currency`, `by_currency` |
| `GET /analytics/forecast` | `historical_average`, `last_value`, `forecast[].forecasted_value` | `currency`, `by_currency[].forecast` |
| `GET /analytics/services-specialists` | services: `total_revenue`, `average_price`, `ltv_per_client`; specialists: `total_revenue`, `average_check`, `revenue_per_client` | `currency`, `by_currency` per row |
| `GET /analytics/benchmarks` | - | `currency`, `by_currency` (`{currency, average_check, business_count, visit_count}`) |
| `GET /expenses/reports/profit` | `revenue`, `expenses`, `profit`, `profit_margin` (also per day) | `currency`, `by_currency`, `requires_review`, `value_basis` |
| `GET /expenses/reports/expenses-by-category` | `total`, `category_id`, `category_name` | `currency`, `by_currency`, `requires_review` |

Benchmarks compare salons only within the same currency. A currency with too few salons is simply
missing from `by_currency`; don't show an empty comparison.

Exports (`POST /analytics/reports/export`, types `financial`, `services`, `clients`): one row per
currency, new `Currency` column (`UNKNOWN` for visits without currency) and `Requires Review` column;
amounts are text with exact cents. Financial export starts with `TOTAL` rows, one per currency.

Gift certificates: `GiftRedemptionOut`, `GiftReversalOut`, `GiftLedgerEntryOut` gained
`coverage_amount_exact` and `coverage_currency` - the money value a redemption covered on the visit,
in the visit currency. Show them on the certificate history next to the redeemed amount.

## 6. Currency of old visits

Owner-only screen (anyone else gets `403`). Old visits saved before currencies existed have no
currency and show up as `currency: null` everywhere. The owner states once what currency they were.

`GET /businesses/me/legacy-visit-currency`

```json
{"unknown_visits": 871, "business_currency": "CZK",
 "first_visit_at": "2024-03-02T09:00:00Z", "last_visit_at": "2026-10-20T15:00:00Z"}
```

`POST /businesses/me/legacy-visit-currency/confirm`

```json
{"currency": "CZK", "expected_count": 871}
```

-> `{"currency": "CZK", "updated_visits": 871}`

- Show it as a banner in finances when `unknown_visits > 0`: "871 visits from 02.03.2024-20.10.2026
  have no currency. They were in: [CZK v]" with `business_currency` preselected, and a confirm button.
- `expected_count` is the number from the preview; the server refuses if it changed.

| Status | `detail.code` | Show |
|---|---|---|
| 409 | `legacy_currency_count_changed` | reload the preview and ask again |
| 409 | `legacy_currency_nothing_to_confirm` | hide the banner |
| 409 | `visit_price_retry` | retry |

This is one-way; there's no undo endpoint. Ask "Are you sure?" before sending.

## 7. Cashback wallets

Cashback is now kept per currency. Affected responses:

| Endpoint | Change |
|---|---|
| `GET /loyalty/balance/{client_id}` | `cashback` nullable; new `cashback_currency`, `cashback_balances` |
| `GET /public/client-portal/loyalty` | same |
| `GET /loyalty/analytics/top-clients` | `cashback` is now a string or `null`; new `cashback_currency`, `cashback_balances` |
| `GET /loyalty/analytics/statistics` | `active_cashback_balance`, `total_cashback_earned`, `total_cashback_spent` are strings or `null`, `cashback_redemption_rate` nullable; new `cashback_currency`, `cashback_by_currency` |
| `GET /loyalty/analytics/trends` | items: `cashback_earned`, `cashback_spent` strings or `null`; new `cashback_currency`, `cashback_by_currency` |
| `GET /loyalty/analytics/promotion-effectiveness` | `total_cashback_awarded` string or `null`; new `cashback_currency`, `cashback_by_currency` |
| `GET /loyalty/transactions/{client_id}` | new `cashback_amount_exact`, `cashback_balance_after_exact`, `cashback_currency`, `cashback_requires_review`, `discount_amount_exact`, `discount_currency` |

Balance example:

```json
{"points": 120.0, "cashback": null, "cashback_currency": null,
 "cashback_balances": [{"currency": "CZK", "amount": "85.00", "requires_review": false},
                       {"currency": "EUR", "amount": "3.50", "requires_review": false}]}
```

- One wallet with a known currency -> `cashback` + `cashback_currency` are filled. Otherwise `null`;
  render `cashback_balances` as separate lines.
- `POST /loyalty/use-points`: `cashback_to_use` is now in the **visit currency** and may be sent as a
  string (`"20.00"`). Only the wallet of the visit currency can be used; pick the visit first.
  Errors are plain `400` with a localized message (e.g. "Choose a visit before using points or
  cashback", "This cashback has no confirmed currency for this visit").
- The whole `/loyalty/*` group now requires "view financials" (`403` otherwise).

## 8. Payroll by currency

Base salary, salary calculations, payments and adjustments carry a currency now.

Employee forms (`POST /employees/`, `POST /employees/create-with-user`, `PUT /employees/{id}`):

- new `base_salary_currency` (ISO code). Responses add `base_salary_currency` and
  `base_salary_exact` (string).
- On update, sending `base_salary_currency` without `base_salary` is refused
  (`payroll_invalid_amount`). Send both together.
- `commission_percentage` accepts a number or a string, 0..100.

Calculations:

- `POST /employees/{id}/salary/calculate` takes optional `currency`. Without it the backend takes the
  one currency found in the period (visits + base salary); if there are several you get `400`
  `payroll_currency_required` with `currencies`, same as below.
- `GET /employees/me/salary/current?currency=CZK` and `GET /employees/{id}/salary/current?currency=CZK`:
  if the employee has unpaid calculations in more than one currency and no `currency` is passed,
  you get `400` with `reason: "payroll_currency_required"` and `currencies: ["CZK", "EUR"]`.
  Show a currency switcher from that list and call again with `?currency=`.
- `SalaryCalculationOut` / `SalaryCalculationDetail` add `currency`, exact string twins of every
  amount (`base_salary_exact`, `commission_amount_exact`, `total_revenue_exact`, `total_salary_exact`,
  `adjustments_total_exact`, `total_with_adjustments_exact`, `paid_amount_exact`),
  `business_id_snapshot` and `salary_period` (`SalaryPeriodOut`: the terms used for that period).
  Prefer the `_exact` fields for display.
- Payments and adjustments (`.../payments`, `.../adjustments`) accept `currency` and `amount` as a
  string; must equal the calculation currency. Responses add `amount_exact`, `currency`.
- `ExpenseOut.salary_payment_id` links an expense created by a salary payment. Editing or deleting
  such an expense is refused with `400 reason: "payroll_correction_required"`; disable edit/delete
  for expenses with `salary_payment_id`.

Payroll errors are `400` with `error.detail.reason`:

| `reason` | Show |
|---|---|
| `payroll_currency_required` | currency switcher from `currencies` |
| `payroll_currency_mismatch` | the currency must match the calculation |
| `payroll_currency_review` | pick a supported ISO currency |
| `payroll_invalid_amount`, `payroll_invalid_rate`, `payroll_invalid_period` | field error |
| `payroll_payment_exceeds_remaining`, `payroll_total_below_paid` | amount too big / below what's paid |
| `payroll_period_overlap` | the period overlaps an existing calculation |
| `payroll_visit_review`, `payroll_legacy_review`, `payroll_snapshot_review` | some visits/old data need review first (e.g. currency of old visits, section 6) |
| `payroll_employee_inactive`, `payroll_employee_missing`, `payroll_business_missing`, `payroll_business_mismatch`, `payroll_calculation_missing`, `payroll_payment_required` | the message |
| `payroll_correction_required` | expense tied to a salary payment, see above |
| `payroll_transaction_failed` | generic, retry later |

## 9. Smaller changes in existing endpoints

- `POST /visits/{id}/restore` may now answer `409` `visit_restore_slot_offered`: the freed slot was
  already offered to a waitlist guest. Show the message; restore works again once the offer is
  answered or expires.
- `GET /public/booking-management` and its `cancel/confirm/reschedule`: `currency` is now the visit's
  saved currency and may be `null`. Don't fall back to the salon currency.
- `GET /services/export/csv` and `/services/export/excel` need the "manage services" permission now,
  like the service list.
- `UsePointsRequest.cashback_to_use` - see section 7.
- Client and visit exports now return **exactly the rows the list shows** for the same filters, every
  page at once. Send the list's current filters with the export link:
  - `GET /clients/export/{csv,excel}` takes `q`, `gender`, `sort_by`, `sort_order` (same values as
    `GET /clients/`), plus the old `phone` and `name`. `owner_id` is gone (it was admin-only and the
    list ignores it too).
  - `GET /visits/export/{csv,excel}` takes `q`, `status`, `sort_by`, `sort_order` next to the old
    `client_id`, `specialist_id`, `date_from`, `date_to` (same values as `GET /visits/`).
  - A salon owner's export now covers the whole salon, including cards and visits made by staff
    (before it was only rows tied to the owner personally). A `specialist_id` or `client_id` from
    another salon gets `403`, same as the list; bad filter values get `422`.

- Assistant (`POST /assistant/plan`): more answers now come back as **questions with no actions**
  instead of a plan that only failed after "confirm" - the master is already booked at that time
  (the question lists the next free times), the client isn't found or several match ("which one: A,
  B?"), or a new card would duplicate a client the salon already has ("you already have X - is it
  her?"). Nothing new for the UI: show `questions` and let the person answer in the same chat.
  Record numbers are no longer shown in `summary`/`questions`.

## 10. Stop sending

- `null` in `commission_percentage` / `base_salary` on employee forms is accepted (create = 0,
  update = "leave as is"), but better just omit the field when the input is empty.
- Don't send a currency you computed from the salon's current currency for old visits; use
  section 6 instead.
- Don't sum amounts from different `by_currency` rows or wallets on the client, not even "for a rough
  total".
- Don't retry money writes (`price-corrections`, `settlement-entries`) with a new
  `idempotency_key`; reuse the one from the first attempt.
