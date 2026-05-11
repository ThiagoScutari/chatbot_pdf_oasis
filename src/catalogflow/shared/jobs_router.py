"""Endpoint de polling de jobs assíncronos.

Compartilhado entre todos os módulos: catalog, orders e romaneio (futuros)
gravam jobs na mesma tabela. O cliente faz `GET /api/v1/jobs/{job_id}` até
ver `status` `success` ou `error`.

Multi-tenant: cada query inclui `brand_id` no WHERE; um job de outra brand
retorna 404 (sem vazar existência).
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from catalogflow.infra.database import get_db
from catalogflow.modules.auth.dependencies import get_current_brand
from catalogflow.modules.auth.models import Brand
from catalogflow.modules.catalog.models import Job
from catalogflow.modules.catalog.schemas import JobResponse
from catalogflow.shared.errors import NotFoundError
from catalogflow.shared.middleware import get_request_id
from catalogflow.shared.responses import StandardResponse, ok

router = APIRouter(prefix="/api/v1/jobs", tags=["jobs"])


@router.get(
    "/{job_id}",
    summary="Retorna o status atual de um job assíncrono.",
)
async def get_job(
    job_id: UUID,
    request: Request,
    brand: Brand = Depends(get_current_brand),
    db: AsyncSession = Depends(get_db),
) -> StandardResponse[JobResponse]:
    stmt = select(Job).where(Job.id == job_id, Job.brand_id == brand.id)
    result = await db.execute(stmt)
    job = result.scalar_one_or_none()
    if job is None:
        raise NotFoundError(
            f"job {job_id} não encontrado",
            code="JOB_NOT_FOUND",
            details={"job_id": str(job_id)},
        )
    return ok(
        JobResponse.model_validate(job),
        request_id=get_request_id(request),
    )
