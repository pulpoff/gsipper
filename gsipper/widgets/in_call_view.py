"""In-call status pane.

Shown inside the Dialer tab while a call is active. Displays the peer,
state and call duration; a big red hangup button at the bottom ends
the call.
"""

from __future__ import annotations

import time
from typing import Optional

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import GLib, GObject, Gtk  # noqa: E402


_STATE_LABELS = {
    "calling":   "Calling…",
    "ringing":   "Ringing…",
    "connected": "Connected",
    "incoming":  "Incoming call",
    "ended":     "Call ended",
}


def _format_duration(seconds: int) -> str:
    if seconds < 0:
        seconds = 0
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"


class InCallView(Gtk.Box):
    __gsignals__ = {
        "hangup-requested": (GObject.SignalFlags.RUN_FIRST, None, ()),
    }

    def __init__(self) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        self.set_margin_top(24)
        self.set_margin_bottom(24)
        self.set_margin_start(24)
        self.set_margin_end(24)
        self.set_valign(Gtk.Align.FILL)

        self._peer_label = Gtk.Label(label="")
        self._peer_label.add_css_class("title-1")
        self._peer_label.set_wrap(True)
        self._peer_label.set_justify(Gtk.Justification.CENTER)
        self.append(self._peer_label)

        self._state_label = Gtk.Label(label="")
        self._state_label.add_css_class("dim-label")
        self.append(self._state_label)

        self._duration_label = Gtk.Label(label="")
        self._duration_label.add_css_class("title-2")
        self._duration_label.add_css_class("numeric")
        self.append(self._duration_label)

        spacer = Gtk.Box()
        spacer.set_vexpand(True)
        self.append(spacer)

        hangup_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL,
                             halign=Gtk.Align.CENTER, spacing=12)
        self._hangup_btn = Gtk.Button(label="End")
        self._hangup_btn.set_icon_name("call-stop-symbolic")
        self._hangup_btn.add_css_class("destructive-action")
        self._hangup_btn.add_css_class("pill")
        self._hangup_btn.set_size_request(180, 56)
        self._hangup_btn.connect("clicked",
                                 lambda *_: self.emit("hangup-requested"))
        hangup_row.append(self._hangup_btn)
        self.append(hangup_row)

        self._state: str = "calling"
        self._connected_at: Optional[float] = None
        self._timer_id: int = 0

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def set_peer(self, peer: str) -> None:
        self._peer_label.set_text(peer or "—")

    def set_state(self, state: str) -> None:
        self._state = state
        self._state_label.set_text(_STATE_LABELS.get(state, state))
        if state == "connected" and self._connected_at is None:
            self._connected_at = time.monotonic()
            self._start_timer()
        if state == "ended":
            self._stop_timer()

    def reset(self) -> None:
        self._stop_timer()
        self._connected_at = None
        self._duration_label.set_text("")
        self._state = "calling"
        self._state_label.set_text("")
        self._peer_label.set_text("")

    # ------------------------------------------------------------------
    # Duration timer
    # ------------------------------------------------------------------

    def _start_timer(self) -> None:
        if self._timer_id:
            return
        self._tick()  # update immediately
        self._timer_id = GLib.timeout_add_seconds(1, self._tick)

    def _stop_timer(self) -> None:
        if self._timer_id:
            GLib.source_remove(self._timer_id)
            self._timer_id = 0

    def _tick(self) -> bool:
        if self._connected_at is None:
            return False
        elapsed = int(time.monotonic() - self._connected_at)
        self._duration_label.set_text(_format_duration(elapsed))
        return True
