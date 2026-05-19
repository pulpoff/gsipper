"""Messages view — SIP IM conversations.

Mirrors MicroSIP's MessagesDlg: a left pane with conversations and a
right pane with the active chat. Initial implementation just shows the
empty state; full chat lands in step 7.
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


class MessagesView(Gtk.Box):
    def __init__(self) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=0)

        if _USE_ADW:
            status = Adw.StatusPage(
                icon_name="mail-unread-symbolic",
                title="No conversations",
                description="SIP messaging will appear here once an account is configured.",
            )
            status.set_vexpand(True)
            self.append(status)
        else:
            empty = Gtk.Label(label="No conversations")
            empty.set_vexpand(True)
            empty.add_css_class("dim-label")
            self.append(empty)
