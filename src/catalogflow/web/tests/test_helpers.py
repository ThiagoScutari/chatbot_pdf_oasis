"""Testes para `web/_helpers.py` — funções puras de formatação pt-BR.

Cobre: format_date_long_pt, format_date_short_pt, humanize_when,
catalog_status_badge, order_status_badge.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta, timezone

from catalogflow.web._helpers import (
    catalog_status_badge,
    format_date_long_pt,
    format_date_short_pt,
    humanize_when,
    order_status_badge,
)


class TestFormatDateLongPt:
    def test_returns_weekday_and_month_in_pt(self) -> None:
        """Data completa em português, ex: 'Terça-feira, 12 de maio de 2026'."""
        d = date(2026, 5, 12)  # 12/05/2026 é uma terça-feira
        assert format_date_long_pt(d) == "Terça-feira, 12 de maio de 2026"

    def test_accepts_datetime_and_strips_time(self) -> None:
        """datetime é aceito; resultado é a mesma data formatada (sem hora)."""
        dt = datetime(2026, 5, 12, 14, 30, tzinfo=UTC)
        assert format_date_long_pt(dt) == "Terça-feira, 12 de maio de 2026"

    def test_all_weekdays_have_pt_label(self) -> None:
        """Os 7 dias da semana retornam labels em pt-BR (capitalize)."""
        # 04/05/2026 é segunda-feira; varremos os 7 dias seguintes.
        base = date(2026, 5, 4)
        labels = [format_date_long_pt(base + timedelta(days=i)).split(",")[0] for i in range(7)]
        expected_starts = {
            "Segunda-feira",
            "Terça-feira",
            "Quarta-feira",
            "Quinta-feira",
            "Sexta-feira",
            "Sábado",
            "Domingo",
        }
        assert set(labels) == expected_starts


class TestFormatDateShortPt:
    def test_zero_padded_day_and_month(self) -> None:
        """Formato dd/mm/yyyy com zero-padding."""
        assert format_date_short_pt(date(2026, 1, 5)) == "05/01/2026"

    def test_accepts_datetime(self) -> None:
        """datetime é aceito; o componente de hora é ignorado."""
        dt = datetime(2026, 12, 31, 23, 59)
        assert format_date_short_pt(dt) == "31/12/2026"


class TestHumanizeWhen:
    def test_today_returns_hoje_prefix(self) -> None:
        """Mesma data do `now` => prefixo 'hoje'."""
        now = datetime(2026, 5, 12, 15, 0)
        when = datetime(2026, 5, 12, 10, 45)
        assert humanize_when(when, now=now) == "hoje 10:45"

    def test_yesterday_returns_ontem_prefix(self) -> None:
        """Um dia antes do `now` => prefixo 'ontem'."""
        now = datetime(2026, 5, 12, 10, 0)
        when = datetime(2026, 5, 11, 18, 30)
        assert humanize_when(when, now=now) == "ontem 18:30"

    def test_older_falls_back_to_dd_mm(self) -> None:
        """Mais de 1 dia atrás => formato dd/mm hh:mm."""
        now = datetime(2026, 5, 12, 10, 0)
        when = datetime(2026, 5, 8, 14, 0)
        assert humanize_when(when, now=now) == "08/05 14:00"

    def test_defaults_now_to_current_when_omitted(self) -> None:
        """Sem `now` explícito usa datetime.now(tz=when.tzinfo); horário do dia preservado."""
        tz = timezone(timedelta(hours=-3))
        when = datetime.now(tz=tz)
        result = humanize_when(when)
        assert result.startswith("hoje ")
        assert result.endswith(f"{when.hour:02d}:{when.minute:02d}")


class TestCatalogStatusBadge:
    def test_known_statuses_return_label_and_variant(self) -> None:
        """Mapeamento dos 4 status conhecidos retorna (label_pt, css_variant)."""
        assert catalog_status_badge("pending") == ("Aguardando", "processing")
        assert catalog_status_badge("processing") == ("Processando", "processing")
        assert catalog_status_badge("ready") == ("Pronto", "ready")
        assert catalog_status_badge("error") == ("Erro", "error")

    def test_unknown_status_falls_back_to_raw_and_processing(self) -> None:
        """Status fora do mapa devolve o próprio status como label + 'processing'."""
        assert catalog_status_badge("foobar") == ("foobar", "processing")


class TestOrderStatusBadge:
    def test_known_statuses_return_label_and_variant(self) -> None:
        """Mapeamento dos 5 status conhecidos."""
        assert order_status_badge("draft") == ("Aguardando", "processing")
        assert order_status_badge("extracted") == ("Pronto", "ready")
        assert order_status_badge("confirmed") == ("Confirmado", "ready")
        assert order_status_badge("cancelled") == ("Cancelado", "error")
        assert order_status_badge("error") == ("Erro", "error")

    def test_unknown_status_falls_back_to_raw_and_processing(self) -> None:
        """Status desconhecido tem fallback consistente com catalog_status_badge."""
        assert order_status_badge("waiting_payment") == ("waiting_payment", "processing")
