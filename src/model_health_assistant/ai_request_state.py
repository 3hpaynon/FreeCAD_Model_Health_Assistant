"""Pure logical state for one asynchronous provider request at a time.

The controller in this module does not start, stop, or retain a worker thread.
Cancellation is deliberately logical: a cancelled or detached request may
continue in its worker, but its late callbacks are rejected by ticket.  This
keeps worker lifetime concerns separate from UI lifetime concerns and avoids
unsafe thread termination.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

AIRequestLifecycleState = Literal[
    "idle",
    "running",
    "cancelled",
    "succeeded",
    "failed",
    "detached",
    "closed",
]


class AIRequestStateError(RuntimeError):
    """Base class for controlled request-state transition failures."""


class AIRequestAlreadyRunningError(AIRequestStateError):
    """Raised when a second request is begun before the active one ends."""


class AIRequestStateClosedError(AIRequestStateError):
    """Raised when a permanently closed controller is asked to begin work."""


@dataclass(frozen=True, slots=True)
class AIRequestStateSnapshot:
    """Immutable, non-result state suitable for driving a UI display."""

    state: AIRequestLifecycleState
    ticket: int | None
    request_type: str | None
    provider_identifier: str | None
    progress_message: str

    @property
    def is_active(self) -> bool:
        """Return whether this snapshot represents the accepted UI request."""

        return self.state == "running"

    @property
    def accepts_callbacks(self) -> bool:
        """Return whether progress or completion for ``ticket`` may be accepted."""

        return self.state == "running"

    @property
    def is_terminal(self) -> bool:
        """Return whether this snapshot no longer represents active work."""

        return self.state in {
            "cancelled",
            "succeeded",
            "failed",
            "detached",
            "closed",
        }


class AIRequestStateController:
    """Gate callbacks for one logical UI request using monotonic tickets.

    A ticket identifies the request that the UI currently accepts.  Worker
    callbacks must present that ticket to ``update_progress``,
    ``accept_success``, or ``accept_failure``.  Once a request is cancelled,
    detached, superseded, or closed, callbacks for its ticket return ``False``
    and cannot change the snapshot.

    ``detach`` suppresses the current request but permits a later ``begin`` so
    a reusable dialog/controller can be shown again.  ``close`` is permanent;
    a destroyed UI should close its controller so no future request or late
    callback can target it.
    """

    def __init__(self) -> None:
        self._next_ticket = 1
        self._closed = False
        self._snapshot = AIRequestStateSnapshot(
            state="idle",
            ticket=None,
            request_type=None,
            provider_identifier=None,
            progress_message="",
        )

    @property
    def snapshot(self) -> AIRequestStateSnapshot:
        """Return the current immutable state snapshot."""

        return self._snapshot

    @property
    def is_busy(self) -> bool:
        """Return whether one request currently owns the UI state."""

        return self._snapshot.is_active

    @property
    def is_closed(self) -> bool:
        """Return whether the controller permanently rejects new requests."""

        return self._closed

    @property
    def active_ticket(self) -> int | None:
        """Return the running ticket, or ``None`` when no request is active."""

        return self._snapshot.ticket if self.is_busy else None

    def begin(
        self,
        request_type: str,
        provider_identifier: str,
        *,
        progress_message: str = "Request started.",
    ) -> int:
        """Begin a new logical request and return its monotonic ticket."""

        normalized_request_type = _required_identifier_text(
            request_type,
            "request_type",
        )
        normalized_provider_identifier = _required_identifier_text(
            provider_identifier,
            "provider_identifier",
        )
        _require_progress_message(progress_message)
        if self._closed:
            raise AIRequestStateClosedError(
                "The request-state controller is permanently closed."
            )
        if self.is_busy:
            raise AIRequestAlreadyRunningError(
                "A provider request is already active for this interface."
            )

        ticket = self._next_ticket
        self._next_ticket += 1
        self._snapshot = AIRequestStateSnapshot(
            state="running",
            ticket=ticket,
            request_type=normalized_request_type,
            provider_identifier=normalized_provider_identifier,
            progress_message=progress_message,
        )
        return ticket

    def update_progress(self, ticket: object, progress_message: str) -> bool:
        """Accept progress only for the currently running ticket."""

        _require_progress_message(progress_message)
        if not self._accepts_ticket(ticket):
            return False
        self._transition("running", progress_message)
        return True

    def cancel(
        self,
        ticket: object,
        *,
        progress_message: str = "Cancelled.",
    ) -> bool:
        """Logically cancel the running ticket without terminating its worker."""

        _require_progress_message(progress_message)
        if not self._accepts_ticket(ticket):
            return False
        self._transition("cancelled", progress_message)
        return True

    def accept_success(
        self,
        ticket: object,
        *,
        progress_message: str = "Completed.",
    ) -> bool:
        """Accept successful completion only for the running ticket."""

        _require_progress_message(progress_message)
        if not self._accepts_ticket(ticket):
            return False
        self._transition("succeeded", progress_message)
        return True

    def accept_failure(
        self,
        ticket: object,
        *,
        progress_message: str = "Failed.",
    ) -> bool:
        """Accept controlled failure only for the running ticket."""

        _require_progress_message(progress_message)
        if not self._accepts_ticket(ticket):
            return False
        self._transition("failed", progress_message)
        return True

    def detach(
        self,
        *,
        progress_message: str = "Detached.",
    ) -> AIRequestStateSnapshot:
        """Suppress current callbacks while allowing a later request to begin."""

        _require_progress_message(progress_message)
        if self._closed:
            return self._snapshot
        self._transition("detached", progress_message)
        return self._snapshot

    def close(
        self,
        *,
        progress_message: str = "Closed.",
    ) -> AIRequestStateSnapshot:
        """Permanently reject future requests and all late callbacks."""

        _require_progress_message(progress_message)
        if not self._closed:
            self._closed = True
            self._transition("closed", progress_message)
        return self._snapshot

    def _accepts_ticket(self, ticket: object) -> bool:
        return (
            not self._closed
            and self._snapshot.state == "running"
            and type(ticket) is int
            and ticket == self._snapshot.ticket
        )

    def _transition(
        self,
        state: AIRequestLifecycleState,
        progress_message: str,
    ) -> None:
        current = self._snapshot
        self._snapshot = AIRequestStateSnapshot(
            state=state,
            ticket=current.ticket,
            request_type=current.request_type,
            provider_identifier=current.provider_identifier,
            progress_message=progress_message,
        )


def _required_identifier_text(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be non-empty text")
    return value.strip()


def _require_progress_message(value: object) -> None:
    if not isinstance(value, str):
        raise TypeError("progress_message must be text")


__all__ = [
    "AIRequestAlreadyRunningError",
    "AIRequestLifecycleState",
    "AIRequestStateClosedError",
    "AIRequestStateController",
    "AIRequestStateError",
    "AIRequestStateSnapshot",
]
