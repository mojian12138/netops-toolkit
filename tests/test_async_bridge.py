"""TaskRunner 单测：后台执行、结果回主线程、错误捕获与取消。"""

from __future__ import annotations

import asyncio
import threading
import time

import pytest
from PyQt6.QtCore import QCoreApplication, QEventLoop, QTimer

from netops.ui.async_bridge import CancelledError, TaskRunner


def _wait_for(handle, timeout_ms: int = 3000) -> str:
    """阻塞等待任务结束，返回 'finished' / 'failed' / 'cancelled'。"""
    loop = QEventLoop()
    outcome: dict[str, str] = {}

    def done(kind: str) -> None:
        outcome["kind"] = kind
        loop.quit()

    handle.finished.connect(lambda _v: done("finished"))
    handle.failed.connect(lambda _e: done("failed"))
    handle.cancelled.connect(lambda: done("cancelled"))
    QTimer.singleShot(timeout_ms, loop.quit)
    loop.exec()
    return outcome.get("kind", "timeout")


@pytest.fixture(scope="module")
def qapp() -> QCoreApplication:
    app = QCoreApplication.instance() or QCoreApplication([])
    return app


def test_runner_returns_result(qapp) -> None:
    runner = TaskRunner()
    handle = runner.submit(lambda: 6 * 7)
    received: list[object] = []
    handle.finished.connect(received.append)
    assert _wait_for(handle) == "finished"
    assert received == [42]
    runner.shutdown()


def test_runner_runs_off_ui_thread(qapp) -> None:
    runner = TaskRunner()
    main_id = threading.get_ident()
    handle = runner.submit(lambda: threading.get_ident())
    ids: list[int] = []
    handle.finished.connect(ids.append)
    assert _wait_for(handle) == "finished"
    assert ids[0] != main_id
    runner.shutdown()


def test_runner_captures_exception(qapp) -> None:
    runner = TaskRunner()

    def boom() -> None:
        raise ValueError("坏输入")

    handle = runner.submit(boom)
    errors: list[str] = []
    handle.failed.connect(errors.append)
    assert _wait_for(handle) == "failed"
    assert "坏输入" in errors[0]
    runner.shutdown()


def test_runner_cancellation_is_cooperative(qapp) -> None:
    runner = TaskRunner()

    def long_job(cancel_token) -> str:
        for _ in range(200):
            time.sleep(0.01)
            cancel_token.raise_if_cancelled()
        return "done"

    handle = runner.submit(long_job)
    handle.cancel()
    assert _wait_for(handle) == "cancelled"
    runner.shutdown()


def test_runner_cancel_after_completion_ignored(qapp) -> None:
    runner = TaskRunner()
    handle = runner.submit(lambda: "ok")
    assert _wait_for(handle) == "finished"
    handle.cancel()
    assert handle.is_cancelled
    runner.shutdown()


def test_runner_submit_async(qapp) -> None:
    runner = TaskRunner()

    async def compute(value: int) -> int:
        await asyncio.sleep(0)
        return value + 1

    handle = runner.submit_async(compute, 41)
    results: list[object] = []
    handle.finished.connect(results.append)
    assert _wait_for(handle) == "finished"
    assert results == [42]
    runner.shutdown()


def test_cancel_token_default_not_cancelled() -> None:
    runner = TaskRunner()

    def job(cancel_token) -> bool:
        return cancel_token.cancelled

    handle = runner.submit(job)
    values: list[object] = []
    handle.finished.connect(values.append)
    assert _wait_for(handle) == "finished"
    assert values == [False]
    runner.shutdown()


def test_cancelled_error_is_runtime_error() -> None:
    assert issubclass(CancelledError, RuntimeError)
