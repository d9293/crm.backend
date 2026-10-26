# API contract

Versioned OpenAPI contract for the ACE / CSKA CRM. `development/README.md` §3 places it in this repository; the frontend generates or validates its types against it.

## Status

`openapi.yaml` — **draft for contract review.** Covers S1 and S3:

| Sprint | Page | Endpoints |
|---|---|---|
| S1 | P01 Access | `POST/GET/DELETE /session`, `GET /me` |
| S1 | P02 Employees | `/employees`, `/{id}`, `/archive`, `/sessions`, `/meta/capabilities` |
| S1 | P03 Locations & resources | `/locations`, `/{id}/resources`, `/resources/{id}`, `/closures` |
| S3 | P07 Availability | `GET /availability` |
| S3 | P07 Customer request | `POST /reservations/requests` |
| S3 | P08/P09 Reads | `GET /reservations`, `GET /reservations/{id}` |
| S3 | P10 Approval queue | `POST /reservations/{id}/confirm`, `/reject` |
| S4 | P08 Customer cancellation | `POST /reservations/{id}/cancellation-requests` |
| S4 | P11 Administrator queue | `GET /cancellation-requests`, `.../approve`, `.../reject` |

Written before the server exists, deliberately: `development/README.md` §3 requires each sprint to begin with contract examples agreed by both teams, and the contract is language-neutral, so it is not blocked by D-01.

This is a proposal for agreement, not approved policy. Fields and operations that depend on an unresolved decision carry a `GATE` note naming it — `D-01`, `D-02`, `D-04`, `D-06`, `D-12`. Those parts are not implementation-ready.

## Validate

```sh
npx @redocly/cli lint          # uses ../redocly.yaml
```

Passes with zero errors and zero warnings. The INF-01 CI job runs this, plus a check that the committed document matches the one the server generates, so a drifted contract breaks the build.

## Generate frontend types

```sh
npx openapi-typescript api/openapi.yaml -o src/api/generated/schema.d.ts
```

Verified: produces about 1,500 lines of typed schemas with descriptions preserved. Generated output is git-ignored in `crm.frontend` — it is a build product of this contract, not a source of truth to edit.

## Browse

```sh
npx @redocly/cli preview-docs api/openapi.yaml
```

## Conventions encoded here

From `development/README.md` §8. Worth reading before adding an endpoint, since they are the reason several shapes look the way they do.

| Convention | Encoding |
|---|---|
| Server-derived identity | No request body accepts a role, requester or customer-ownership field |
| Location scope | Explicit `location_id`, checked server-side; `/locations` returns permitted locations only |
| Pagination | `limit`/`offset` with `total` covering the **whole filtered set**, never the page |
| Optimistic concurrency | `version` + `ETag` on reads; `If-Match` required on mutations; stale fails `VERSION_CONFLICT` with `current_version` |
| Idempotency | `Idempotency-Key` parameter defined for reuse; applies from S3 |
| CSRF | `X-CSRF-Token` required on every POST, PATCH and DELETE |
| Single error shape | `{ code, message, field_errors?, current_version?, correlation_id }` |
| Half-open intervals | Closure `ends_at` is exclusive, matching reservations |
| Time | ISO 8601 instants with offset; `LocalDate` where a date must not shift; `Asia/Almaty` applied by the consumer |

## Decisions this draft needs from S0 review

Three shapes were chosen where the specification left room. Each is a question for the contract review, not a settled answer.

**1. Opening hours across midnight.** Courts run 07:00–00:00 and a 00:00 end belongs to the next calendar date. Modelled as `closes_at` plus an explicit `closes_next_day` boolean, rather than a `24:00` string — `closes_at` stays a time every client can parse, and the midnight case becomes explicit instead of implied. Confirm or replace.

**2. Pagination style.** `limit`/`offset` with `total` suits the S1 directory lists. Finance and audit lists in S5 and S9 page over data that changes while being read, where offset paging can skip or repeat rows; cursor pagination may be needed there. Decide now, or accept two styles later.

**3. Sign-in identifier.** `identifier` is deliberately opaque. Whether staff and customers sign in by email, phone or username is part of D-02, and this contract must not fix it by accident.

**4. Rejection reason.** Modelled as optional. `ui-ux.md` §6 forbids requiring a reason because a designer assumed one, so the policy has to be a stated decision — and making it mandatory has not been decided.

**5. Allowed durations.** `GET /availability` returns the permitted durations rather than letting a client offer arbitrary intervals. The demo offered 30 minutes to five hours in half-hour steps; the real list, and any per-customer limit, is D-06.

### Deliberately absent

Not oversights — each needs a decision before it can be specified:

| Missing | Gate |
|---|---|
| Staff-created reservations (immediate vs approval) | D-06 |
| Customer withdrawal of a pending request | D-06 |
| Active-hold and advance-booking limits | D-06 |
| Administrator decisions after the cutoff or after start | D-07 |
| Customer resubmission after a rejected cancellation | D-07 |
| Cancellation interaction with rescheduling; staff override | D-07 |
| No-show and completion handling | D-07 |
| Multiple courts as *alternatives* rather than all-together | D-08 |
| Recurrence and rescheduling | D-08 |
| Financial consequences of a cancellation | D-09 |
| Structured pricing units and duration-based tariffs | D-10 |

`can_decide` on a cancellation request answers only *this actor, this request, now*. It must not be read as settling who may decide after the cutoff — that is D-07.

## The hold mechanism

The S3 endpoints carry the system's hardest correctness requirements. What the contract fixes, and why:

| Rule | Where it is encoded |
|---|---|
| Hold is server request-acceptance time + 300s | `POST /reservations/requests`; `hold_expires_at` is absent from the request body, so a caller cannot set or extend it |
| Confirmation valid only **strictly before** expiry | `confirm` returns 409 `HOLD_EXPIRED`; at exactly the expiry instant the hold is already inactive, even if not yet swept |
| One outcome under a race | Confirm, reject and expiry serialize; a second decision returns the committed outcome with `current_state` |
| Expired requests can never be confirmed | `can_confirm` is computed server-side from database time, so a client cannot present an expired request as actionable |
| Confirmation requires no payment | `financial_summary` is independent and may show an outstanding balance on a confirmed booking (AC-04) |
| Double-click makes one hold | `Idempotency-Key` is **required**; a replay returns the original with its original expiry — retrying never buys time |
| No partial allocation | `resource_ids` are all-or-nothing; a 409 means nothing was written |
| Adjacent intervals don't conflict | `ends_at` documented exclusive throughout — half-open `[start, end)` |
| Availability can't leak identities | `AvailabilitySlot.unavailable_reason` is coarse (`occupied`/`closed`/`outside_hours`) and does not distinguish a hold from a booking |
| Client clocks are never trusted | `server_time` on every reservation response and on both conflict shapes |

`can_confirm` deserves a note: it exists so the *server* decides whether a decision is still possible. A client comparing timestamps itself would get it wrong under clock skew, and `ui-ux.md` §6 requires decision buttons to disappear at server expiry.

## The cancellation cutoff

The S4 rules are confirmed (BR-07, BR-08, BR-09) and encoded the same way the hold is — structurally, not just described:

| Rule | Where it is encoded |
|---|---|
| Accepted up to **exactly** 2 hours before start | `now <= starts_at - 2h`. For a 15:00 start, 13:00:00 passes and 13:00:01 does not (AC-05) |
| Enforced at **submission**, not page load | The server re-evaluates on every request; a form opened early and submitted late is refused |
| The client never computes the deadline | `cancellation_cutoff_at` and `can_request_cancellation` are both server-supplied, so a skewed clock cannot show the wrong deadline or offer an expired action (UX-03) |
| A pending request keeps the court | The reservation stays `confirmed`; `cancellation_request_state` is a *separate* field, never merged into reservation state (BR-08, UX-04) |
| One active request per reservation | A duplicate submission returns the existing record, so a double-tap cannot fill the queue |
| Approval releases the slot exactly once | One transaction records the decision, cancels the reservation and frees allocations; a repeat approval is refused |
| Rejection changes nothing | Booking stays confirmed and occupied |
| **No money moves, either way** | BR-09. Approval touches no payment, charge or payroll; `ui-ux.md` §7 forbids an implicit refund checkbox |
| No second timeout | A cancellation request has no expiry of its own; the queue orders by how soon the *booking* starts |

`reservation_starts_at` is stored **on the request**, so a later reschedule cannot silently change what the customer asked to cancel.

## Changelog

| Version | Date | Change |
|---|---|---|
| 0.3.0 | 26 October 2026 | S4 cancellation: customer request with the two-hour cutoff, administrator approve/reject |
| 0.2.0 | 26 October 2026 | S3 booking core: availability, customer request with five-minute hold, employee confirm/reject |
| 0.1.0 | 25 October 2026 | Initial S1 draft for contract review |

Bump the minor version for additive changes within v1. A breaking change requires a new major base path (`/api/v2`), not an edit in place — the frontend's generated types are built from this file.
