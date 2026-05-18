"""Endpoints HTTP do módulo `catalog`.

Todos sob `/api/v1/catalogs/`. Autenticação via `Authorization: Bearer cf_...`
(dependency `get_current_brand`). Multi-tenant: cada query passa pelo
`brand_id` no service.
"""

from __future__ import annotations

import logging
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, Request, Response, UploadFile, status
from fastapi.responses import RedirectResponse

from catalogflow.modules.auth.dependencies import get_current_brand
from catalogflow.modules.auth.models import Brand
from catalogflow.modules.catalog.dependencies import get_catalog_service
from catalogflow.modules.catalog.schemas import (
    CatalogProductResponse,
    CatalogResponse,
    ProcessCatalogResponse,
)
from catalogflow.modules.catalog.service import CatalogService
from catalogflow.shared.errors import JobNotReadyError
from catalogflow.shared.middleware import get_request_id
from catalogflow.shared.responses import StandardResponse, ok

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/catalogs", tags=["catalog"])


@router.post(
    "/process",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Submete um catálogo PDF para processamento assíncrono.",
)
async def process_catalog(
    request: Request,
    file: UploadFile = File(..., description="PDF do catálogo (até 50MB)."),
    name: str = Form(..., min_length=1, max_length=255),
    collection: str | None = Form(default=None, max_length=128),
    brand: Brand = Depends(get_current_brand),
    service: CatalogService = Depends(get_catalog_service),
) -> StandardResponse[ProcessCatalogResponse]:
    """Aceita o upload, dispara a task Celery e devolve `job_id` para polling.

    A validação de tipo é server-side (assinatura `%PDF`). Tamanho é
    verificado contra `settings.max_pdf_size_bytes` antes do upload.
    """
    pdf_bytes = await file.read()
    catalog, job = await service.create_catalog(
        brand_id=brand.id,
        name=name,
        collection=collection,
        pdf_bytes=pdf_bytes,
    )
    payload = ProcessCatalogResponse(
        catalog_id=catalog.id,
        job_id=job.id,
        status="pending",
        poll_url=f"/api/v1/jobs/{job.id}",
    )
    logger.info(
        "catalog.process accepted (catalog=%s job=%s brand=%s)",
        catalog.id,
        job.id,
        brand.id,
    )
    return ok(payload, request_id=get_request_id(request))


@router.get(
    "/{catalog_id}",
    summary="Retorna metadados e produtos do catálogo.",
)
async def get_catalog_endpoint(
    catalog_id: UUID,
    request: Request,
    brand: Brand = Depends(get_current_brand),
    service: CatalogService = Depends(get_catalog_service),
) -> StandardResponse[CatalogResponse]:
    """Recupera o catálogo, garantindo isolamento por brand."""
    catalog = await service.get_catalog(catalog_id, brand.id)
    payload = CatalogResponse(
        id=catalog.id,
        brand_id=catalog.brand_id,
        name=catalog.name,
        collection=catalog.collection,
        status=catalog.status,  # type: ignore[arg-type]
        n_pages=catalog.n_pages,
        n_product_pages=catalog.n_product_pages,
        n_skus=catalog.n_skus,
        n_fields=catalog.n_fields,
        error_message=catalog.error_message,
        created_at=catalog.created_at,
        updated_at=catalog.updated_at,
        products=[CatalogProductResponse.model_validate(p) for p in catalog.products],
    )
    return ok(payload, request_id=get_request_id(request))


@router.get(
    "/{catalog_id}/download",
    summary="Download do PDF editável — bytes diretos em dev, 302 presigned em produção.",
)
async def download_catalog(
    catalog_id: UUID,
    brand: Brand = Depends(get_current_brand),
    service: CatalogService = Depends(get_catalog_service),
) -> Response:
    """Em dev (`s3_public_url` definido) serve os bytes pelo backend.

    Em produção retorna 302 para a URL assinada do storage. 409 se o catálogo
    ainda não está pronto.
    """
    catalog = await service.get_catalog(catalog_id, brand.id)
    if catalog.status != "ready" or not catalog.output_key:
        raise JobNotReadyError(
            f"catalog {catalog_id} ainda não está pronto (status={catalog.status})",
            code="CATALOG_NOT_READY",
            details={"catalog_id": str(catalog_id), "status": catalog.status},
        )
    if service.settings.s3_public_url:
        pdf_bytes = await service.storage.download(catalog.output_key)
        return Response(content=pdf_bytes, media_type="application/pdf")
    url = await service.storage.presigned_url(catalog.output_key)
    return RedirectResponse(url=url, status_code=status.HTTP_302_FOUND)
