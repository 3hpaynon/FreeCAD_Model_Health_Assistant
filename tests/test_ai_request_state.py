import ast
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from model_health_assistant.ai_request_state import (
    AIRequestAlreadyRunningError,
    AIRequestStateClosedError,
    AIRequestStateController,
)


def test_initial_snapshot_is_immutable_idle_and_nonbusy() -> None:
    controller = AIRequestStateController()

    snapshot = controller.snapshot

    assert snapshot.state == "idle"
    assert snapshot.ticket is None
    assert snapshot.request_type is None
    assert snapshot.provider_identifier is None
    assert snapshot.progress_message == ""
    assert snapshot.is_active is False
    assert snapshot.accepts_callbacks is False
    assert snapshot.is_terminal is False
    assert controller.is_busy is False
    assert controller.active_ticket is None
    assert controller.is_closed is False
    with pytest.raises(FrozenInstanceError):
        snapshot.state = "running"  # type: ignore[misc]


def test_begin_records_request_provider_progress_and_rejects_overlap() -> None:
    controller = AIRequestStateController()

    ticket = controller.begin(
        "selected-warning",
        "openai",
        progress_message="Preparing the reviewed request.",
    )

    assert ticket == 1
    assert controller.active_ticket == ticket
    assert controller.is_busy is True
    assert controller.snapshot.state == "running"
    assert controller.snapshot.request_type == "selected-warning"
    assert controller.snapshot.provider_identifier == "openai"
    assert controller.snapshot.progress_message == "Preparing the reviewed request."
    with pytest.raises(AIRequestAlreadyRunningError, match="already active"):
        controller.begin("full-analysis", "openai")


@pytest.mark.parametrize(
    ("request_type", "provider_identifier"),
    [
        ("", "openai"),
        ("   ", "openai"),
        ("full-analysis", ""),
        ("full-analysis", "   "),
    ],
)
def test_begin_rejects_missing_request_or_provider_identity(
    request_type: str,
    provider_identifier: str,
) -> None:
    controller = AIRequestStateController()

    with pytest.raises(ValueError):
        controller.begin(request_type, provider_identifier)


def test_progress_and_success_accept_only_the_current_running_ticket() -> None:
    controller = AIRequestStateController()
    ticket = controller.begin("full-analysis", "openai")
    original_snapshot = controller.snapshot

    assert controller.update_progress(ticket + 1, "Stale progress") is False
    assert controller.update_progress(None, "Invalid progress") is False
    assert controller.snapshot is original_snapshot
    assert controller.update_progress(ticket, "Waiting for provider response.") is True
    assert controller.snapshot.progress_message == "Waiting for provider response."
    assert original_snapshot.progress_message == "Request started."

    assert controller.accept_success(ticket, progress_message="Response accepted.") is True
    assert controller.snapshot.state == "succeeded"
    assert controller.snapshot.progress_message == "Response accepted."
    assert controller.snapshot.is_terminal is True
    assert controller.is_busy is False
    assert controller.active_ticket is None
    assert controller.accept_success(ticket) is False
    assert controller.accept_failure(ticket) is False
    assert controller.update_progress(ticket, "Late progress") is False


def test_failure_acceptance_is_terminal_and_does_not_store_an_error() -> None:
    controller = AIRequestStateController()
    ticket = controller.begin("connection-test", "openai")
    provider_error = RuntimeError("sensitive provider detail")

    assert controller.accept_failure(
        ticket,
        progress_message="Connection failed safely.",
    ) is True

    snapshot = controller.snapshot
    assert snapshot.state == "failed"
    assert snapshot.progress_message == "Connection failed safely."
    assert "sensitive provider detail" not in repr(snapshot)
    assert provider_error not in vars(controller).values()


def test_logical_cancel_rejects_late_result_and_new_request_supersedes_it() -> None:
    controller = AIRequestStateController()
    cancelled_ticket = controller.begin("selected-warning", "openai")

    assert controller.cancel(cancelled_ticket) is True
    assert controller.snapshot.state == "cancelled"
    assert controller.is_busy is False
    assert controller.accept_success(cancelled_ticket) is False
    assert controller.accept_failure(cancelled_ticket) is False

    replacement_ticket = controller.begin("custom-question", "openai")

    assert replacement_ticket > cancelled_ticket
    assert controller.snapshot.ticket == replacement_ticket
    assert controller.snapshot.request_type == "custom-question"
    assert controller.update_progress(cancelled_ticket, "Late cancelled progress") is False
    assert controller.accept_success(cancelled_ticket) is False
    assert controller.update_progress(replacement_ticket, "Answering question.") is True
    assert controller.accept_success(replacement_ticket) is True


def test_tickets_are_monotonic_across_terminal_states() -> None:
    controller = AIRequestStateController()

    first = controller.begin("connection-test", "openai")
    assert controller.accept_success(first) is True
    second = controller.begin("selected-warning", "local-mock")
    assert controller.accept_failure(second) is True
    third = controller.begin("full-analysis", "openai")
    assert controller.cancel(third) is True
    fourth = controller.begin("custom-question", "openai")

    assert (first, second, third, fourth) == (1, 2, 3, 4)


def test_detach_suppresses_current_callbacks_but_allows_reopen_with_new_ticket() -> None:
    controller = AIRequestStateController()
    detached_ticket = controller.begin("full-analysis", "openai")

    detached_snapshot = controller.detach(progress_message="Dialog detached.")

    assert detached_snapshot.state == "detached"
    assert detached_snapshot.ticket == detached_ticket
    assert detached_snapshot.progress_message == "Dialog detached."
    assert controller.is_busy is False
    assert controller.accept_success(detached_ticket) is False
    assert controller.accept_failure(detached_ticket) is False
    assert controller.update_progress(detached_ticket, "Late progress") is False

    reopened_ticket = controller.begin("full-analysis", "openai")
    assert reopened_ticket > detached_ticket
    assert controller.accept_success(detached_ticket) is False
    assert controller.accept_success(reopened_ticket) is True


def test_close_is_permanent_idempotent_and_rejects_every_late_callback() -> None:
    controller = AIRequestStateController()
    ticket = controller.begin("full-analysis", "openai")

    closed_snapshot = controller.close(progress_message="Results dialog closed.")

    assert closed_snapshot.state == "closed"
    assert closed_snapshot.ticket == ticket
    assert closed_snapshot.progress_message == "Results dialog closed."
    assert controller.is_closed is True
    assert controller.is_busy is False
    assert controller.update_progress(ticket, "Late progress") is False
    assert controller.cancel(ticket) is False
    assert controller.accept_success(ticket) is False
    assert controller.accept_failure(ticket) is False
    assert controller.close() is closed_snapshot
    with pytest.raises(AIRequestStateClosedError, match="permanently closed"):
        controller.begin("full-analysis", "openai")


def test_progress_messages_must_be_text() -> None:
    controller = AIRequestStateController()
    with pytest.raises(TypeError, match="progress_message"):
        controller.begin(
            "full-analysis",
            "openai",
            progress_message=None,  # type: ignore[arg-type]
        )

    ticket = controller.begin("full-analysis", "openai")
    with pytest.raises(TypeError, match="progress_message"):
        controller.update_progress(ticket, None)  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="progress_message"):
        controller.cancel(ticket, progress_message=None)  # type: ignore[arg-type]


def test_module_has_no_qt_freecad_provider_sdk_or_network_imports() -> None:
    source_path = (
        Path(__file__).parents[1]
        / "src"
        / "model_health_assistant"
        / "ai_request_state.py"
    )
    tree = ast.parse(source_path.read_text(encoding="utf-8"))
    imported_roots = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported_roots.add(node.module.split(".")[0])

    assert not imported_roots & {
        "FreeCAD",
        "FreeCADGui",
        "PySide",
        "PySide2",
        "httpx",
        "keyring",
        "openai",
        "requests",
        "socket",
        "urllib",
    }
