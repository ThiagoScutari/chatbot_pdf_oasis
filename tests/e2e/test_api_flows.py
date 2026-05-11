"""Testes E2E — flow HTTP completo via httpx.

Não há Celery rodando: emulamos o worker chamando `process_catalog` direto
no service após capturar o `(catalog_id, job_id)` da resposta 202.
"""

from __future__ import annotations

from pathlib import Path
from uuid import UUID

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from catalogflow.modules.auth.models import Brand
from catalogflow.modules.catalog.service import CatalogService
from tests.fakes import FakeStorage

FIXTURES_DIR = Path(__file__).resolve().parents[1] / "fixtures"


def _load(name: str) -> bytes:
    path = FIXTURES_DIR / name
    if not path.exists():
        pytest.skip(f"fixture {name} ausente")
    return path.read_bytes()


class TestFullCatalogFlow:
    async def test_upload_poll_process_download(
        self,
        client: AsyncClient,
        brand_with_key: tuple[Brand, str],
        db_session: AsyncSession,
        fake_storage: FakeStorage,
    ) -> None:
        brand, raw_key = brand_with_key
        headers = {"Authorization": f"Bearer {raw_key}"}

        # 1) Health check (público)
        resp = await client.get("/api/v1/health")
        assert resp.status_code == 200
        assert resp.json()["data"]["status"] == "ok"

        # 2) Upload do catálogo
        pdf_bytes = _load("catalogo_1_produto_2_cores.pdf")
        resp = await client.post(
            "/api/v1/catalogs/process",
            headers=headers,
            files={"file": ("c.pdf", pdf_bytes, "application/pdf")},
            data={"name": "E2E Catálogo", "collection": "MOTION"},
        )
        assert resp.status_code == 202
        body = resp.json()
        assert body["success"] is True
        catalog_id = body["data"]["catalog_id"]
        job_id = body["data"]["job_id"]
        poll_url = body["data"]["poll_url"]
        assert poll_url == f"/api/v1/jobs/{job_id}"

        # 3) Polling — job ainda pending
        resp = await client.get(poll_url, headers=headers)
        assert resp.status_code == 200
        assert resp.json()["data"]["status"] == "pending"

        # 4) Download antes de pronto → 409
        resp = await client.get(
            f"/api/v1/catalogs/{catalog_id}/download",
            headers=headers,
        )
        assert resp.status_code == 409
        assert resp.json()["error"]["code"] == "CATALOG_NOT_READY"

        # 5) Worker simulado — chama process_catalog direto
        worker_service = CatalogService(
            db_session,
            storage=fake_storage,  # type: ignore[arg-type]
        )
        await worker_service.process_catalog(
            catalog_id=UUID(catalog_id),
            job_id=UUID(job_id),
        )
        await db_session.commit()

        # 6) Polling novamente — job success
        resp = await client.get(poll_url, headers=headers)
        assert resp.status_code == 200
        job_data = resp.json()["data"]
        assert job_data["status"] == "success"
        assert job_data["progress"] == 100
        assert job_data["result"]["n_fields"] == 8

        # 7) GET do catálogo — agora ready com produtos
        resp = await client.get(
            f"/api/v1/catalogs/{catalog_id}",
            headers=headers,
        )
        assert resp.status_code == 200
        catalog_data = resp.json()["data"]
        assert catalog_data["status"] == "ready"
        assert catalog_data["n_fields"] == 8
        assert catalog_data["n_skus"] == 1
        assert len(catalog_data["products"]) == 1
        assert catalog_data["products"][0]["sku"] == "0442500912-0"
        assert catalog_data["products"][0]["n_colors"] == 2

        # 8) Download — 302 com Location apontando para storage
        resp = await client.get(
            f"/api/v1/catalogs/{catalog_id}/download",
            headers=headers,
        )
        assert resp.status_code == 302
        location = resp.headers["location"]
        assert "fake-s3" in location
        assert f"{brand.id}/catalogs/" in location
        assert location.endswith("?token=test")

    async def test_unauthenticated_upload_returns_401(
        self,
        client: AsyncClient,
    ) -> None:
        resp = await client.post(
            "/api/v1/catalogs/process",
            files={
                "file": (
                    "c.pdf",
                    _load("catalogo_1_produto_1_cor.pdf"),
                    "application/pdf",
                ),
            },
            data={"name": "x"},
        )
        assert resp.status_code == 401
        assert resp.json()["error"]["code"] == "MISSING_CREDENTIAL"

    async def test_request_id_is_propagated_through_full_flow(
        self,
        client: AsyncClient,
        brand_with_key: tuple[Brand, str],
    ) -> None:
        _, raw_key = brand_with_key
        rid = "rid-e2e-flow-001"
        resp = await client.get(
            "/api/v1/health",
            headers={"X-Request-ID": rid, "Authorization": f"Bearer {raw_key}"},
        )
        assert resp.headers["X-Request-ID"] == rid
        assert resp.json()["meta"]["request_id"] == rid
