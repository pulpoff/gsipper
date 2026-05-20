"""Dialer view — numeric pad + call/end buttons, swappable with the
in-call view.

Mirrors MicroSIP's Dialer.cpp keypad layout but uses a Gtk.Stack so
the same tab smoothly transitions between "idle keypad" and
"active call". The window listens to call-requested / hangup-requested
and drives state via show_keypad() / show_call().
"""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import GObject, Gtk  # noqa: E402

from .in_call_view import InCallView


_KEYPAD = [
    ("1", ""),
    ("2", "ABC"),
    ("3", "DEF"),
    ("4", "GHI"),
    ("5", "JKL"),
    ("6", "MNO"),
    ("7", "PQRS"),
    ("8", "TUV"),
    ("9", "WXYZ"),
    ("∗", ""),  # asterisk
    ("0", "+"),
    ("#", ""),
]


class DialerView(Gtk.Box):
    __gsignals__ = {
        "call-requested": (GObject.SignalFlags.RUN_FIRST, None, (str,)),
        "hangup-requested": (GObject.SignalFlags.RUN_FIRST, None, ()),
    }

    def __init__(self) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=0)

        self._stack = Gtk.Stack()
        self._stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        self._stack.set_transition_duration(150)
        self._stack.set_vexpand(True)

        # Idle page: the keypad.
        keypad = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        keypad.set_margin_top(18)
        keypad.set_margin_bottom(18)
        keypad.set_margin_start(18)
        keypad.set_margin_end(18)

        self._entry = Gtk.Entry()
        # No placeholder: the entry sits centred under the keypad and
        # the placeholder text was visual noise — a stray 'Number or
        # sip:user@host' line under the empty cursor. The user knows
        # what the field is for.
        self._entry.set_alignment(0.5)
        self._entry.add_css_class("dialer-number")
        self._entry.connect("activate", lambda *_: self._emit_call())
        keypad.append(self._entry)

        grid = Gtk.Grid(column_spacing=8, row_spacing=8, halign=Gtk.Align.CENTER)
        for idx, (digit, sub) in enumerate(_KEYPAD):
            row, col = divmod(idx, 3)
            grid.attach(self._make_keypad_button(digit, sub), col, row, 1, 1)
        keypad.append(grid)

        actions = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12,
                          halign=Gtk.Align.CENTER, margin_top=8)
        self._call_btn = Gtk.Button(label="Call")
        self._call_btn.set_icon_name("call-start-symbolic")
        self._call_btn.add_css_class("suggested-action")
        self._call_btn.add_css_class("dialer-call")
        self._call_btn.set_size_request(140, 48)
        self._call_btn.connect("clicked", lambda *_: self._emit_call())
        actions.append(self._call_btn)
        keypad.append(actions)

        self._stack.add_named(keypad, "keypad")

        # In-call page.
        self._in_call = InCallView()
        self._in_call.connect("hangup-requested",
                              lambda *_: self.emit("hangup-requested"))
        self._stack.add_named(self._in_call, "incall")

        self._stack.set_visible_child_name("keypad")
        self.append(self._stack)

    # ------------------------------------------------------------------
    # Keypad helpers
    # ------------------------------------------------------------------

    def _make_keypad_button(self, digit: str, sub: str) -> Gtk.Button:
        btn = Gtk.Button()
        btn.add_css_class("dialer-key")
        btn.set_size_request(80, 56)

        label_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0,
                            halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER)
        primary = Gtk.Label(label=digit)
        primary.add_css_class("title-2")
        label_box.append(primary)
        if sub:
            secondary = Gtk.Label(label=sub)
            secondary.add_css_class("dim-label")
            secondary.add_css_class("caption")
            label_box.append(secondary)
        btn.set_child(label_box)
        btn.connect("clicked", lambda *_: self._append_digit(digit))
        return btn

    def _append_digit(self, digit: str) -> None:
        text = digit if digit != "∗" else "*"
        pos = self._entry.get_position()
        self._entry.insert_text(text, pos)
        self._entry.set_position(pos + len(text))

    def _emit_call(self) -> None:
        number = self._entry.get_text().strip()
        if number:
            self.emit("call-requested", number)

    # ------------------------------------------------------------------
    # Public API used by MainWindow
    # ------------------------------------------------------------------

    def set_number(self, number: str) -> None:
        self._entry.set_text(number)

    def show_call(self, peer: str, state: str = "calling") -> None:
        self._in_call.set_peer(peer)
        self._in_call.set_state(state)
        self._stack.set_visible_child_name("incall")

    def update_call_state(self, state: str) -> None:
        self._in_call.set_state(state)

    def show_keypad(self) -> None:
        self._in_call.reset()
        self._stack.set_visible_child_name("keypad")
