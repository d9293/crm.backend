"""The error contract.

Every failure path must produce the single shape from `README.md` section 8,
carry a correlation id, and disclose nothing internal.
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel

from app.errors import CORRELATION_HEADER, ApiError, ErrorCode, register_error_handlers


class Payload(BaseModel):
    name: str
    count: int


def build_app() -> FastAPI:
    app = FastAPI()
    register_error_handlers(app)

    @app.get("/boom-domain")
    async def boom_domain() -> None:
        raise ApiError(
            status_code=409,
            code=ErrorCode.VERSION_CONFLICT,
            message="Запись изменена другим пользователем. Обновите данные.",
            current_version=7,
        )

    @app.get("/boom-conflict-detail")
    async def boom_conflict_detail() -> None:
        raise ApiError(
            status_code=409,
            code=ErrorCode.SLOT_UNAVAILABLE,
            message="Это время уже занято.",
            extra={"conflicts": [{"resource_id": 11}]},
        )

    @app.post("/validated")
    async def validated(payload: Payload) -> dict[str, str]:
        return {"ok": "yes"}

    @app.get("/boom-unexpected")
    async def boom_unexpected() -> None:
        # Deliberately leaks something sensitive in the message, to prove it
        # never reaches the client.
        raise RuntimeError("connection to postgres://user:hunter2@db failed")

    return app


client = TestClient(build_app(), raise_server_exceptions=False)


def test_domain_error_uses_the_contract_shape() -> None:
    response = client.get("/boom-domain")
    body = response.json()

    assert response.status_code == 409
    assert body["code"] == ErrorCode.VERSION_CONFLICT
    assert body["current_version"] == 7
    assert body["correlation_id"]
    assert response.headers[CORRELATION_HEADER] == body["correlation_id"]


def test_structured_detail_is_preserved() -> None:
    # SLOT_UNAVAILABLE carries the conflicting intervals so the UI can refresh
    # availability and keep the user's other selections.
    body = client.get("/boom-conflict-detail").json()

    assert body["code"] == ErrorCode.SLOT_UNAVAILABLE
    assert body["conflicts"] == [{"resource_id": 11}]


def test_validation_errors_are_reshaped_to_field_errors() -> None:
    response = client.post("/validated", json={"count": "not a number"})
    body = response.json()

    assert response.status_code == 422
    assert body["code"] == ErrorCode.VALIDATION_FAILED
    # FastAPI's own shape would force forms to parse two formats.
    assert "field_errors" in body
    assert "name" in body["field_errors"]
    assert "count" in body["field_errors"]
    assert isinstance(body["field_errors"]["name"], list)


def test_unexpected_errors_disclose_nothing() -> None:
    response = client.get("/boom-unexpected")
    body = response.json()

    assert response.status_code == 500
    assert body["code"] == ErrorCode.INTERNAL_ERROR
    # The exception text, the connection string and the password must not
    # reach the client. The correlation id is the only bridge to the log.
    serialized = str(body)
    assert "hunter2" not in serialized
    assert "postgres://" not in serialized
    assert "RuntimeError" not in serialized
    assert "Traceback" not in serialized
    assert body["correlation_id"]


def test_every_error_has_a_correlation_id() -> None:
    for path in ("/boom-domain", "/boom-unexpected"):
        body = client.get(path).json()
        assert body.get("correlation_id"), f"{path} produced no correlation id"


def test_framework_404_uses_the_contract_shape() -> None:
    # The most common error on the API. Without an HTTPException handler
    # FastAPI's default {"detail": "Not Found"} escapes, and a client
    # branching on `code` reads it as unknown.
    response = client.get("/no-such-route")
    body = response.json()

    assert response.status_code == 404
    assert body["code"] == ErrorCode.NOT_FOUND
    assert body["correlation_id"]
    assert "detail" not in body


def test_framework_405_uses_the_contract_shape() -> None:
    response = client.post("/boom-domain")
    body = response.json()

    assert response.status_code == 405
    assert body["code"] == ErrorCode.NOT_FOUND
    assert body["correlation_id"]
    assert "detail" not in body
