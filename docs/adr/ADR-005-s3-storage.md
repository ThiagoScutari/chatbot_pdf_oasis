# ADR-005: S3-compatible storage para arquivos PDF

**Status:** Vigente
**Data:** 2026-05-11

## Contexto

PDFs de catálogo chegam a 30–50 MB. Armazenar em banco é antipadrão (bloat,
backups lentos, leitura em streaming difícil).

## Decisão

Storage S3-compatível para todos os uploads e outputs. Banco armazena apenas
metadados e referência ao objeto (chave S3).

- **Dev / produção atual:** MinIO self-hosted (S3 API compatível).
- **Planejado para escala:** Cloudflare R2 (sem egress fees).

## Consequências

- O banco fica leve (queries continuam rápidas mesmo com volume).
- Presigned URLs para downloads — autorização desacoplada do storage.
- Backup do storage é separado do backup do banco.
