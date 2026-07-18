"""Small reusable Qt thread boundary for provider operations."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

BackgroundOperation = Callable[[], object]
SuccessCallback = Callable[[object], None]
FailureCallback = Callable[[BaseException], None]


class QtBackgroundOperation:
    """Retain one QThread, worker, and GUI-thread bridge until completion."""

    def __init__(self, thread: Any, worker: Any, bridge: Any) -> None:
        self._thread = thread
        self._worker = worker
        self._bridge = bridge
        self._running = True

    @property
    def is_running(self) -> bool:
        return self._running

    def _mark_finished(self) -> None:
        self._running = False
        self._worker = None
        self._bridge = None
        self._thread = None


def start_qt_background_operation(
    qt_core: Any,
    parent: Any,
    operation: BackgroundOperation,
    on_success: SuccessCallback,
    on_failure: FailureCallback,
) -> QtBackgroundOperation:
    """Run ``operation`` on a QThread and marshal callbacks to the GUI thread."""

    if not callable(operation) or not callable(on_success) or not callable(on_failure):
        raise TypeError("background operation and callbacks must be callable")
    signal_type = getattr(qt_core, "Signal", None)
    if not callable(signal_type):
        raise RuntimeError("This Qt runtime does not provide Signal.")
    slot_type = getattr(qt_core, "Slot", None)
    if not callable(slot_type):
        slot_type = lambda *_args: lambda function: function

    class _Worker(qt_core.QObject):
        succeeded = signal_type(object)
        failed = signal_type(object)

        @slot_type()
        def run(self) -> None:
            try:
                result = operation()
            except Exception as error:
                self.failed.emit(error)
                return
            self.succeeded.emit(result)

    thread = qt_core.QThread(parent)
    worker = _Worker()

    class _Bridge(qt_core.QObject):
        @slot_type(object)
        def succeeded(self, result: object) -> None:
            try:
                on_success(result)
            finally:
                thread.quit()

        @slot_type(object)
        def failed(self, error: BaseException) -> None:
            try:
                on_failure(error)
            finally:
                thread.quit()

    bridge = _Bridge(parent)
    handle = QtBackgroundOperation(thread, worker, bridge)
    worker.moveToThread(thread)
    thread.started.connect(worker.run)
    worker.succeeded.connect(bridge.succeeded)
    worker.failed.connect(bridge.failed)
    thread.finished.connect(worker.deleteLater)
    thread.finished.connect(handle._mark_finished)
    thread.start()
    return handle


__all__ = ["QtBackgroundOperation", "start_qt_background_operation"]
