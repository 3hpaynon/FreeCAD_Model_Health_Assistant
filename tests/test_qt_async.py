from types import SimpleNamespace

from model_health_assistant.qt_async import start_qt_background_operation


class _BoundSignal:
    def __init__(self) -> None:
        self.callbacks = []

    def connect(self, callback) -> None:
        self.callbacks.append(callback)

    def emit(self, *args) -> None:
        for callback in list(self.callbacks):
            callback(*args)


class _SignalDescriptor:
    def __init__(self, *_args) -> None:
        self.name = ""

    def __set_name__(self, _owner, name: str) -> None:
        self.name = name

    def __get__(self, instance, _owner):
        if instance is None:
            return self
        signals = instance.__dict__.setdefault("_signals", {})
        return signals.setdefault(self.name, _BoundSignal())


class _QObject:
    def __init__(self, parent=None) -> None:
        self.parent = parent
        self.thread = None
        self.deleted = False

    def moveToThread(self, thread) -> None:  # noqa: N802
        self.thread = thread

    def deleteLater(self) -> None:  # noqa: N802
        self.deleted = True


class _QThread:
    def __init__(self, parent=None) -> None:
        self.parent = parent
        self.started = _BoundSignal()
        self.finished = _BoundSignal()
        self.started_count = 0
        self.quit_count = 0

    def start(self) -> None:
        self.started_count += 1
        self.started.emit()

    def quit(self) -> None:
        self.quit_count += 1
        self.finished.emit()


class _DeferredQThread(_QThread):
    latest = None

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        type(self).latest = self

    def start(self) -> None:
        self.started_count += 1

    def complete_deferred_start(self) -> None:
        self.started.emit()


_QT_CORE = SimpleNamespace(
    QObject=_QObject,
    QThread=_QThread,
    Signal=_SignalDescriptor,
    Slot=lambda *_args: lambda function: function,
)

_DEFERRED_QT_CORE = SimpleNamespace(
    QObject=_QObject,
    QThread=_DeferredQThread,
    Signal=_SignalDescriptor,
    Slot=lambda *_args: lambda function: function,
)


def test_background_operation_uses_qthread_and_marshals_success() -> None:
    successes = []
    failures = []

    handle = start_qt_background_operation(
        _QT_CORE,
        object(),
        lambda: "result",
        successes.append,
        failures.append,
    )

    assert successes == ["result"]
    assert failures == []
    assert handle.is_running is False
    assert handle.is_cancelled is False
    assert handle.is_detached is False
    assert handle.cancel() is False
    assert handle.detach() is False


def test_background_operation_marshals_controlled_failure() -> None:
    error = RuntimeError("safe provider failure")
    failures = []

    handle = start_qt_background_operation(
        _QT_CORE,
        object(),
        lambda: (_ for _ in ()).throw(error),
        lambda _result: None,
        failures.append,
    )

    assert failures == [error]
    assert handle.is_running is False


def test_logical_cancel_suppresses_deferred_success_and_still_cleans_up() -> None:
    calls = []
    successes = []
    failures = []

    handle = start_qt_background_operation(
        _DEFERRED_QT_CORE,
        object(),
        lambda: calls.append("operation") or "late result",
        successes.append,
        failures.append,
    )
    thread = _DeferredQThread.latest
    worker = thread.started.callbacks[0].__self__
    bridge = worker.succeeded.callbacks[0].__self__

    assert handle.is_running is True
    assert handle.cancel() is True
    assert handle.cancel() is False
    assert handle.is_cancelled is True
    assert handle.is_detached is False
    assert thread.quit_count == 0

    thread.complete_deferred_start()

    assert calls == ["operation"]
    assert successes == []
    assert failures == []
    assert thread.quit_count == 1
    assert worker.deleted is True
    assert bridge.deleted is True
    assert handle.is_running is False
    assert handle.is_cancelled is True


def test_detach_suppresses_deferred_failure_and_still_cleans_up() -> None:
    error = RuntimeError("late provider failure")
    successes = []
    failures = []

    handle = start_qt_background_operation(
        _DEFERRED_QT_CORE,
        object(),
        lambda: (_ for _ in ()).throw(error),
        successes.append,
        failures.append,
    )
    thread = _DeferredQThread.latest
    worker = thread.started.callbacks[0].__self__
    bridge = worker.failed.callbacks[0].__self__

    assert handle.detach() is True
    assert handle.detach() is False
    assert handle.is_detached is True
    assert handle.is_cancelled is False
    assert thread.quit_count == 0

    thread.complete_deferred_start()

    assert successes == []
    assert failures == []
    assert thread.quit_count == 1
    assert worker.deleted is True
    assert bridge.deleted is True
    assert handle.is_running is False
    assert handle.is_detached is True
