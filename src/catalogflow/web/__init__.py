"""Módulo `web` — UI Jinja2 + HTMX servida pelo próprio FastAPI.

Este módulo é estritamente uma camada de apresentação. Toda lógica de
negócio continua nos módulos de domínio (`catalog`, `orders`, `romaneio`,
`auth`); aqui apenas renderizamos templates e fazemos chamadas internas à
API REST para popular dados.

Convenções:
- Rotas de página vivem em `web.router` e não têm prefixo `/api/v1/`.
- Autenticação por sessão (API Key assinada em cookie) em `web.auth`.
- Templates em `src/catalogflow/templates/` (raiz, fora deste pacote).
- Assets estáticos em `src/catalogflow/static/`.
"""
