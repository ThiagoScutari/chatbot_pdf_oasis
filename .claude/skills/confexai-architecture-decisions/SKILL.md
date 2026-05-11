---
name: confexai-architecture-decisions
description: >
  Decisões arquiteturais e ADRs do ConfexAI (DRX Têxtil). Use esta SKILL sempre
  que for sugerir mudanças estruturais, novos padrões, bibliotecas, frameworks,
  ou abordagens de design no ConfexAI. Também use ao responder perguntas sobre
  "por que fazemos X dessa forma", ao avaliar trade-offs técnicos, ou ao planejar
  features que afetam a arquitetura. Esta SKILL previne sugestões que contradizem
  decisões já tomadas. Consulte sempre antes de propor mudanças estruturais.
---

# ConfexAI — Decisões Arquiteturais

Ver registro completo em `docs/decisions/ADRs.md`. Este arquivo é o resumo
operacional para uso no Claude Code.

## Decisões Imutáveis — Nunca Contestar

### Stack e Frameworks
- **React** no frontend (ADR-001) — estado complexo de pipeline
- **FastAPI monolito** (ADR-002) — MVP single-developer, sem microserviços
- **Sem Celery no MVP** (ADR-008) — jobs síncronos, reavaliar com > 500 jobs/dia

### Banco de Dados
- **Soft delete universal** (ADR-003) — `is_archived` ou `is_active` ou `deleted_at`,
  nunca `db.delete()` em entidades de negócio
  - Exceção documentada: `cleanup_broken_jobs` faz hard delete de jobs corrompidos
    sem arquivo em disco (ferramenta de manutenção, não entidade de negócio)
- **Migrations manuais** (ADR-004) — scripts idempotentes em `migrations/migrate_sprint_NN.py`
  com rollback correspondente `rollback_sprint_NN.py`

### APIs de IA
- **google-genai SDK** (ADR-006) — não usar `google-generativeai` (legado)
```python
  # CORRETO
  from google import genai
  from google.genai import types
  config=types.GenerateContentConfig(response_modalities=["IMAGE", "TEXT"])

  # PROIBIDO — causa HTTP 400
  response_mime_type="image/png"
```
- **Pillow fallback** (ADR-007) — automático se Gemini falhar, registrar `fallback_reason`
- **rembg local** (ADR-005) — motor primário de remoção de fundo, gratuito

### Pipeline de Imagem (PDCA Sprint 16)
- **`job_short_id` no output_path** — obrigatório para evitar colisão de arquivos
```python
  # CORRETO — único por job
  job_short_id = str(job.id)[:8]
  output_path = Path(image_path).parent / f"color_{safe_hex}{view_suffix}_{job_short_id}.png"

  # PROIBIDO — causa colisão entre execuções
  output_path = Path(image_path).parent / f"color_{safe_hex}{view_suffix}.png"
```
- **`db.flush()` antes de construir output_path** — para que `job.id` exista
- **`color_hex` no result JSON** — sempre incluir, frontend depende disso

### Segurança e Validação
- **`dangerouslySetInnerHTML` proibido** (ADR-013) com conteúdo externo
- **`Literal` types no Pydantic** (ADR-012) para campos com valores fixos
- **CORS inclui PATCH e DELETE** (ADR-010) — necessário para archive/unarchive/delete
- **Auditoria `sgp-sprint-review` obrigatória antes do commit** (ADR-015)

### Frontend
- **Tema "Industrial Refinado"** — dark, amber (#f59e0b), DM fonts
- **Nunca usar** Inter, Roboto, system-ui, purple gradients, fundo branco

## Antipadrões que Quebraram Sprints

| Antipadrão | Sprint | Consequência |
|---|---|---|
| `response_mime_type: "image/png"` | 03 | HTTP 400 no Gemini |
| `original.png` sem view no nome | 10 | Sobrescreve outras views |
| `color_{HEX}_{VIEW}.png` sem job_id | 16 | Colisão entre execuções |
| `dangerouslySetInnerHTML` sem sanitização | 12 | XSS |
| CORS sem PATCH | 07 | Archive falha silenciosamente |
| `db.delete()` em entidade de negócio | 01 | Dados perdidos |
| API externa sem mock em teste | 02 | Custo real + flakiness |
