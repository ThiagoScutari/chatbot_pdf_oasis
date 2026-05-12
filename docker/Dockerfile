# syntax=docker/dockerfile:1.7
# ──────────────────────────────────────────────
#  CatalogFlow — Multi-stage build
# ──────────────────────────────────────────────
#  Stage `builder`: instala dependências em /opt/venv.
#  Stage `production`: copia o venv pronto + código. Runs as non-root.
# ──────────────────────────────────────────────

ARG PYTHON_VERSION=3.12

# ──────────────────────────────────────────────
#  Stage 1 — builder
# ──────────────────────────────────────────────
FROM python:${PYTHON_VERSION}-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_ROOT_USER_ACTION=ignore

WORKDIR /build

# Pacotes de sistema para compilar wheels (psycopg, pymupdf, etc.)
# e libmagic para python-magic em runtime.
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        gcc \
        libffi-dev \
        libmagic1 \
        libjpeg62-turbo-dev \
        zlib1g-dev \
        curl \
    && rm -rf /var/lib/apt/lists/*

# Criar venv isolado que será copiado integralmente para a imagem final.
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Copiar manifest primeiro para aproveitar cache de layers.
COPY pyproject.toml README.md ./
# Estrutura mínima do pacote para o setuptools.find resolver.
COPY src ./src

RUN pip install --upgrade pip setuptools wheel \
    && pip install --no-cache-dir .

# ──────────────────────────────────────────────
#  Stage 2 — production
# ──────────────────────────────────────────────
FROM python:${PYTHON_VERSION}-slim AS production

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH" \
    APP_HOME=/app

# libmagic1 é dependência runtime de python-magic (validação MIME server-side).
RUN apt-get update && apt-get install -y --no-install-recommends \
        libmagic1 \
        libjpeg62-turbo \
        curl \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --system catalogflow \
    && useradd --system --gid catalogflow --create-home --home-dir /home/catalogflow catalogflow

WORKDIR ${APP_HOME}

# Copia venv pronto do stage builder.
COPY --from=builder --chown=catalogflow:catalogflow /opt/venv /opt/venv

# Código da aplicação.
COPY --chown=catalogflow:catalogflow src ./src
COPY --chown=catalogflow:catalogflow migrations ./migrations
COPY --chown=catalogflow:catalogflow pyproject.toml README.md alembic.ini ./

# Reinstala em modo desenvolvimento para registrar entry points no venv.
RUN pip install --no-deps --no-cache-dir -e .

USER catalogflow

# Healthcheck consumido pelo orquestrador (Fly.io/compose) e por /api/v1/health.
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS http://localhost:8000/api/v1/health || exit 1

EXPOSE 8000

# Default command: API. O worker usa um command override no compose/deploy.
CMD ["uvicorn", "catalogflow.main:app", "--host", "0.0.0.0", "--port", "8000"]
