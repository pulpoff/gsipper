"""Ringtone playback for incoming calls.

Two backends layered behind a single Ringer API:

1. **Default ringtone** — the freedesktop ``phone-incoming-call``
   event via GSound (PyGObject wrapper around libcanberra).
   Looped manually via a GLib timeout so a single play_simple()
   call doesn't leave the caller in silence.

2. **Custom ringtone (MP3)** — when the user picks one of the
   bundled ``ringtones/ring*.mp3`` files in Settings, we play it
   via a GStreamer ``playbin`` with the standard end-of-stream
   "rewind and play again" loop.

Either backend gracefully degrades to a no-op if its dependency
isn't installed; the visual popup + GNOME notification remain
the user-facing alert in that case.
"""

from __future__ import annotations

import logging
import os
from typing import Optional

import gi

from gi.repository import GLib

logger = logging.getLogger(__name__)

try:
    gi.require_version("GSound", "1.0")
    from gi.repository import GSound  # noqa: E402
    HAVE_GSOUND = True
except (ValueError, ImportError):
    GSound = None  # type: ignore
    HAVE_GSOUND = False

try:
    gi.require_version("Gst", "1.0")
    from gi.repository import Gst  # noqa: E402
    Gst.init(None)
    HAVE_GST = True
except (ValueError, ImportError, Exception):
    Gst = None  # type: ignore
    HAVE_GST = False


_REPEAT_MS = 4000  # roughly one full ring-ring cadence for GSound

# Search path for custom-ringtone basenames passed via set_source().
# /usr/share/gsipper/ringtones/  → deb install layout
# <repo>/ringtones/              → source tree layout for './build.sh --run'
_RINGTONE_DIRS = [
    "/usr/share/gsipper/ringtones",
    os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "ringtones")),
]


def list_bundled_ringtones() -> list:
    """Return the basenames of MP3 / OGG ringtones we found on the
    standard search path. Used by SettingsDialog to populate the
    drop-down."""
    seen: set = set()
    out: list = []
    for d in _RINGTONE_DIRS:
        if not os.path.isdir(d):
            continue
        for name in sorted(os.listdir(d)):
            if name.lower().endswith((".mp3", ".ogg", ".oga", ".wav")) \
                    and name not in seen:
                seen.add(name)
                out.append(name)
    return out


def resolve_ringtone(name: str) -> str:
    """Look up a ringtone basename in the standard search dirs.
    Returns the absolute path, or '' if the file isn't present."""
    if not name:
        return ""
    if os.path.isabs(name) and os.path.isfile(name):
        return name
    for d in _RINGTONE_DIRS:
        path = os.path.join(d, name)
        if os.path.isfile(path):
            return path
    return ""


class Ringer:
    def __init__(self) -> None:
        # GSound context for the freedesktop default; None if the
        # library isn't installed.
        self._ctx = None
        if HAVE_GSOUND:
            try:
                ctx = GSound.Context()
                ctx.init()
                self._ctx = ctx
            except Exception as exc:
                logger.warning("GSound init failed: %s", exc)
        elif not HAVE_GST:
            logger.info("Neither GSound nor GStreamer available; "
                        "incoming calls will be silent")

        # GStreamer playbin for custom files; created lazily so we
        # don't pay the cost until someone picks a non-default tone.
        self._playbin = None

        # Currently selected source. "" / None = default; otherwise
        # the absolute path of an MP3 / OGG file to loop.
        self._source_path: str = ""

        # Active GLib timeout id for the GSound repeat loop. 0 when
        # the ringer is stopped or running on the GStreamer backend
        # (which loops via the bus EOS handler instead of a timer).
        self._timer_id = 0

    # ------------------------------------------------------------------
    # Configuration

    def set_source(self, name_or_path: str) -> None:
        """Pick the ringtone. Empty string / None falls back to the
        freedesktop default via GSound. Anything else is resolved
        through :func:`resolve_ringtone` (basenames look up
        /usr/share/gsipper/ringtones/ first, then the source tree's
        ringtones/ for dev runs)."""
        if not name_or_path:
            self._source_path = ""
            return
        path = resolve_ringtone(name_or_path)
        if not path:
            logger.warning("ringtone %r not found; using default", name_or_path)
            self._source_path = ""
        else:
            self._source_path = path

    # ------------------------------------------------------------------
    # Public API

    def start(self) -> None:
        """Start the currently-configured ringtone. Idempotent —
        calling start() twice in a row doesn't double the tempo."""
        if self._source_path:
            self._start_gst()
        else:
            self._start_gsound()

    def stop(self) -> None:
        self._stop_gst()
        self._stop_gsound()

    def play_once(self, name_or_path: Optional[str] = None) -> None:
        """One-shot 'preview' used by the Settings dialog. Plays the
        given ringtone for ~5 s then auto-stops, so the user can
        sample a selection without it cycling. Pass None to preview
        the currently-configured source."""
        prev = self._source_path
        if name_or_path is not None:
            self.set_source(name_or_path)
        try:
            self.start()
        finally:
            self._source_path = prev
        # Auto-stop after ~5 s. The user can also tap Play again to
        # restart from the top; we wire that via SettingsDialog.
        GLib.timeout_add_seconds(5, lambda: (self.stop(), False)[1])

    # ------------------------------------------------------------------
    # GSound backend (default ringtone)

    def _start_gsound(self) -> None:
        if self._ctx is None or self._timer_id:
            return
        self._play_once_gsound()
        self._timer_id = GLib.timeout_add(_REPEAT_MS, self._play_once_gsound)

    def _stop_gsound(self) -> None:
        if self._timer_id:
            GLib.source_remove(self._timer_id)
            self._timer_id = 0
        if self._ctx is not None:
            try:
                self._ctx.cancel(1)
            except Exception:
                pass

    def _play_once_gsound(self) -> bool:
        if self._ctx is None:
            return False
        try:
            self._ctx.play_simple({GSound.ATTR_EVENT_ID: "phone-incoming-call"})
        except Exception as exc:
            logger.warning("ringtone playback failed: %s", exc)
            return False
        return True

    # ------------------------------------------------------------------
    # GStreamer backend (custom MP3)

    def _ensure_playbin(self) -> bool:
        if not HAVE_GST:
            return False
        if self._playbin is not None:
            return True
        try:
            self._playbin = Gst.ElementFactory.make("playbin", "gsipper-ringer")
            if self._playbin is None:
                return False
            bus = self._playbin.get_bus()
            bus.add_signal_watch()
            bus.connect("message::eos", self._on_gst_eos)
            bus.connect("message::error", self._on_gst_error)
            return True
        except Exception:
            logger.exception("GStreamer playbin init failed")
            self._playbin = None
            return False

    def _start_gst(self) -> None:
        if not self._ensure_playbin():
            # GStreamer unavailable — fall back to default so the
            # caller at least hears something.
            self._start_gsound()
            return
        try:
            self._playbin.set_state(Gst.State.NULL)
            self._playbin.set_property("uri", Gst.filename_to_uri(self._source_path))
            self._playbin.set_state(Gst.State.PLAYING)
        except Exception:
            logger.exception("GStreamer ringtone start failed")

    def _stop_gst(self) -> None:
        if self._playbin is None:
            return
        try:
            self._playbin.set_state(Gst.State.NULL)
        except Exception:
            pass

    def _on_gst_eos(self, _bus, _msg) -> None:
        # Loop: rewind and play again. set_state(NULL) then PLAYING
        # restarts the pipeline cleanly.
        if self._playbin is None:
            return
        try:
            self._playbin.set_state(Gst.State.READY)
            self._playbin.set_state(Gst.State.PLAYING)
        except Exception:
            logger.exception("ringtone loop restart failed")

    def _on_gst_error(self, _bus, msg) -> None:
        err, dbg = msg.parse_error()
        logger.warning("GStreamer ringtone error: %s (%s)", err, dbg)
        self._stop_gst()
