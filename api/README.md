# API contract

Versioned OpenAPI contract for the ACE / CSKA CRM. `development/README.md` §3 places it in this repository; the frontend generates or validates its types against it.

## Status

`openapi.yaml` — **draft for S0 contract review.** Covers the S1 surface only:

| Page | Endpoints |
|---|---|
| P01 Access | `POST/GET/DELETE /session`, `GET /me` |
| P02 Employees | `/employees`, `/employees/{id}`, `/archive`, `/sessions`, `/meta/capabilities` |
| P03 Locations and resources | `/locations`, `/locations/{id}/resources`, `/resources/{id}`, `/resources/{id}/closures` |

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

## Changelog

| Version | Date | Change |
|---|---|---|
| 0.1.0 | 25 October 2026 | Initial S1 draft for contract review |

Bump the minor version for additive changes within v1. A breaking change requires a new major base path (`/api/v2`), not an edit in place — the frontend's generated types are built from this file.
