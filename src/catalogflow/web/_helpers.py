"""Helpers de formatação para templates web.

Mantém os templates limpos: nada de cadeia gigante de filtros Jinja.
"""

from __future__ import annotations

from datetime import date, datetime

_DAYS_PT = {
    0: "segunda-feira",
    1: "terça-feira",
    2: "quarta-feira",
    3: "quinta-feira",
    4: "sexta-feira",
    5: "sábado",
    6: "domingo",
}

_MONTHS_PT = {
    1: "janeiro",
    2: "fevereiro",
    3: "março",
    4: "abril",
    5: "maio",
    6: "junho",
    7: "julho",
    8: "agosto",
    9: "setembro",
    10: "outubro",
    11: "novembro",
    12: "dezembro",
}


def format_date_long_pt(d: date | datetime) -> str:
    """`Terça-feira, 12 de maio de 2026`."""
    if isinstance(d, datetime):
        d = d.date()
    weekday = _DAYS_PT[d.weekday()].capitalize()
    return f"{weekday}, {d.day} de {_MONTHS_PT[d.month]} de {d.year}"


def format_date_short_pt(d: date | datetime) -> str:
    """`12/05/2026`."""
    if isinstance(d, datetime):
        d = d.date()
    return f"{d.day:02d}/{d.month:02d}/{d.year}"


def humanize_when(when: datetime, *, now: datetime | None = None) -> str:
    """Texto leve estilo `hoje 10:45`, `ontem 18:30`, ou `12/05 14:00`."""
    if now is None:
        now = datetime.now(tz=when.tzinfo)
    delta_days = (now.date() - when.date()).days
    time_part = f"{when.hour:02d}:{when.minute:02d}"
    if delta_days == 0:
        return f"hoje {time_part}"
    if delta_days == 1:
        return f"ontem {time_part}"
    return f"{when.day:02d}/{when.month:02d} {time_part}"


# ──────────────────────────────────────────────
#  Mapeamento de status → classe CSS / label
# ──────────────────────────────────────────────

_CATALOG_STATUS_LABELS = {
    "pending":    ("Aguardando",   "processing"),
    "processing": ("Processando",  "processing"),
    "ready":      ("Pronto",       "ready"),
    "error":      ("Erro",         "error"),
}

_ORDER_STATUS_LABELS = {
    "draft":     ("Aguardando",  "processing"),
    "extracted": ("Pronto",      "ready"),
    "confirmed": ("Confirmado",  "ready"),
    "cancelled": ("Cancelado",   "error"),
    "error":     ("Erro",        "error"),
}


def catalog_status_badge(status: str) -> tuple[str, str]:
    """Retorna `(label_pt, css_variant)` para um status de catálogo."""
    return _CATALOG_STATUS_LABELS.get(status, (status, "processing"))


def order_status_badge(status: str) -> tuple[str, str]:
    """Retorna `(label_pt, css_variant)` para um status de pedido."""
    return _ORDER_STATUS_LABELS.get(status, (status, "processing"))
