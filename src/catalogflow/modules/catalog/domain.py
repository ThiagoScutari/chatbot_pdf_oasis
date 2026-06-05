"""Tipos de domínio do módulo `catalog`.

Hospeda dataclasses POD que não pertencem ao ORM (`models.py`) nem à
engine de processamento (`pdf_analyzer.py`).

Nesta primeira versão (Sprint 08 Fase C, ADR-011):
- `AnalyzerWarning`: dataclass de observabilidade não-bloqueante
  (ADR-011 D1).
- Constantes de severidades e códigos padronizados (ADR-011 D3).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

# ──────────────────────────────────────────────
#  Severidades padronizadas (ADR-011 D3)
# ──────────────────────────────────────────────
#
# Severidade indica gravidade da degradação para o operador comercial;
# NENHUM código interrompe o pipeline (política de não-bloqueio, D2).
SEVERITY_INFO: Final = "info"
SEVERITY_WARNING: Final = "warning"
SEVERITY_ERROR: Final = "error"

# ──────────────────────────────────────────────
#  Códigos de warning padronizados (ADR-011 D3)
# ──────────────────────────────────────────────
GRADE_NOT_DETECTED: Final = "GRADE_NOT_DETECTED"
NAME_NOT_DETECTED: Final = "NAME_NOT_DETECTED"
PRICE_NOT_DETECTED: Final = "PRICE_NOT_DETECTED"
SWATCHES_NOT_DETECTED: Final = "SWATCHES_NOT_DETECTED"
FIELDS_NOT_INJECTED_NO_GRADE: Final = "FIELDS_NOT_INJECTED_NO_GRADE"


@dataclass(frozen=True, slots=True)
class AnalyzerWarning:
    """Degradação observável detectada durante o processamento do catálogo.

    Política de não-bloqueio (ADR-011 D2): o pipeline continua e o
    produto é persistido com campos opcionais quando aplicável.
    Severidade indica gravidade ao operador comercial; não interrompe
    processamento.

    Campos:
        code: identificador padronizado (ver constantes neste módulo).
        severity: "info" | "warning" | "error".
        page_index: página onde a degradação ocorreu (0-indexed).
        sku: SKU do produto afetado quando aplicável; `None` se for
             degradação de página inteira.
        message: mensagem humana em pt-BR.
        detected_value: valor parcial detectado (diagnóstico); `None`
                        quando nada foi detectado.
    """

    code: str
    severity: str
    page_index: int
    sku: str | None
    message: str
    detected_value: str | None
