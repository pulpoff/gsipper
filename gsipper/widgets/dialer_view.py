"""Dialer view — numeric pad + call/end buttons.

Mirrors MicroSIP's Dialer.cpp: a 4×3 keypad (1–9, *, 0, #), a number entry
above it, and a green Call / red End row at the bottom.
"""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import GObject, Gtk  # noqa: E402


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
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        self.set_margin_top(18)
        self.set_margin_bottom(18)
        self.set_margin_start(18)
        self.set_margin_end(18)

        self._entry = Gtk.Entry()
        self._entry.set_placeholder_text("Number or sip:user@host")
        self._entry.set_alignment(0.5)
        self._entry.add_css_class("dialer-number")
        self._entry.connect("activate", lambda *_: self._emit_call())
        self.append(self._entry)

        grid = Gtk.Grid(column_spacing=8, row_spacing=8, halign=Gtk.Align.CENTER)
        for idx, (digit, sub) in enumerate(_KEYPAD):
            row, col = divmod(idx, 3)
            grid.attach(self._make_keypad_button(digit, sub), col, row, 1, 1)
        self.append(grid)

        actions = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12,
                          halign=Gtk.Align.CENTER, margin_top=8)

        self._call_btn = Gtk.Button(label="Call")
        self._call_btn.set_icon_name("call-start-symbolic")
        self._call_btn.add_css_class("suggested-action")
        self._call_btn.add_css_class("dialer-call")
        self._call_btn.set_size_request(140, 48)
        self._call_btn.connect("clicked", lambda *_: self._emit_call())
        actions.append(self._call_btn)

        self._end_btn = Gtk.Button(label="End")
        self._end_btn.set_icon_name("call-stop-symbolic")
        self._end_btn.add_css_class("destructive-action")
        self._end_btn.add_css_class("dialer-end")
        self._end_btn.set_size_request(140, 48)
        self._end_btn.set_sensitive(False)
        self._end_btn.connect("clicked", lambda *_: self.emit("hangup-requested"))
        actions.append(self._end_btn)

        self.append(actions)

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

    def set_number(self, number: str) -> None:
        self._entry.set_text(number)

    def set_in_call(self, in_call: bool) -> None:
        self._call_btn.set_sensitive(not in_call)
        self._end_btn.set_sensitive(in_call)
