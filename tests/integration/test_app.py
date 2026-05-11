"""Testes do app: health, envelope, request_id, handlers, CORS."""

from __future__ import annotations

import re
from uuid import uuid4

from fastapi import FastAPI
from httpx import AsyncClient

from catalogflow.shared.errors import (
    AuthenticationError,
    ConflictError,
    DomainError,
    NotFoundError,
)

_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE,
)


class TestHealth:
    async def test_returns_200_with_ok_envelope(self, client: AsyncClient) -> None:
        resp = await client.get("/api/v1/health")
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert body["data"] == {"status": "ok", "db": "ok", "redis": "ok"}
        assert body["error"] is None
        assert _UUID_RE.match(body["meta"]["request_id"])
        assert "timestamp" in body["meta"]

    async def test_echoes_request_id_when_provided(self, client: AsyncClient) -> None:
        rid = "rid-test-12345"
        resp = await client.get(
            "/api/v1/health",
            headers={"X-Request-ID": rid},
        )
        assert resp.status_code == 200
        assert resp.headers["X-Request-ID"] == rid
        assert resp.json()["meta"]["request_id"] == rid

    async def test_generates_request_id_when_absent(self, client: AsyncClient) -> None:
        resp = await client.get("/api/v1/health")
        rid = resp.headers["X-Request-ID"]
        assert _UUID_RE.match(rid)
        assert resp.json()["meta"]["request_id"] == rid


class TestDomainErrorHandler:
    async def test_not_found_returns_404_envelope(
        self,
        app: FastAPI,
        client: AsyncClient,
    ) -> None:
        @app.get("/boom/notfound")
        async def _boom() -> None:
            raise NotFoundError("nada aqui", code="THING_NOT_FOUND")

        resp = await client.get("/boom/notfound")
        assert resp.status_code == 404
        body = resp.json()
        assert body["success"] is False
        assert body["data"] is None
        assert body["error"] == {
            "code": "THING_NOT_FOUND",
            "message": "nada aqui",
            "details": {},
        }
        assert _UUID_RE.match(body["meta"]["request_id"])

    async def test_conflict_returns_409(
        self,
        app: FastAPI,
        client: AsyncClient,
    ) -> None:
        @app.get("/boom/conflict")
        async def _boom() -> None:
            raise ConflictError("já existe", code="DUP", details={"key": "v"})

        resp = await client.get("/boom/conflict")
        assert resp.status_code == 409
        assert resp.json()["error"]["details"] == {"key": "v"}

    async def test_auth_returns_401(self, app: FastAPI, client: AsyncClient) -> None:
        @app.get("/boom/auth")
        async def _boom() -> None:
            raise AuthenticationError("não", code="X")

        resp = await client.get("/boom/auth")
        assert resp.status_code == 401

    async def test_subclass_uses_subclass_status(
        self,
        app: FastAPI,
        client: AsyncClient,
    ) -> None:
        class _Custom(DomainError):
            code = "CUSTOM"
            http_status = 418

        @app.get("/boom/custom")
        async def _boom() -> None:
            raise _Custom("teapot")

        resp = await client.get("/boom/custom")
        assert resp.status_code == 418
        assert resp.json()["error"]["code"] == "CUSTOM"


class TestUnhandledExceptionHandler:
    async def test_returns_500_envelope_without_leaking_traceback(
        self,
        app: FastAPI,
        client: AsyncClient,
    ) -> None:
        @app.get("/boom/unhandled")
        async def _boom() -> None:
            raise RuntimeError("segredo interno")

        resp = await client.get("/boom/unhandled")
        assert resp.status_code == 500
        body = resp.json()
        assert body["success"] is False
        assert body["error"]["code"] == "INTERNAL_ERROR"
        assert body["error"]["message"] == "erro interno do servidor"
        # Não vaza message original nem stacktrace para o cliente.
        assert "segredo interno" not in str(body)
        assert _UUID_RE.match(body["meta"]["request_id"])


class TestValidationErrorHandler:
    async def test_invalid_payload_returns_422_envelope(
        self,
        client: AsyncClient,
    ) -> None:
        # Rota interna requer body Pydantic; payload inválido aciona o handler.
        resp = await client.post(
            "/internal/brands",
            headers={"X-Internal-Secret": "test-internal-secret"},
            json={"slug": "?? not valid", "name": ""},
        )
        # Pode ser 422 (Pydantic) ou 400 dependendo da validação custom.
        assert resp.status_code == 422
        body = resp.json()
        assert body["success"] is False
        assert body["error"]["code"] == "VALIDATION_ERROR"
        assert "errors" in body["error"]["details"]


class TestCors:
    async def test_preflight_returns_allowed_headers(
        self,
        client: AsyncClient,
    ) -> None:
        resp = await client.options(
            "/api/v1/health",
            headers={
                "Origin": "http://localhost:3000",
                "Access-Control-Request-Method": "GET",
                "Access-Control-Request-Headers": "authorization",
            },
        )
        assert resp.status_code == 200
        assert resp.headers["access-control-allow-origin"] == "http://localhost:3000"


class TestRequestIdPropagatesThroughHandlers:
    async def test_request_id_consistent_across_envelope_and_header(
        self,
        app: FastAPI,
        client: AsyncClient,
    ) -> None:
        rid = str(uuid4())

        @app.get("/boom/propag")
        async def _boom() -> None:
            raise NotFoundError("nope", code="GONE")

        resp = await client.get("/boom/propag", headers={"X-Request-ID": rid})
        assert resp.headers["X-Request-ID"] == rid
        assert resp.json()["meta"]["request_id"] == rid
