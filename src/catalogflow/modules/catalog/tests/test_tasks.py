"""Testes da Celery task `catalog.process` — cobre o wrapper de tasks.py.

Foco: classificação de erros (permanentes não fazem retry, transientes
sim) e propagação correta para o backend de retry do Celery. Os
caminhos de pipeline real são cobertos em `tests/integration/`.

Resolução da dívida de cobertura apontada no PRD Sprint 02 (Armadilha #5).
"""

from __future__ import annotations

from typing import Any
from unittest.mock import patch
from uuid import uuid4

import pytest
from celery.exceptions import Retry

from catalogflow.modules.catalog.tasks import process_catalog_task
from catalogflow.shared.errors import PDFCorruptError, PDFEncryptedError


# ──────────────────────────────────────────────
#  Sucesso — retorna dict do _run_process_catalog
# ──────────────────────────────────────────────


class TestCatalogProcessSuccess:
    def test_task_returns_result_from_pipeline(self) -> None:
        cid = str(uuid4())
        jid = str(uuid4())
        expected_result = {"catalog_id": cid, "n_skus": 1, "n_fields": 4}

        async def fake_run(*args: Any, **kwargs: Any) -> dict[str, Any]:
            return expected_result

        with patch(
            "catalogflow.modules.catalog.tasks._run_process_catalog",
            new=fake_run,
        ):
            result = process_catalog_task.apply(args=[cid, jid])
        assert result.successful()
        assert result.result == expected_result


# ──────────────────────────────────────────────
#  Erros permanentes — sem retry
# ──────────────────────────────────────────────


class TestCatalogProcessPermanentErrors:
    @pytest.mark.parametrize(
        "exc",
        [
            PDFCorruptError("corrompido", code="PDF_CORRUPT"),
            PDFEncryptedError("encrypted", code="PDF_ENCRYPTED"),
        ],
    )
    def test_permanent_error_is_reraised_without_retry(
        self,
        exc: Exception,
    ) -> None:
        cid = str(uuid4())
        jid = str(uuid4())

        async def boom(*args: Any, **kwargs: Any) -> dict[str, Any]:
            raise exc

        with patch(
            "catalogflow.modules.catalog.tasks._run_process_catalog",
            new=boom,
        ):
            result = process_catalog_task.apply(args=[cid, jid])
        # Erro permanente sobe — não vira Retry.
        assert result.failed()
        assert isinstance(result.result, type(exc))


# ──────────────────────────────────────────────
#  Erros transientes — retry com backoff
# ──────────────────────────────────────────────


class TestCatalogProcessTransientErrors:
    def test_unknown_exception_invokes_self_retry(self) -> None:
        """Erros transitórios chamam `self.retry(...)`.

        Em eager mode (`task.apply()`) sem broker disponível, o retry pode
        re-levantar o erro original — o que importa é que `self.retry` foi
        invocado. Verificamos via patch.
        """
        cid = str(uuid4())
        jid = str(uuid4())

        async def boom(*args: Any, **kwargs: Any) -> dict[str, Any]:
            raise RuntimeError("transient blip")

        with patch(
            "catalogflow.modules.catalog.tasks._run_process_catalog",
            new=boom,
        ), patch.object(
            process_catalog_task,
            "retry",
            side_effect=Retry("retry-scheduled"),
        ) as mock_retry:
            process_catalog_task.apply(args=[cid, jid])
        assert mock_retry.called, "self.retry() deveria ter sido invocado"
        # countdown segue 60 * 2^0 na primeira tentativa.
        assert mock_retry.call_args.kwargs.get("countdown") == 60
