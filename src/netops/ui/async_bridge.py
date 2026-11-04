"""后台任务与 Qt 主线程之间的唯一通道。

业务页面禁止直接 ``threading.Thread`` 或自己 ``asyncio.run``：
统一通过 ``TaskRunner`` 提交，结果经 Qt 信号回到主线程，UI 永不阻塞。

用法::

    runner = TaskRunner(self)
    handle = runner.submit(blocking_job, "1.1.1.1")
    handle.finished.connect(self._on_done)
    handle.failed.connect(self._on_error)
    handle.progress.connect(self._on_progress)
    handle.cancel()   # 协作式取消，job 需定期检查 handle.cancelled
"""

from __future__ import annotations

import asyncio
import inspect
import logging
import threading
import traceback
from collections.abc import Callable
from typing import Any

from PyQt6.QtCore import QObject, QRunnable, QThreadPool, pyqtSignal

__all__ = ["CancellationToken", "CancelledError", "TaskHandle", "TaskRunner"]

_log = logging.getLogger(__name__)


def _accepted_kwargs(fn: Callable[..., Any]) -> frozenset[str]:
    """检查函数是否接受 ``cancel_token``；不接受时不强行注入。"""
    try:
        parameters = inspect.signature(fn).parameters.values()
    except (TypeError, ValueError):  # 内建函数等拿不到签名
        return frozenset()
    names: set[str] = set()
    for parameter in parameters:
        if parameter.kind is inspect.Parameter.VAR_KEYWORD:
            return frozenset({"cancel_token"})
        names.add(parameter.name)
    return frozenset(names)


class CancellationToken:
    """协作式取消令牌：任务内部定期调用 ``raise_if_cancelled()``。"""

    __slots__ = ("_event",)

    def __init__(self) -> None:
        self._event = threading.Event()

    @property
    def cancelled(self) -> bool:
        return self._event.is_set()

    def cancel(self) -> None:
        self._event.set()

    def raise_if_cancelled(self) -> None:
        if self._event.is_set():
            raise CancelledError("任务已取消")


class CancelledError(RuntimeError):
    """任务被用户主动取消。"""


class TaskHandle(QObject):
    """一次后台任务的句柄：只暴露信号与取消方法，不泄漏线程细节。"""

    finished = pyqtSignal(object)
    failed = pyqtSignal(str)
    progress = pyqtSignal(object)
    cancelled = pyqtSignal()

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.token = CancellationToken()

    def cancel(self) -> None:
        self.token.cancel()

    @property
    def is_cancelled(self) -> bool:
        return self.token.cancelled

    def raise_if_cancelled(self) -> None:
        self.token.raise_if_cancelled()


class _Signals(QObject):
    """QRunnable 不是 QObject，信号单独挂在这里。"""

    result = pyqtSignal(object)
    error = pyqtSignal(str)
    progress = pyqtSignal(object)
    was_cancelled = pyqtSignal()


class _Job(QRunnable):
    def __init__(
        self, fn: Callable[..., Any], args: tuple[Any, ...], kwargs: dict[str, Any]
    ) -> None:
        super().__init__()
        self._fn = fn
        self._args = args
        self._kwargs = kwargs
        self.signals = _Signals()
        self.token = CancellationToken()
        self.setAutoDelete(True)

    def run(self) -> None:  # pragma: no cover - 由线程池调用
        try:
            value = self._invoke()
        except CancelledError:
            self.signals.was_cancelled.emit()
        except Exception as exc:  # noqa: BLE001 - 后台任务边界，必须兜住
            _log.debug("后台任务异常\n%s", traceback.format_exc())
            self.signals.error.emit(f"{type(exc).__name__}: {exc}")
        else:
            if self.token.cancelled:
                self.signals.was_cancelled.emit()
            else:
                self.signals.result.emit(value)

    def _invoke(self) -> Any:
        """只有任务显式声明 ``cancel_token`` 参数时才注入令牌。"""
        if "cancel_token" in _accepted_kwargs(self._fn):
            self._kwargs.setdefault("cancel_token", self.token)
        return self._fn(*self._args, **self._kwargs)


class _AsyncJob(QRunnable):
    """协程任务：在工作者线程里跑一个独立事件循环。"""

    def __init__(
        self, factory: Callable[..., Any], args: tuple[Any, ...], kwargs: dict[str, Any]
    ) -> None:
        super().__init__()
        self._factory = factory
        self._args = args
        self._kwargs = kwargs
        self.signals = _Signals()
        self.token = CancellationToken()
        self.setAutoDelete(True)

    def run(self) -> None:  # pragma: no cover - 由线程池调用
        try:
            coroutine = self._factory(*self._args, **self._kwargs)
            value = asyncio.run(coroutine)
        except CancelledError:
            self.signals.was_cancelled.emit()
        except Exception as exc:  # noqa: BLE001
            _log.debug("异步任务异常\n%s", traceback.format_exc())
            self.signals.error.emit(f"{type(exc).__name__}: {exc}")
        else:
            if self.token.cancelled:
                self.signals.was_cancelled.emit()
            else:
                self.signals.result.emit(value)


class TaskRunner(QObject):
    """页面级任务调度器：一个页面一个实例，随页面销毁。

    ``max_threads`` 即并发上限；页面关闭时调用 ``shutdown()`` 取消并等待。
    """

    def __init__(self, parent: QObject | None = None, *, max_threads: int = 8) -> None:
        super().__init__(parent)
        self._pool = QThreadPool(self)
        self._pool.setMaxThreadCount(max(1, max_threads))
        self._handles: list[TaskHandle] = []

    @property
    def active_count(self) -> int:
        return self._pool.activeThreadCount()

    def submit(self, fn: Callable[..., Any], *args: Any, **kwargs: Any) -> TaskHandle:
        """把阻塞函数丢到线程池；函数可接收 ``cancel_token`` 关键字参数。"""
        handle = TaskHandle(self)
        job = _Job(fn, args, kwargs)
        job.token = handle.token
        self._wire(job.signals, handle, job)
        self._pool.start(job)
        return handle

    def submit_async(self, factory: Callable[..., Any], *args: Any, **kwargs: Any) -> TaskHandle:
        """把返回协程的可调用对象丢到线程池执行。"""
        handle = TaskHandle(self)
        job = _AsyncJob(factory, args, kwargs)
        job.token = handle.token
        self._wire(job.signals, handle, job)
        self._pool.start(job)
        return handle

    def _wire(self, signals: _Signals, handle: TaskHandle, job: QRunnable) -> None:
        signals.result.connect(handle.finished.emit)
        signals.error.connect(handle.failed.emit)
        signals.was_cancelled.connect(handle.cancelled.emit)

        # 用普通闭包而不是 ``lambda _v, j=job: ...``：PyQt 会按默认参数个数
        # 推断槽签名，带默认值的 lambda 在 0 参数的 was_cancelled 上会解析失败。
        def forget(*_args: Any) -> None:
            self._forget(handle)

        signals.result.connect(forget)
        signals.error.connect(forget)
        signals.was_cancelled.connect(forget)
        self._handles.append(handle)

    def _forget(self, handle: TaskHandle) -> None:
        if handle in self._handles:
            self._handles.remove(handle)

    def cancel_all(self) -> None:
        for handle in list(self._handles):
            handle.cancel()

    def wait_for_idle(self, timeout_ms: int = 5000) -> bool:
        """测试辅助：等待所有任务结束。真实 UI 不要调用。"""
        return self._pool.waitForDone(timeout_ms)

    def shutdown(self) -> None:
        self.cancel_all()
        self._pool.clear()
        self._pool.waitForDone(3000)
