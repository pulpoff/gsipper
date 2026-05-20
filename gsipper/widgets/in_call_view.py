"""In-call status pane.

Shown inside the Dialer tab while a call is active. Displays the peer,
state and call duration; a big red hangup button at the bottom ends
the call. For incoming-ringing calls a green Answer button appears
alongside, and the red one's label flips to 'Decline'.
"""

from __future__ import annotations

import logging
import time
from typing import Optional

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import GLib, GObject, Gtk  # noqa: E402


logger = logging.getLogger(__name__)


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
        # Emitted when the user taps the green Accept button on an
        # incoming-ringing call. Wired by MainWindow to SipEndpoint
        # .answer_active(), same path the RinginWindow popup uses.
        "answer-requested": (GObject.SignalFlags.RUN_FIRST, None, ()),
        # Mute toggle for the local mic. Payload is the new muted
        # state. Only fires while the call is connected (the toggle
        # button is hidden in every other state).
        "mute-toggled": (GObject.SignalFlags.RUN_FIRST, None, (bool,)),
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

        # Mid-call controls (mute, etc.). Visible only while the call
        # is connected — hidden during ringing/calling/incoming so the
        # accept/decline buttons stay the focus, and hidden after
        # 'ended' to match the in-call pane's reset.
        controls_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL,
                               halign=Gtk.Align.CENTER, spacing=12)
        controls_row.set_margin_bottom(12)
        self._mute_btn = Gtk.ToggleButton()
        self._mute_btn.set_icon_name("microphone-sensitivity-high-symbolic")
        self._mute_btn.set_tooltip_text("Mute microphone")
        self._mute_btn.add_css_class("circular")
        self._mute_btn.set_size_request(48, 48)
        self._mute_btn.connect("toggled", self._on_mute_toggled)
        controls_row.append(self._mute_btn)
        self._controls_row = controls_row
        self._controls_row.set_visible(False)
        self.append(controls_row)

        # Single button row that hosts either:
        #   - [Answer] [Decline]  for incoming-ringing calls
        #   - [End]               for everything else
        # set_call_kind() flips between layouts; we always show ONE
        # row so the in-call pane never resizes mid-call. The Answer
        # button comes first (left) so the user's natural reach lands
        # on accept; the Decline / End button is on the right.
        btn_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL,
                          halign=Gtk.Align.CENTER, spacing=24)

        self._answer_btn = Gtk.Button(label="Answer")
        self._answer_btn.set_icon_name("call-start-symbolic")
        # .call-answer is the gsipper green pill (resources/style.css);
        # GNOME's stock 'suggested-action' is blue, not green, which is
        # the wrong colour for an accept-call button.
        self._answer_btn.add_css_class("call-answer")
        self._answer_btn.add_css_class("pill")
        self._answer_btn.set_size_request(150, 56)
        self._answer_btn.connect("clicked",
                                 lambda *_: self.emit("answer-requested"))
        # Hidden by default — only shown for incoming-ringing.
        self._answer_btn.set_visible(False)
        btn_row.append(self._answer_btn)

        self._hangup_btn = Gtk.Button(label="End")
        self._hangup_btn.set_icon_name("call-stop-symbolic")
        self._hangup_btn.add_css_class("destructive-action")
        self._hangup_btn.add_css_class("pill")
        self._hangup_btn.set_size_request(150, 56)
        self._hangup_btn.connect("clicked",
                                 lambda *_: self.emit("hangup-requested"))
        btn_row.append(self._hangup_btn)

        self.append(btn_row)

        self._state: str = "calling"
        self._incoming: bool = False
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
        self._refresh_buttons()

    def set_call_kind(self, incoming: bool) -> None:
        """Tell the view whether this is an incoming call. Together
        with the current state, this controls whether the Answer
        button is shown (incoming + ringing/incoming = yes; everything
        else = no, Answer hidden, End / Decline visible)."""
        self._incoming = bool(incoming)
        self._refresh_buttons()

    def _refresh_buttons(self) -> None:
        # Show the Answer button for an incoming call that hasn't been
        # accepted yet (state is "incoming" or "ringing"). When the
        # call connects or ends, hide Answer and revert the red button
        # label back to "End".
        is_incoming_ringing = (self._incoming
                               and self._state in ("incoming", "ringing"))
        self._answer_btn.set_visible(is_incoming_ringing)
        self._hangup_btn.set_label("Decline" if is_incoming_ringing else "End")
        # Mid-call controls only make sense once the call is up.
        self._controls_row.set_visible(self._state == "connected")

    def reset(self) -> None:
        self._stop_timer()
        self._connected_at = None
        self._duration_label.set_text("")
        self._state = "calling"
        self._incoming = False
        self._state_label.set_text("")
        self._peer_label.set_text("")
        # Drop the mute state so the next call starts un-muted; do it
        # silently (handler_block) so we don't fire mute-toggled while
        # there's no call to apply it to.
        self._mute_btn.handler_block_by_func(self._on_mute_toggled)
        self._mute_btn.set_active(False)
        self._mute_btn.set_icon_name("microphone-sensitivity-high-symbolic")
        self._mute_btn.set_tooltip_text("Mute microphone")
        self._mute_btn.handler_unblock_by_func(self._on_mute_toggled)
        self._refresh_buttons()

    def _on_mute_toggled(self, btn: Gtk.ToggleButton) -> None:
        muted = btn.get_active()
        # Adwaita gives us muted/unmuted symbolic glyphs for the same
        # mic — flip the icon + tooltip so the button visibly reflects
        # the current state.
        btn.set_icon_name(
            "microphone-disabled-symbolic" if muted
            else "microphone-sensitivity-high-symbolic"
        )
        btn.set_tooltip_text("Unmute microphone" if muted else "Mute microphone")
        self.emit("mute-toggled", muted)

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
