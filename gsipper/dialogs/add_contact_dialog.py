"""Add / edit contact dialog.

Mirrors MicroSIP's AddDlg with the fields we actually use: name,
primary number, optional SIP URI, optional organization. Multi-phone
contacts get all their numbers preserved on import but can only be
edited as a single primary here.
"""

from __future__ import annotations

from typing import Callable, Optional

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk  # noqa: E402

from ..storage.contacts import Contact, new_id


class AddContactDialog(Adw.PreferencesWindow):
    def __init__(
        self,
        parent: Gtk.Window,
        on_save: Callable[[Contact], None],
        contact: Optional[Contact] = None,
    ) -> None:
        super().__init__()
        self.set_title("New contact" if contact is None else "Edit contact")
        self.set_transient_for(parent)
        self.set_modal(True)
        self.set_search_enabled(False)
        self.set_default_size(480, 420)

        self._on_save = on_save
        self._contact = contact or Contact(id=new_id())

        page = Adw.PreferencesPage()

        identity = Adw.PreferencesGroup(title="Identity")
        self._row_name = self._entry("Name", self._contact.name)
        self._row_org = self._entry("Organization", self._contact.organization)
        for r in (self._row_name, self._row_org):
            identity.add(r)
        page.add(identity)

        contact_group = Adw.PreferencesGroup(title="Contact")
        primary_number = ""
        if self._contact.phones:
            primary_number = self._contact.phones[0].get("number", "")
        self._row_number = self._entry("Phone number", primary_number)
        self._row_sip = self._entry("SIP URI (optional)", self._contact.sip_uri)
        for r in (self._row_number, self._row_sip):
            contact_group.add(r)
        page.add(contact_group)

        self.add(page)
        self.connect("close-request", self._on_close)

    @staticmethod
    def _entry(title: str, value: str) -> Adw.EntryRow:
        row = Adw.EntryRow(title=title)
        row.set_text(value or "")
        return row

    def _on_close(self, *_args) -> bool:
        c = self._contact
        c.name = self._row_name.get_text().strip()
        c.organization = self._row_org.get_text().strip()
        c.sip_uri = self._row_sip.get_text().strip()

        number = self._row_number.get_text().strip()
        if number:
            if c.phones:
                c.phones[0]["number"] = number
            else:
                c.phones = [{"label": "phone", "number": number}]
        elif c.phones:
            # User cleared the field — drop the leading phone.
            c.phones = c.phones[1:]

        if c.name and (c.sip_uri or number):
            try:
                self._on_save(c)
            except Exception:
                import traceback
                traceback.print_exc()
        return False
