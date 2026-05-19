# ADR-004: PyMuPDF (AGPL) — repositório público como conformidade

**Status:** Vigente
**Data:** 2026-05-11

## Contexto

PyMuPDF é tecnicamente superior para manipulação de AcroForm. Licença AGPL
exige divulgação do código-fonte quando o software é distribuído como
serviço web.

## Decisão

Usar PyMuPDF sem restrições. O repositório do CatalogFlow **é e permanecerá
público** no GitHub (<https://github.com/ThiagoScutari/chatbot_pdf_oasis>),
o que cumpre integralmente a exigência de divulgação da AGPL. **Nenhuma
licença comercial Artifex é necessária enquanto o repositório permanecer
público.**

## Consequência

PyPDFForm deixa de ser fallback necessário por questão de licença. Pode ser
avaliado futuramente apenas por razões técnicas, não legais.

## Alerta para o futuro

Se o repositório for tornado privado (ex.: versão white-label para cliente
enterprise), a questão de licença volta à tona. Nesse cenário, avaliar
licença comercial Artifex ou migração para PyPDFForm antes de fechar o
repositório. A migração seria cirúrgica — apenas `pdf_analyzer.py` e
`field_injector.py`, cujas interfaces são estáveis (bytes in → bytes/dataclass
out).
