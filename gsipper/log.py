"""Process-wide logging.

Three sinks all wired off the `gsipper` logger:

- stderr (for terminal launches)
- a rotating file at ~/.local/state/gsipper/gsipper.log
- an in-memory ring buffer the Log dialog reads from, with a listener
  callback for live updates.

PJSUA2's own log stream is bridged into this same logger via
gsipper.sip.endpoint._PjLogBridge.
"""

from __future__ import annotations

import collections
import logging
import logging.handlers
import os
import sys
from typing import Callable, List, Optional


_LOG_DIR = os.path.join(
    os.environ.get("XDG_STATE_HOME", os.path.expanduser("~/.local/state")),
    "gsipper",
)
LOG_PATH = os.path.join(_LOG_DIR, "gsipper.log")


class _RingHandler(logging.Handler):
    def __init__(self, capacity: int = 5000) -> None:
        super().__init__()
        self.records: "collections.deque[str]" = collections.deque(maxlen=capacity)
        self.listeners: List[Callable[[str], None]] = []

    def emit(self, record: logging.LogRecord) -> None:
        try:
            msg = self.format(record)
        except Exception:
            return
        self.records.append(msg)
        for cb in list(self.listeners):
            try:
                cb(msg)
            except Exception:
                pass

    def snapshot(self) -> str:
        return "\n".join(self.records)


_ring_handler: Optional[_RingHandler] = None


def setup_logging() -> None:
    """Idempotent: safe to call from main() and from tests."""
    global _ring_handler
    if _ring_handler is not None:
        return

    os.makedirs(_LOG_DIR, exist_ok=True)
    fmt = logging.Formatter(
        "%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    root = logging.getLogger("gsipper")
    root.setLevel(logging.DEBUG)
    root.propagate = False

    _ring_handler = _RingHandler()
    _ring_handler.setFormatter(fmt)
    root.addHandler(_ring_handler)

    stderr_h = logging.StreamHandler(sys.stderr)
    stderr_h.setFormatter(fmt)
    stderr_h.setLevel(logging.INFO)
    root.addHandler(stderr_h)

    try:
        file_h = logging.handlers.RotatingFileHandler(
            LOG_PATH, mode="a", encoding="utf-8",
            maxBytes=1_000_000, backupCount=3,
        )
        file_h.setFormatter(fmt)
        root.addHandler(file_h)
    except OSError:
        pass

    logging.getLogger("gsipper").info("gsipper logger ready, file=%s", LOG_PATH)


def get_log_text() -> str:
    if _ring_handler is None:
        return ""
    return _ring_handler.snapshot()


def add_listener(cb: Callable[[str], None]) -> None:
    if _ring_handler is not None:
        _ring_handler.listeners.append(cb)


def remove_listener(cb: Callable[[str], None]) -> None:
    if _ring_handler is not None:
        try:
            _ring_handler.listeners.remove(cb)
        except ValueError:
            pass


def get_log_path() -> str:
    return LOG_PATH
