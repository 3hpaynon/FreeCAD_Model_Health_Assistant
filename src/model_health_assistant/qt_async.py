"""Small reusable Qt thread boundary for provider operations."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

BackgroundOperation = Callable[[], object]
SuccessCallback = Callable[[object], None]
FailureCallback = Callable[[BaseException], None]


class QtBackgroundOperation:
    """Retain one QThread operation and gate its user-facing callbacks.

    Cancellation is deliberately logical.  It never asks Qt to terminate the
    worker thread; it only prevents a result from reaching user-interface
    callbacks while the operation is allowed to finish and clean up normally.
    Detachment provides the same callback guard for an interface that is being
    closed without describing the operation itself as cancelled.
    """

    def __init__(self, thread: Any, worker: Any, bridge: Any) -> None:
        self._thread = thread
        self._worker = worker
        self._bridge = bridge
        self._running = True
        self._cancelled = False
        self._detached = False
        self._callback_delivered = False

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def is_cancelled(self) -> bool:
        return self._cancelled

    @property
    def is_detached(self) -> bool:
        return self._detached

    def cancel(self) -> bool:
        """Suppress a pending callback without terminating the worker thread."""

        if self._cancelled or self._callback_delivered:
            return False
        self._cancelled = True
        return True

    def detach(self) -> bool:
        """Suppress a pending callback because its interface is no longer valid."""

        if self._detached or self._callback_delivered:
            return False
        self._detached = True
        return True

    def _claim_callback(self) -> bool:
        """Return whether one completion callback may still be delivered."""

        if self._callback_delivered or self._cancelled or self._detached:
            return False
        self._callback_delivered = True
        return True

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
        completed = signal_type()

        @slot_type()
        def run(self) -> None:
            try:
                result = operation()
            except Exception as error:
                self.failed.emit(error)
            else:
                self.succeeded.emit(result)
            finally:
                # Completion always stops the event loop normally.  Logical
                # cancellation and detachment never terminate the thread.
                self.completed.emit()

    thread = qt_core.QThread(parent)
    worker = _Worker()

    class _Bridge(qt_core.QObject):
        @slot_type(object)
        def succeeded(self, result: object) -> None:
            if handle._claim_callback():
                on_success(result)

        @slot_type(object)
        def failed(self, error: BaseException) -> None:
            if handle._claim_callback():
                on_failure(error)

    bridge = _Bridge(parent)
    handle = QtBackgroundOperation(thread, worker, bridge)
    worker.moveToThread(thread)
    thread.started.connect(worker.run)
    worker.succeeded.connect(bridge.succeeded)
    worker.failed.connect(bridge.failed)
    worker.completed.connect(thread.quit)
    thread.finished.connect(worker.deleteLater)
    thread.finished.connect(bridge.deleteLater)
    thread.finished.connect(handle._mark_finished)
    thread.start()
    return handle


__all__ = ["QtBackgroundOperation", "start_qt_background_operation"]
