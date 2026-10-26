# crm.backend

Server API and domain logic for the ACE / CSKA tennis-centre CRM.

PostgreSQL is the authoritative persistent store. The API contract is versioned OpenAPI published from this repository; the frontend generates or validates its types against it.

## Status

**BE-00 complete.** D-01 resolved to **Python** on 26 October 2026: FastAPI, SQLAlchemy 2.x, Alembic, psycopg 3, PostgreSQL 18.

The application boots, connects to PostgreSQL, reports health and readiness, and fails in the contract's error shape. No domain endpoints yet — those arrive with their sprints, starting with BE-01 (session and current user) once **D-02** and **D-04** are answered.

## Quick start

Requires Python 3.13 and a running PostgreSQL (`docker compose up -d postgres` in `crm.infra`).

```sh
py -m venv .venv
.venv/Scripts/python -m pip install -e ".[dev]"     # Windows
# source .venv/bin/activate && pip install -e ".[dev]"   # Linux/macOS

# Required — no defaults, by design
export POSTGRES_DB=crm_dev POSTGRES_USER=crm_dev
export POSTGRES_PASSWORD=... APP_SECRET_KEY=...

alembic upgrade head
uvicorn app.main:app --reload
```

Or through Compose from `crm.infra`:

```sh
docker compose run --rm migrate      # explicit, never automatic
docker compose up -d backend
```

| Endpoint | Purpose |
|---|---|
| `GET /health` | Liveness. Never touches the database — a database blip must not trigger a container restart. |
| `GET /ready` | Readiness. Checks PostgreSQL; returns 503 when unreachable. |
| `GET /docs` | Swagger UI. Disabled when `ENVIRONMENT=production`. |

## Commands

```sh
ruff check .            # lint
ruff format .           # format
mypy app                # typecheck, strict
pytest -q               # tests
alembic upgrade head    # apply migrations
alembic check           # assert models and migrations have not diverged
alembic downgrade base  # roll back
```

All of these run in CI and must pass before merge.

## Layout

```
app/
  config.py    Settings from the environment. Secrets have no defaults and are
               redacted from __repr__.
  errors.py    The single error shape and every handler that produces it.
  db.py        Engine, session factory, declarative base, naming convention.
  models.py    Cross-cutting tables: audit_event, idempotency_key.
  main.py      FastAPI app, CORS, correlation id, health and readiness.
migrations/    Alembic. The URL comes from settings, never from alembic.ini.
tests/
api/           The agreed OpenAPI contract.
```

## Decisions encoded in the scaffold

Worth knowing before extending it — each exists to prevent a specific failure:

- **`audit_event` is append-only, enforced by a PostgreSQL trigger** that refuses `UPDATE`, `DELETE` *and* `TRUNCATE`. An application-level guard would be bypassed by any direct SQL, and an audit trail that can be quietly rewritten is not evidence of anything.
- **`idempotency_key` uniqueness is a database constraint** on (key, actor, operation). A concurrent duplicate loses in the database rather than in a check-then-insert race.
- **Migrations never run on container start.** They are an explicit operator command, so a rolling restart cannot race three migration runs against one database.
- **`/health` and `/ready` are different questions.** Liveness must not depend on the database.
- **Secrets have no defaults.** A missing password stops the process at boot rather than falling back to something that happens to work.
- **Every error carries a correlation id**, including unhandled ones. The detail stays in the log; the client gets a code and an id to quote.

## API contract

[`api/openapi.yaml`](api/openapi.yaml) — draft S1 contract, covering access, employees, locations and resources. See [`api/README.md`](api/README.md) for validation, type generation and the three shape questions it raises for S0 review.

Written ahead of the server deliberately: the contract is language-neutral, so it is not blocked by D-01, and §3 requires both teams to agree contract examples before a sprint starts.

```sh
npx @redocly/cli lint    # zero errors, zero warnings
```

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
