"""Testes do wrapper Celery `order.extract`.

Foco: PDFFlattenedError NÃO dispara retry (Armadilha #3 do PRD); outros
erros transitórios disparam retry com backoff.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import patch
from uuid import uuid4

from celery.exceptions import Retry

from catalogflow.modules.orders.tasks import extract_order_task
from catalogflow.shared.errors import PDFCorruptError, PDFFlattenedError


class TestOrderExtractTaskSuccess:
    def test_task_returns_pipeline_result(self) -> None:
        oid = str(uuid4())
        jid = str(uuid4())
        expected = {"order_id": oid, "total_pecas": 5, "n_skus": 1}

        async def fake_run(*args: Any, **kwargs: Any) -> dict[str, Any]:
            return expected

        with patch(
            "catalogflow.modules.orders.tasks._run_process_order",
            new=fake_run,
        ):
            result = extract_order_task.apply(args=[oid, jid])
        assert result.successful()
        assert result.result == expected


class TestOrderExtractTaskPermanentErrors:
    def test_pdf_flattened_does_not_retry(self) -> None:
        """O comportamento-chave da Sprint 02 — PDF achatado nunca-retryable."""
        oid = str(uuid4())
        jid = str(uuid4())

        async def boom(*args: Any, **kwargs: Any) -> dict[str, Any]:
            raise PDFFlattenedError("flatten", code="PDF_FLATTENED")

        with patch(
            "catalogflow.modules.orders.tasks._run_process_order",
            new=boom,
        ):
            result = extract_order_task.apply(args=[oid, jid])
        assert result.failed()
        # NÃO é Retry — é PDFFlattenedError direto.
        assert isinstance(result.result, PDFFlattenedError)
        assert not isinstance(result.result, Retry)

    def test_pdf_corrupt_does_not_retry(self) -> None:
        oid = str(uuid4())
        jid = str(uuid4())

        async def boom(*args: Any, **kwargs: Any) -> dict[str, Any]:
            raise PDFCorruptError("corrupt", code="PDF_CORRUPT")

        with patch(
            "catalogflow.modules.orders.tasks._run_process_order",
            new=boom,
        ):
            result = extract_order_task.apply(args=[oid, jid])
        assert result.failed()
        assert isinstance(result.result, PDFCorruptError)


class TestOrderExtractTaskTransientErrors:
    def test_unknown_exception_invokes_self_retry(self) -> None:
        """Erros transitórios chamam `self.retry(...)` com backoff exponencial."""
        oid = str(uuid4())
        jid = str(uuid4())

        async def boom(*args: Any, **kwargs: Any) -> dict[str, Any]:
            raise ConnectionError("network blip")

        with (
            patch(
                "catalogflow.modules.orders.tasks._run_process_order",
                new=boom,
            ),
            patch.object(
                extract_order_task,
                "retry",
                side_effect=Retry("retry-scheduled"),
            ) as mock_retry,
        ):
            extract_order_task.apply(args=[oid, jid])
        assert mock_retry.called
        assert mock_retry.call_args.kwargs.get("countdown") == 60
