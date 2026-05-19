"""Calls view — call history (incoming / outgoing / missed).

Mirrors MicroSIP's Calls tab (Calls.cpp): a list of past calls with
direction icon, contact label, and timestamp. Wired up to persisted
history in step 5.
"""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")

_USE_ADW = False
try:
    gi.require_version("Adw", "1")
    from gi.repository import Adw  # noqa: E402
    _USE_ADW = True
except (ValueError, ImportError):
    pass

from gi.repository import Gtk  # noqa: E402


class CallsView(Gtk.Box):
    def __init__(self) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=0)

        if _USE_ADW:
            status = Adw.StatusPage(
                icon_name="call-start-symbolic",
                title="No calls yet",
                description="Your incoming, outgoing and missed calls will appear here.",
            )
            status.set_vexpand(True)
            self.append(status)
        else:
            empty = Gtk.Label(label="No calls yet")
            empty.set_vexpand(True)
            empty.add_css_class("dim-label")
            self.append(empty)
