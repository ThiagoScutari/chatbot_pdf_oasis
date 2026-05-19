"""Testes das Celery tasks de `stock` — `stock.check` e `stock.submit`.

Espelha o padrão de `catalog/tests/test_tasks.py`: patch do runner async
(`_run_check`, `_run_submit`), `task.apply(...)` em eager mode, e
classificação de erros (permanentes não chamam retry; transientes sim).
"""

from __future__ import annotations

from typing import Any, ClassVar
from unittest.mock import patch
from uuid import UUID, uuid4

import pytest
from celery.exceptions import Retry
from sqlalchemy.ext.asyncio import AsyncSession

from catalogflow.modules.stock.tasks import (
    _run_check,
    _run_submit,
    check_stock_task,
    submit_order_task,
)

# ──────────────────────────────────────────────
#  check_stock_task — entry point Celery
# ──────────────────────────────────────────────


class TestCheckStockTaskSuccess:
    def test_task_returns_runner_result(self) -> None:
        """Pipeline OK → task devolve o dict do `_run_check`."""
        oid = str(uuid4())
        scid = str(uuid4())
        jid = str(uuid4())
        expected = {"order_id": oid, "available": True}

        async def fake_runner(*args: Any, **kwargs: Any) -> dict[str, Any]:
            return expected

        with patch(
            "catalogflow.modules.stock.tasks._run_check",
            new=fake_runner,
        ):
            result = check_stock_task.apply(args=[oid, scid, jid])
        assert result.successful()
        assert result.result == expected


class TestCheckStockTaskPermanentErrors:
    def test_not_implemented_error_is_reraised_without_retry(self) -> None:
        """`NotImplementedError` é classificado como permanente — sobe direto."""
        oid = str(uuid4())
        scid = str(uuid4())
        jid = str(uuid4())

        async def boom(*args: Any, **kwargs: Any) -> dict[str, Any]:
            raise NotImplementedError("adapter não suportado")

        with (
            patch(
                "catalogflow.modules.stock.tasks._run_check",
                new=boom,
            ),
            patch.object(
                check_stock_task,
                "retry",
                side_effect=Retry("não-deveria-rodar"),
            ) as mock_retry,
        ):
            result = check_stock_task.apply(args=[oid, scid, jid])
        # Erro permanente: a task falha sem chamar self.retry.
        assert result.failed()
        assert isinstance(result.result, NotImplementedError)
        assert not mock_retry.called


class TestCheckStockTaskTransientErrors:
    def test_unknown_exception_invokes_self_retry_with_backoff(self) -> None:
        """Erros transitórios chamam `self.retry(...)` com `countdown=60`."""
        oid = str(uuid4())
        scid = str(uuid4())
        jid = str(uuid4())

        async def boom(*args: Any, **kwargs: Any) -> dict[str, Any]:
            raise RuntimeError("rede instável")

        with (
            patch(
                "catalogflow.modules.stock.tasks._run_check",
                new=boom,
            ),
            patch.object(
                check_stock_task,
                "retry",
                side_effect=Retry("retry-scheduled"),
            ) as mock_retry,
        ):
            check_stock_task.apply(args=[oid, scid, jid])
        assert mock_retry.called
        # Backoff exponencial: 60 * 2^0 na 1ª tentativa.
        assert mock_retry.call_args.kwargs.get("countdown") == 60


# ──────────────────────────────────────────────
#  submit_order_task — entry point Celery
# ──────────────────────────────────────────────


class TestSubmitOrderTaskSuccess:
    def test_task_returns_runner_result(self) -> None:
        """Pipeline OK → task devolve o dict do `_run_submit`."""
        oid = str(uuid4())
        jid = str(uuid4())
        expected = {"order_id": oid, "erp_id": "12345"}

        async def fake_runner(*args: Any, **kwargs: Any) -> dict[str, Any]:
            return expected

        with patch(
            "catalogflow.modules.stock.tasks._run_submit",
            new=fake_runner,
        ):
            result = submit_order_task.apply(args=[oid, "CUST-001", jid])
        assert result.successful()
        assert result.result == expected


class TestSubmitOrderTaskPermanentErrors:
    def test_not_implemented_error_is_reraised_without_retry(self) -> None:
        """ConsistemAdapter.submit_order ainda não tem contrato — `NotImplementedError` é permanente."""
        oid = str(uuid4())
        jid = str(uuid4())

        async def boom(*args: Any, **kwargs: Any) -> dict[str, Any]:
            raise NotImplementedError("submit não implementado")

        with (
            patch(
                "catalogflow.modules.stock.tasks._run_submit",
                new=boom,
            ),
            patch.object(
                submit_order_task,
                "retry",
                side_effect=Retry("não-deveria-rodar"),
            ) as mock_retry,
        ):
            result = submit_order_task.apply(args=[oid, "CUST-001", jid])
        assert result.failed()
        assert isinstance(result.result, NotImplementedError)
        assert not mock_retry.called


class TestSubmitOrderTaskTransientErrors:
    def test_unknown_exception_invokes_self_retry_with_backoff(self) -> None:
        """Erro genérico → `self.retry(countdown=60)` na 1ª tentativa."""
        oid = str(uuid4())
        jid = str(uuid4())

        async def boom(*args: Any, **kwargs: Any) -> dict[str, Any]:
            raise RuntimeError("ERP fora do ar")

        with (
            patch(
                "catalogflow.modules.stock.tasks._run_submit",
                new=boom,
            ),
            patch.object(
                submit_order_task,
                "retry",
                side_effect=Retry("retry-scheduled"),
            ) as mock_retry,
        ):
            submit_order_task.apply(args=[oid, "CUST-001", jid])
        assert mock_retry.called
        assert mock_retry.call_args.kwargs.get("countdown") == 60


# ──────────────────────────────────────────────
#  Runners async — `_run_check` e `_run_submit`
# ──────────────────────────────────────────────


class _FakeStockService:
    """Stub do `StockService` usado pelos runners.

    Os runners constroem o service e chamam um método assíncrono que
    deve devolver um dict; o stub registra a chamada e devolve o
    `result` previamente configurado (ou levanta `raise_exc` se setado).
    """

    last_args: ClassVar[dict[str, Any]] = {}

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def check_order_stock(
        self, *, order_id: UUID, stock_check_id: UUID, job_id: UUID
    ) -> dict[str, Any]:
        type(self).last_args = {
            "order_id": order_id,
            "stock_check_id": stock_check_id,
            "job_id": job_id,
        }
        if getattr(type(self), "raise_exc", None) is not None:
            raise type(self).raise_exc  # type: ignore[attr-defined]
        return {"order_id": str(order_id), "ok": True}

    async def submit_order_to_erp(
        self, *, order_id: UUID, customer_code: str, job_id: UUID
    ) -> dict[str, Any]:
        type(self).last_args = {
            "order_id": order_id,
            "customer_code": customer_code,
            "job_id": job_id,
        }
        if getattr(type(self), "raise_exc", None) is not None:
            raise type(self).raise_exc  # type: ignore[attr-defined]
        return {"order_id": str(order_id), "submitted": True}


@pytest.mark.asyncio
class TestRunCheckRunner:
    async def test_runs_service_and_returns_result(self) -> None:
        """`_run_check` instancia StockService e devolve o dict do método."""
        _FakeStockService.raise_exc = None  # type: ignore[attr-defined]
        oid, scid, jid = uuid4(), uuid4(), uuid4()
        with patch("catalogflow.modules.stock.tasks.StockService", _FakeStockService):
            result = await _run_check(oid, scid, jid)
        assert result == {"order_id": str(oid), "ok": True}
        assert _FakeStockService.last_args["order_id"] == oid
        assert _FakeStockService.last_args["stock_check_id"] == scid

    async def test_propagates_exception_after_commit(self) -> None:
        """Exceção do service propaga; commit foi chamado mesmo assim."""
        _FakeStockService.raise_exc = RuntimeError("queimou")  # type: ignore[attr-defined]
        oid, scid, jid = uuid4(), uuid4(), uuid4()
        with patch("catalogflow.modules.stock.tasks.StockService", _FakeStockService):
            with pytest.raises(RuntimeError, match="queimou"):
                await _run_check(oid, scid, jid)
        _FakeStockService.raise_exc = None  # type: ignore[attr-defined]


@pytest.mark.asyncio
class TestRunSubmitRunner:
    async def test_runs_service_and_returns_result(self) -> None:
        """`_run_submit` passa `customer_code` adiante e devolve dict."""
        _FakeStockService.raise_exc = None  # type: ignore[attr-defined]
        oid, jid = uuid4(), uuid4()
        with patch("catalogflow.modules.stock.tasks.StockService", _FakeStockService):
            result = await _run_submit(oid, "CUST-XYZ", jid)
        assert result == {"order_id": str(oid), "submitted": True}
        assert _FakeStockService.last_args["customer_code"] == "CUST-XYZ"

    async def test_propagates_exception_after_commit(self) -> None:
        """Exceção do service propaga; o `finally` ainda chama dispose_engine."""
        _FakeStockService.raise_exc = NotImplementedError("ainda não")  # type: ignore[attr-defined]
        oid, jid = uuid4(), uuid4()
        with patch("catalogflow.modules.stock.tasks.StockService", _FakeStockService):
            with pytest.raises(NotImplementedError):
                await _run_submit(oid, "CUST-XYZ", jid)
        _FakeStockService.raise_exc = None  # type: ignore[attr-defined]
