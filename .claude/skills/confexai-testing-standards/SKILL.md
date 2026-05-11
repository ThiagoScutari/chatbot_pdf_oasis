---
name: confexai-testing-standards
description: >
  Padrões de testes obrigatórios do ConfexAI. Use esta SKILL sempre que for
  escrever, revisar ou planejar testes. Define: banco de teste correto, fixtures
  obrigatórias, estrutura por módulo, mocks de APIs externas, e os cenários
  mínimos por endpoint. Nunca escreva testes no ConfexAI sem consultar esta
  SKILL primeiro.
---

# ConfexAI — Padrões de Testes

## Regras Inegociáveis

- **Banco de teste:** `confexai_test_db` — NUNCA `confexai_db`
- **APIs externas:** sempre mockar Anthropic, Gemini, KlingAI
- **Imagens em fixtures:** usar PIL para criar PNG real em disco
- **Cleanup:** fixtures devem deletar arquivos e registros ao final

## Mock Obrigatório para APIs Externas
```python
# Anthropic Claude
with patch("app.services.protected_regions.anthropic.Anthropic") as mock:
    mock.return_value.messages.create.return_value = MagicMock(
        content=[MagicMock(text='{"has_protected_regions": false, "protected_regions": []}')],
        usage=MagicMock(input_tokens=100, output_tokens=50)
    )

# Google Gemini
with patch("app.services.color_variation.genai.Client") as mock:
    ...

# SEO Generator
with patch("app.api.products.SEOGeneratorService") as MockSvc:
    MockSvc.return_value = _mock_seo_service()
```

## Cenários Mínimos por Endpoint

| Tipo de endpoint | Cenários obrigatórios |
|---|---|
| GET lista | 200 autenticado, 403 sem token |
| GET item | 200 encontrado, 403, 404 não encontrado |
| POST criação | 201 criado, 403, 422 payload inválido |
| POST ação (approve/reject) | 200, 403, 404, 409 estado errado |
| PATCH (archive/unarchive) | 200, 403, 404 |
| PATCH soft delete | 200, 403, 404, 404 duplo (já deletado) |
| DELETE hard (cleanup) | 200, 403 |
| Endpoint custoso (SEO) | 200, 403, 429 rate limit |

**Nota:** HTTPBearer retorna 403 (não 401) quando o token está ausente.

## Fixture de Imagem Real
```python
@pytest.fixture
def sample_image_uploaded(db, sample_product):
    import io
    from PIL import Image as PILImage
    from pathlib import Path
    import os

    upload_dir = Path(os.getenv("UPLOAD_DIR", "/app/examples/uploads"))
    product_dir = upload_dir / str(sample_product.id)
    product_dir.mkdir(parents=True, exist_ok=True)
    img_path = product_dir / "original_frente.png"

    img = PILImage.new("RGBA", (600, 600), (150, 100, 80, 255))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    img_path.write_bytes(buf.getvalue())

    from app.models import ProductImage
    image = ProductImage(
        product_id=sample_product.id,
        type="original",
        view="frente",
        original_url=str(img_path),
    )
    db.add(image)
    db.commit()
    db.refresh(image)
    yield image

    db.delete(image)
    db.commit()
    if img_path.exists():
        img_path.unlink()
```

## Jobs com deleted_at — Padrão de Teste
```python
# Para testar que endpoints rejeitam jobs soft-deleted:
from datetime import datetime
job.deleted_at = datetime.utcnow()
db.commit()
response = client.post(f"/api/v1/jobs/{job.id}/approve", headers=auth_headers)
assert response.status_code == 404
```

## Contagem Atual de Testes (Sprint 17.1)

86 testes em 17 arquivos. Todo sprint deve manter 0 falhas.
Endpoints novos obrigatoriamente adicionam testes antes do commit.
