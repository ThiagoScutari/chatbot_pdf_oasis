---
name: camisart-sprint-workflow
description: >
  Workflow obrigatório de desenvolvimento do Camisart AI (Camisart Belém). Use esta
  SKILL sempre que for iniciar qualquer tarefa de desenvolvimento no Camisart AI —
  nova feature, correção de bug, refatoração, migração de banco ou abertura de sprint.
  Define o ritual completo: inspeção → feedback → aprovação → implementação → testes
  → CI verde → commits atômicos → sprint review → merge. Nunca pule etapas. Nunca
  implemente sem inspeção prévia aprovada. Nunca faça merge sem CI verde e sprint
  review. Use também ao escrever PRDs, planejar sprints ou estruturar prompts de
  implementação para o Claude Code.
---

# Camisart AI — Workflow de Sprint

## Filosofia Central

**A IA é o estagiário sênior. O arquiteto é o humano.**

- Nunca tome decisões arquiteturais sem debate
- Nunca implemente sem inspeção prévia aprovada
- Nunca commite com testes falhando — 0 falhas é inegociável
- Nunca faça merge sem CI verde + `camisart-sprint-review` aprovado
- Commits atômicos — um por item do PRD, sem agrupamentos

---

## Stack de Referência

| Componente | Tecnologia | Nota |
|---|---|---|
| Linguagem | Python 3.12+ | |
| Framework | FastAPI + Uvicorn/Gunicorn | Async nativo |
| Banco produção | PostgreSQL 15+ em VPS | `camisart_db` |
| Banco de testes | PostgreSQL 15+ local | `camisart_test_db` — NUNCA o de produção |
| ORM | SQLAlchemy 2.x | Migrations em `app/migrations/migrate_sprint_NN.py` |
| Testes | pytest + pytest-asyncio + pytest-cov | Direto no venv, sem Docker na Fase 1 |
| CI | GitHub Actions | Obrigatório — PR só merga com CI verde |
| Deploy | VPS Hostinger + systemd + nginx | Sem Docker-compose na Fase 1 |
| Canal WhatsApp | WhatsApp Cloud API (Meta) | HMAC obrigatório em todo webhook |
| Lint | ruff | `ruff check app/` — 0 erros antes do commit |

---

## O Ritual — 8 Passos Obrigatórios

### Passo 1 — Inspeção Cirúrgica

**Antes de qualquer implementação**, gerar prompt de inspeção para o Claude Code.

```
Read docs/project_specz.md and docs/PRD_Sprint_NN_*.md.

Do NOT implement anything. Inspection only.

1. [Item a inspecionar]
   - Show exact file:line and literal code
   - Confirm [condição específica]
```

A inspeção deve mostrar arquivo, linha e código literal. NÃO sugerir correções. NÃO modificar arquivos.

### Passo 2 — Feedback e Análise

O arquiteto reporta o resultado. Claude analisa:
- ✅ Confirmado — gap real, proceder
- ❌ Falso positivo — descartar e documentar em ADRs
- ⚠️ Parcial — ajustar escopo antes de implementar

**Nunca avançar sem este passo.**

### Passo 3 — Aprovação Explícita

O arquiteto aprova cada item confirmado.
Claude aguarda aprovação antes de gerar prompts de implementação.

### Passo 4 — Implementação

```
Implement in this exact order. Do NOT run pytest until all changes complete.
Reference: docs/project_specz.md — contratos invioláveis (§2.1).

── ITEM S01-NN ─────────────────────────
File: app/path/to/file.py
[código exato]

── APÓS TODOS OS ITENS ──────────────────
pytest tests/ -v --cov=app --cov-report=term-missing
ruff check app/

Target: NNN passed, 0 failed. Coverage ≥ 70% em engines/, services/, pipeline/.
Do NOT commit until approved.
```

### Passo 5 — Testes

Claude Code reporta resultado. Critérios:
- ✅ `NNN passed, 0 failed` + cobertura ≥ 70% → aprovado para commit
- ❌ Qualquer falha → identificar causa, corrigir, repetir
- ❌ Cobertura < 70% em camadas críticas → adicionar testes antes de commitar

**Nunca commitar com testes falhando.**

### Passo 6 — Commits Atômicos

Um commit por item do PRD. Nunca agrupar itens não relacionados.

```bash
# Nomenclatura obrigatória — Camisart AI
tipo(módulo): descrição curta em português [SNN-NN]

# Tipos válidos
feat      nova feature
fix       correção de bug
test      apenas testes
docs      documentação
refactor  sem mudança de comportamento
perf      melhoria de performance
devops    CI, deploy, infra
security  mudança de segurança
prompt    alteração em faq.json, campaigns.json, knowledge/

# Exemplos reais do projeto
feat(adapter): WhatsAppCloudAdapter com verificação HMAC [S01-04]
feat(engine): FAQEngine regex multipadrão com priority [S01-03]
feat(pipeline): MessagePipeline canal-agnóstica [S01-05]
test(engine): testes FAQEngine cobrindo 12 cenários [S01-07]
devops(ci): GitHub Actions pytest + coverage + ruff [S01-08]
```

### Passo 7 — CI Verde Obrigatório

Antes de abrir PR, confirmar que o GitHub Actions passou:

```yaml
# .github/workflows/ci.yml — executa em todo push e PR
name: CI

on: [push, pull_request]

jobs:
  test:
    runs-on: ubuntu-latest
    services:
      postgres:
        image: postgres:15
        env:
          POSTGRES_DB: camisart_test_db
          POSTGRES_PASSWORD: test
        ports: ["5432:5432"]
        options: >-
          --health-cmd pg_isready
          --health-interval 10s
          --health-timeout 5s
          --health-retries 5

    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - run: pip install -r requirements.txt
      - run: ruff check app/
      - run: pytest tests/ -v --cov=app --cov-report=term-missing
        env:
          DATABASE_URL: postgresql://postgres:test@localhost:5432/camisart_test_db
          TEST_DATABASE_URL: postgresql://postgres:test@localhost:5432/camisart_test_db
          APP_ENV: test
          WHATSAPP_VERIFY_TOKEN: test-token
          WHATSAPP_APP_SECRET: test-secret-32-chars-minimum-here
          ADMIN_TOKEN: test-admin-token-32-chars-minimum
```

**PR sem CI verde não é revisado. Sem exceções.**

### Passo 8 — Sprint Review e Merge

Executar `camisart-sprint-review` antes do merge.
Merge apenas com aprovação explícita do arquiteto.

---

## Branch Strategy

```
main                            ← produção — apenas merge via PR aprovado
└── sprint/01-foundation        ← uma branch por sprint
    ├── commits atômicos S01-01 → S01-NN
    └── PR → main após sprint review aprovado
```

**Nomenclatura de branches:** `sprint/NN-nome-curto`

```bash
# Iniciar sprint
git checkout main && git pull origin main
git checkout -b sprint/01-foundation

# Durante o sprint — commits atômicos
git add app/engines/regex_engine.py tests/test_regex_engine.py
git commit -m "feat(engine): FAQEngine com regex multipadrão [S01-03]"
git push origin sprint/01-foundation

# Abrir PR ao concluir todos os itens do PRD
# Título: "Sprint 01 — Foundation [S01-01..S01-09]"
# Body: resultado dos testes + checklist de aceite do PRD
```

**Regra:** nunca commitar diretamente em `main`. Sempre via PR da branch do sprint.

---

## Estrutura de PRD

Todo sprint começa com um PRD salvo em `docs/PRD_Sprint01_foundation.md`.

```markdown
# PRD — Sprint NN: Título
**Status:** Aprovação Pendente
**Branch:** sprint/NN-nome
**Origem:** feature / bug / refatoração

## Entregáveis do Sprint
| ID | Módulo | Descrição | Esforço estimado |
|---|---|---|---|

## SNN-01 — Nome do Item
### Motivação
[por que este item existe]

### Implementação
[código exato ou abordagem]

### Testes
[cenários a cobrir]

## Ordem de Execução
## Commits Atômicos Esperados
## Critérios de Aceite
```

---

## Migrações de Banco de Dados

**Regras inegociáveis:**

- Sempre idempotentes — pode rodar duas vezes sem erro
- Sempre com script de rollback em `app/migrations/rollback_sprint_NN.py`
- Rodar manualmente na VPS após deploy: `python app/migrations/migrate_sprint_NN.py`

```python
# Template — migration idempotente Camisart
def migrate():
    with engine.connect() as conn:
        exists = conn.execute(text("""
            SELECT column_name FROM information_schema.columns
            WHERE table_name = 'tabela' AND column_name = 'coluna'
        """)).fetchone()
        if not exists:
            conn.execute(text("ALTER TABLE tabela ADD COLUMN ..."))
            conn.commit()
            print("✅ Migração Sprint NN aplicada.")
        else:
            print("✅ Já estava atualizado.")
```

---

## Padrões de Teste — Específicos do Camisart

```python
# Banco de teste — sempre camisart_test_db
TEST_DATABASE_URL = "postgresql://user:pass@localhost:5432/camisart_test_db"

# Mock obrigatório para Meta Graph API (nunca chamar de verdade)
@pytest.fixture
def mock_whatsapp_client(monkeypatch):
    sent_messages = []
    async def fake_send(phone, payload):
        sent_messages.append({"phone": phone, "payload": payload})
        return "wamid.fake123"
    monkeypatch.setattr("app.adapters.whatsapp_cloud.client.send_message", fake_send)
    return sent_messages

# HMAC válido para testes do webhook
def make_hmac_header(payload: bytes, secret: str = "test-secret") -> str:
    import hmac, hashlib
    sig = hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()
    return f"sha256={sig}"

# Fixture de sessão de teste
@pytest.fixture
def test_session(db):
    from app.models.session import Session as SessionModel
    session = SessionModel(
        channel_id="whatsapp_cloud",
        channel_user_id="5591999990000",
        display_name="TEST_Cliente",
    )
    db.add(session)
    db.commit()
    db.refresh(session)
    yield session
    db.delete(session)
    db.commit()
```

**Cenários mínimos obrigatórios:**

| Componente | Cenários |
|---|---|
| `GET /adapters/whatsapp_cloud/webhook` | HMAC válido → 200 com challenge; token inválido → 403 |
| `POST /adapters/whatsapp_cloud/webhook` | Payload válido → 200; sem HMAC → 403; reenvio (dedup) → 200 sem reprocessar |
| `FAQEngine.match()` | Intent com match; intent sem match (None); pattern regex malformado (não quebra); priority correta |
| `MessagePipeline.process()` | Mensagem nova cria sessão; mensagem existente reutiliza sessão; timeout reset; fallback correto |
| `GET /health` | 200 com DB up |
| `POST /admin/campaigns/reload` | Token válido → 200; token inválido → 403 |

---

## Deploy na VPS Hostinger

```bash
# Após merge no main
ssh user@vps
cd /opt/camisart
git pull origin main
.venv/bin/pip install -r requirements.txt --quiet
.venv/bin/python app/migrations/migrate_sprint_NN.py
sudo systemctl restart camisart

# Verificar
curl https://camisart-bot.seu-dominio.com/health
journalctl -u camisart --since "2 minutes ago" | tail -20
```

**Checklist pós-deploy:**
- [ ] `GET /health` retorna 200 com `{"status": "ok", "db": "up"}`
- [ ] Webhook Meta responde ao handshake
- [ ] Migration executada sem erro
- [ ] Zero erros críticos no journal nos primeiros 2 minutos

---

## Severidade de Bugs

| 🔴 Crítico | App quebra, HMAC bypass, dados perdidos, chamada real à Meta em teste |
|---|---|
| 🟡 Médio | Intent não reconhecido, estado inconsistente, UX degradada |
| 🟢 Baixo | Cosmético, log desnecessário, melhoria de padrão |

Bugs 🔴 bloqueiam o merge. Bugs 🟡 e 🟢 podem ser backlog justificado.
