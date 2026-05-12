"""Testes do wrapper Celery `romaneio.generate`.

Diferente de orders, não há erro permanente — todos transitórios para
retry.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import patch
from uuid import uuid4

from celery.exceptions import Retry

from catalogflow.modules.romaneio.tasks import generate_romaneio_task


class TestRomaneioGenerateTaskSuccess:
    def test_task_returns_pipeline_result(self) -> None:
        rid = str(uuid4())
        jid = str(uuid4())
        expected = {"romaneio_id": rid, "output_key": "k", "size_bytes": 1234}

        async def fake_run(*args: Any, **kwargs: Any) -> dict[str, Any]:
            return expected

        with patch(
            "catalogflow.modules.romaneio.tasks._run_process_romaneio",
            new=fake_run,
        ):
            result = generate_romaneio_task.apply(args=[rid, jid])
        assert result.successful()
        assert result.result == expected


class TestRomaneioGenerateTaskAllErrorsRetry:
    def test_any_exception_invokes_self_retry(self) -> None:
        rid = str(uuid4())
        jid = str(uuid4())

        async def boom(*args: Any, **kwargs: Any) -> dict[str, Any]:
            raise ConnectionError("storage blip")

        with patch(
            "catalogflow.modules.romaneio.tasks._run_process_romaneio",
            new=boom,
        ), patch.object(
            generate_romaneio_task,
            "retry",
            side_effect=Retry("retry-scheduled"),
        ) as mock_retry:
            generate_romaneio_task.apply(args=[rid, jid])
        assert mock_retry.called
        assert mock_retry.call_args.kwargs.get("countdown") == 60
