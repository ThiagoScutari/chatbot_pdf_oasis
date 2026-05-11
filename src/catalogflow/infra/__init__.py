"""Dependências externas do CatalogFlow.

Toda integração com PostgreSQL, Redis, S3/R2 e Celery vive aqui.
Lógica de domínio em `modules/` jamais importa diretamente bibliotecas externas
de infraestrutura — sempre via wrappers definidos neste pacote.
"""
