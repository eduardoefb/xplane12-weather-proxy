"""Capture stdout/stderr and fan out to registered listeners (e.g. the GUI log pane)."""

from __future__ import annotations

import sys
from typing import Callable, TextIO


Listener = Callable[[str], None]


class TeeWriter:
    """Write to the original stream and notify listeners."""

    def __init__(self, original: TextIO) -> None:
        self._original = original
        self._listeners: list[Listener] = []

    def add_listener(self, listener: Listener) -> None:
        self._listeners.append(listener)

    def remove_listener(self, listener: Listener) -> None:
        self._listeners = [item for item in self._listeners if item is not listener]

    def write(self, text: str) -> int:
        if not text:
            return 0
        self._original.write(text)
        self._original.flush()
        for listener in list(self._listeners):
            try:
                listener(text)
            except Exception:
                pass
        return len(text)

    def flush(self) -> None:
        self._original.flush()

    def fileno(self) -> int:
        return self._original.fileno()

    def isatty(self) -> bool:
        return self._original.isatty()


_stdout_tee: TeeWriter | None = None
_stderr_tee: TeeWriter | None = None


def install_log_capture() -> TeeWriter:
    global _stdout_tee, _stderr_tee
    if _stdout_tee is None:
        _stdout_tee = TeeWriter(sys.stdout)
        sys.stdout = _stdout_tee  # type: ignore[assignment]
    if _stderr_tee is None:
        _stderr_tee = TeeWriter(sys.stderr)
        sys.stderr = _stderr_tee  # type: ignore[assignment]
    return _stdout_tee
