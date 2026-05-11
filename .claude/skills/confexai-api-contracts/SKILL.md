---
name: confexai-api-contracts
description: >
  Contratos de API do ConfexAI. Use esta SKILL sempre que for criar, modificar
  ou revisar endpoints FastAPI, schemas Pydantic, ou convenções de resposta.
  Define os contratos de todos os módulos: produtos, imagens, jobs, SEO e
  página de produto unificada. Consulte antes de criar qualquer endpoint novo
  ou alterar schemas existentes.
---

# ConfexAI — Contratos de API

## Padrão de Resposta Universal
```python
# Sucesso
return StandardResponse(data=result)  # {"data": ...}

# Erro
raise HTTPException(404, detail="Recurso não encontrado.")
raise HTTPException(422, detail="Validação falhou: campo X obrigatório.")
raise HTTPException(429, detail=f"Aguarde {n}s antes de tentar novamente.")
raise HTTPException(500, detail="Erro interno do servidor.")  # nunca str(e)
```

## Padrão de Endpoint FastAPI
```python
@router.post("/recurso", status_code=201)
def create_recurso(
    payload: RecursoCreate,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    try:
        db.commit()
        return StandardResponse(data=result)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Erro: {e}", exc_info=True)
        raise HTTPException(500, detail="Erro interno do servidor.")
```

## Endpoints por Módulo

### Auth
- `POST /auth/login` → 200 `{access_token, token_type}`

### Produtos
- `POST /products` → 201
- `GET /products` → 200 lista
- `GET /products/{id}` → 200
- `GET /products/{id}/summary` → 200 (imagens + variações + SEO + stats)
- `DELETE /products/{id}` → 200 (soft delete)
- `POST /products/{id}/seo` → 202 (rate limit 30s)
- `GET /products/{id}/seo` → 200

### Imagens
- `POST /products/{id}/images/upload?view={view}` → 201
- `POST /products/{id}/images/{imageId}/remove-background` → 202

### Jobs
- `POST /jobs/detect-protected-regions` → 202
- `POST /jobs/color-variation` → 202
- `GET /jobs` → 200 (filtra `deleted_at=None`, `is_archived=False` por padrão)
- `GET /jobs/{id}` → 200 (filtra `deleted_at=None`)
- `GET /jobs/history?limit=50&offset=0` → 200 paginado
- `POST /jobs/{id}/approve` → 200 (filtra `deleted_at=None`)
- `POST /jobs/{id}/reject` → 200 (filtra `deleted_at=None`)
- `PATCH /jobs/{id}/archive` → 200 (filtra `deleted_at=None`)
- `PATCH /jobs/{id}/unarchive` → 200 (filtra `deleted_at=None`)
- `PATCH /jobs/{id}/delete` → 200 (soft delete — define `deleted_at=now()`)
- `DELETE /jobs/cleanup-broken` → 200 (hard delete de jobs corrompidos sem arquivo)
- `GET /jobs/export/{product_id}` → ZIP stream
- `POST /jobs/export/bulk` → ZIP stream

## Validações Pydantic Obrigatórias
```python
# SEMPRE usar Literal para campos com valores fixos
from typing import Literal

PlatformType = Literal["mercadolivre", "shopee", "shopify"]
ViewType = Literal["frente", "costas", "lat_direita", "lat_esquerda"]
JobType = Literal["color_variation", "protected_region_detection",
                  "background_removal", "seo_description", "video_ugc"]

# NUNCA
platforms: list[str]  # aceita valores inválidos
```

## Campos deleted_at — Regra Universal

**Todo endpoint que lista ou busca jobs deve filtrar `deleted_at == None`.**
Exceção: `cleanup_broken_jobs` (ferramenta de manutenção).
```python
# Obrigatório em TODA query de job para o usuário
.filter(GenerationJob.deleted_at == None)
```
