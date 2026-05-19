"""XDG-themed ringtone for incoming calls.

Plays the freedesktop `phone-incoming-call` event via GSound (the
PyGObject wrapper around libcanberra). Loops every few seconds until
explicitly stopped, so a single play_simple call doesn't leave the
caller with a silent UI after one tone.

Falls back silently if GSound isn't installed (e.g. headless / minimal
desktop) — the visual popup + notification remain.
"""

from __future__ import annotations

import logging

from gi.repository import GLib

logger = logging.getLogger(__name__)

try:
    import gi
    gi.require_version("GSound", "1.0")
    from gi.repository import GSound  # noqa: E402
    HAVE_GSOUND = True
except (ValueError, ImportError):
    GSound = None  # type: ignore
    HAVE_GSOUND = False


_REPEAT_MS = 4000  # roughly one full ring-ring cadence


class Ringer:
    def __init__(self) -> None:
        self._ctx = None
        self._timer_id = 0
        if not HAVE_GSOUND:
            logger.info("GSound unavailable; incoming calls will be silent")
            return
        try:
            ctx = GSound.Context()
            ctx.init()
            self._ctx = ctx
        except Exception as exc:
            logger.warning("GSound init failed: %s", exc)

    def start(self) -> None:
        if self._ctx is None or self._timer_id:
            return
        self._play_once()
        self._timer_id = GLib.timeout_add(_REPEAT_MS, self._play_once)

    def stop(self) -> None:
        if self._timer_id:
            GLib.source_remove(self._timer_id)
            self._timer_id = 0
        if self._ctx is not None:
            try:
                self._ctx.cancel(1)
            except Exception:
                pass

    def _play_once(self) -> bool:
        if self._ctx is None:
            return False
        try:
            self._ctx.play_simple({GSound.ATTR_EVENT_ID: "phone-incoming-call"})
        except Exception as exc:
            logger.warning("ringtone playback failed: %s", exc)
            return False
        return True
