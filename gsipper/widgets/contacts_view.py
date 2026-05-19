"""Contacts view — searchable list of saved contacts.

Mirrors MicroSIP's Contacts tab: a search entry at top and a list below.
Full CRUD (add/edit/delete) lands in step 6; this placeholder shows the
empty state and the search bar so the layout matches from day one.
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


class ContactsView(Gtk.Box):
    def __init__(self) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=0)

        search = Gtk.SearchEntry()
        search.set_placeholder_text("Search contacts…")
        search.set_margin_top(8)
        search.set_margin_bottom(8)
        search.set_margin_start(8)
        search.set_margin_end(8)
        self.append(search)

        if _USE_ADW:
            status = Adw.StatusPage(
                icon_name="system-users-symbolic",
                title="No contacts yet",
                description="Add contacts to call or message them with one click.",
            )
            add_btn = Gtk.Button(label="Add contact")
            add_btn.add_css_class("suggested-action")
            add_btn.add_css_class("pill")
            add_btn.set_halign(Gtk.Align.CENTER)
            status.set_child(add_btn)
            status.set_vexpand(True)
            self.append(status)
        else:
            empty = Gtk.Label(label="No contacts yet")
            empty.set_vexpand(True)
            empty.add_css_class("dim-label")
            self.append(empty)
