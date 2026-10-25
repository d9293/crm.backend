# crm.backend

Server API and domain logic for the ACE / CSKA tennis-centre CRM.

PostgreSQL is the authoritative persistent store. The API contract is versioned OpenAPI published from this repository; the frontend generates or validates its types against it.

## Status

**Not scaffolded.** `development/README.md` §3 states: *"Do not scaffold a framework until D-01 is resolved."* This repository therefore holds documentation and ignore rules only.

Resolve **D-01** — language, framework, session strategy and supported deployment environment — then BE-00 delivers the modular application layout, migrations, OpenAPI document and CI.

## Specification

| Document | Use |
|---|---|
| `development/backend.md` | Ticket backlog BE-00–BE-21 |
| `development/README.md` | Business rules, state machines, API conventions, data model boundaries, AC-01–15 |
| `development/decisions.md` | Open decisions — a feature whose decision is unresolved is not implementation-ready |
| `development/infra.md` | Environment, CI and release tickets INF-00–INF-08 |

## D-01: engineering recommendation

Pending owner and team review. Recorded here so the reasoning is visible rather than re-argued later. This is a recommendation, not a decision.

**Recommended: Python.** The difficulty in this system is not request throughput — it is correctness in four areas, and Python's ecosystem addresses each directly:

| Requirement | Why Python helps |
|---|---|
| Exact money (`README.md` §6: no binary floating point) | `decimal.Decimal` in the standard library; mature Postgres numeric mapping |
| Versioned migrations with a drift check (INF-01) | Alembic does autogenerate-and-diff as a first-class workflow |
| Excel export matching a supplied sample (BE-16, D-14) | `openpyxl` reproduces formulas, formats and layout |
| OpenAPI as the cross-team contract (§3) | FastAPI generates the document from the same models that validate requests |

**Where Go would win:** raw concurrency throughput and single-binary deployment. Neither is the constraint here. Two locations with roughly a dozen courts produce a workload well inside either language's capacity, and D-16 has not yet supplied volume figures that would change that. Go's advantage would be real if this were a high-fanout service; it is not.

**What the choice does not change.** The hard concurrency work lives in PostgreSQL — row locks in deterministic order, the `btree_gist` exclusion constraint, and expiry evaluated from database time inside the allocation transaction. That strategy is identical in either language, and it is where AC-01, AC-02 and AC-03 are won or lost. Picking Go would not make the booking race easier; picking Python will not make it harder.

Proposed concretely: Python 3.13+, FastAPI, SQLAlchemy 2.x, Alembic, `psycopg` 3, pytest with testcontainers or the Compose test database. Session as a secure `HttpOnly` cookie with CSRF protection on state-changing requests, per BE-01.

## Non-negotiable constraints

From `development/backend.md`; each exists to prevent a specific defect class, not as preference.

- **Authorization server-side on every read, write and export.** Derive identity server-side; never trust a client-supplied role or customer ownership. Fail closed when a policy is not configured. UI hiding is not enforcement.
- **Validate domain transitions in the service layer inside the transaction,** not only as request-field validation.
- **Exact monetary arithmetic.** Integer minor units in the API, currency `KZT`, exact decimal intermediates, an explicit rounding rule. No binary floats anywhere near money.
- **Immutable posted financial entries.** Corrections are linked records with actor and reason. Never delete operational or financial history through entity cascades.
- **Idempotent commands** for every duplicate-prone effect — reservation requests, financial commands, membership activation, attendance. Store command key, request digest and committed response transactionally. Re-validate ownership and location on retries.
- **Optimistic versions** on mutable records; reject stale updates rather than silently overwriting.
- **Transactional outbox** for post-commit notifications. A committed payment must never be rolled back because push or email was unavailable.
- **Integration tests against real PostgreSQL.** SQLite and mocks cannot validate exclusion constraints, row locking or the booking races in AC-01–03. CI enforces this (INF-01).
- **Hold expiry is evaluated from database time**, inside the allocation transaction. Correctness must not depend on the cleanup worker running on time — AC-02 must pass with the worker stopped.
- **Half-open intervals** `[start, end)`: 10:00–11:00 and 11:00–12:00 do not conflict.
- **Timezone-aware instants stored in UTC**; `Asia/Almaty` for business display, report boundaries and recurrence expansion. Date-only values such as birth dates and tariff dates must not shift on conversion.
- **No secrets or personal data in logs.** Error responses carry no stack traces.

## Not authorized

Automated bank charging, Kaspi API integration, fiscalization, public self-registration, customer access to other people's records, and automatic financial write-offs are outside this specification. Each needs a separate scope decision. Selecting "Kaspi" as a payment method records how money was received; it is not an integration.
