"""Contacts view — searchable list with Add and Import buttons.

Reads / writes ~/.local/share/gsipper/contacts.json. Click a row to
place a call to the contact's primary target (SIP URI if set, else
the first phone number). Import accepts vCard 3.0/4.0 (.vcf) or
Google Contacts CSV (.csv).
"""

from __future__ import annotations

import logging
from typing import Callable, List, Optional

import gi

gi.require_version("Gtk", "4.0")

_USE_ADW = False
try:
    gi.require_version("Adw", "1")
    from gi.repository import Adw  # noqa: E402
    _USE_ADW = True
except (ValueError, ImportError):
    pass

from gi.repository import Gio, GLib, GObject, Gtk  # noqa: E402

from ..storage import contacts as contacts_store
from ..storage.contact_import import import_file
from ..storage.contacts import Contact


logger = logging.getLogger(__name__)


class ContactsView(Gtk.Box):
    __gsignals__ = {
        # Fired when the user clicks a contact row; payload is the
        # SIP URI or raw phone number.
        "call-requested": (GObject.SignalFlags.RUN_FIRST, None, (str,)),
    }

    def __init__(self) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=0)

        self._contacts: List[Contact] = contacts_store.load_contacts()
        self._query: str = ""

        # GAction group exposing edit/delete/dial-by-index for menu rows.
        self._action_group = Gio.SimpleActionGroup()
        for name, handler in (
            ("dial",   self._on_dial_action),
            ("edit",   self._on_edit_action),
            ("delete", self._on_delete_action),
        ):
            action = Gio.SimpleAction.new(name, GLib.VariantType.new("s"))
            action.connect("activate", handler)
            self._action_group.add_action(action)
        self.insert_action_group("contacts", self._action_group)

        # Top action bar: search, Add, Import.
        top = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6,
                      margin_top=8, margin_bottom=8,
                      margin_start=8, margin_end=8)

        self._search = Gtk.SearchEntry()
        self._search.set_placeholder_text("Search contacts…")
        self._search.set_hexpand(True)
        self._search.connect("search-changed", self._on_search_changed)
        top.append(self._search)

        add_btn = Gtk.Button(icon_name="list-add-symbolic", tooltip_text="Add contact")
        add_btn.add_css_class("flat")
        add_btn.connect("clicked", lambda *_: self._open_add_dialog())
        top.append(add_btn)

        import_btn = Gtk.Button(icon_name="document-open-symbolic",
                                tooltip_text="Import contacts.vcf or contacts.csv")
        import_btn.add_css_class("flat")
        import_btn.connect("clicked", lambda *_: self._open_import_picker())
        top.append(import_btn)

        self.append(top)

        # Empty state ↔ list.
        self._stack = Gtk.Stack()
        self._stack.set_vexpand(True)

        if _USE_ADW:
            self._empty = Adw.StatusPage(
                icon_name="system-users-symbolic",
                title="No contacts yet",
                description="Use the + button above to add a contact, "
                            "or the open icon to import a vCard or Google CSV.",
            )
        else:
            self._empty = Gtk.Label(label="No contacts yet")
            self._empty.add_css_class("dim-label")
        self._stack.add_named(self._empty, "empty")

        self._listbox = Gtk.ListBox()
        self._listbox.set_selection_mode(Gtk.SelectionMode.NONE)
        self._listbox.add_css_class("navigation-sidebar")
        self._listbox.set_filter_func(self._row_filter)
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scrolled.set_child(self._listbox)
        scrolled.set_vexpand(True)
        self._stack.add_named(scrolled, "list")

        self.append(self._stack)

        self._rebuild_rows()

    # ------------------------------------------------------------------
    # Search filter
    # ------------------------------------------------------------------

    def _on_search_changed(self, entry: Gtk.SearchEntry) -> None:
        self._query = entry.get_text().strip()
        self._listbox.invalidate_filter()

    def _row_filter(self, row) -> bool:
        contact = getattr(row, "_contact", None)
        if contact is None:
            return True
        return contact.matches(self._query)

    # ------------------------------------------------------------------
    # Row rendering
    # ------------------------------------------------------------------

    def _rebuild_rows(self) -> None:
        child = self._listbox.get_first_child()
        while child is not None:
            nxt = child.get_next_sibling()
            self._listbox.remove(child)
            child = nxt

        if not self._contacts:
            self._stack.set_visible_child_name("empty")
            return

        # Stable sort by name.
        for contact in sorted(self._contacts, key=lambda c: c.name.casefold()):
            row = self._make_row(contact)
            row._contact = contact  # type: ignore[attr-defined]
            self._listbox.append(row)
        self._stack.set_visible_child_name("list")

    def _make_row(self, contact: Contact):
        subtitle_bits = []
        if contact.organization:
            subtitle_bits.append(contact.organization)
        if contact.sip_uri:
            subtitle_bits.append(contact.sip_uri)
        elif contact.phones:
            label = contact.phones[0].get("label", "")
            number = contact.phones[0].get("number", "")
            subtitle_bits.append(f"{label}: {number}" if label else number)

        if _USE_ADW:
            row = Adw.ActionRow(
                title=contact.name or "(unnamed)",
                subtitle="  ·  ".join(subtitle_bits),
            )
            row.set_activatable(True)
            avatar = Gtk.Image.new_from_icon_name("avatar-default-symbolic")
            avatar.set_pixel_size(28)
            row.add_prefix(avatar)

            call_btn = Gtk.Button(icon_name="call-start-symbolic",
                                  valign=Gtk.Align.CENTER)
            call_btn.add_css_class("flat")
            call_btn.set_tooltip_text("Call")
            call_btn.connect("clicked", lambda *_: self._dial(contact))
            row.add_suffix(call_btn)

            menu_btn = Gtk.MenuButton(icon_name="view-more-symbolic",
                                      valign=Gtk.Align.CENTER)
            menu_btn.add_css_class("flat")
            menu_btn.set_menu_model(self._row_menu(contact))
            row.add_suffix(menu_btn)

            row.connect("activated", lambda *_: self._dial(contact))
            return row

        # Fallback for vanilla GTK.
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8,
                      margin_top=6, margin_bottom=6,
                      margin_start=10, margin_end=10)
        avatar = Gtk.Image.new_from_icon_name("avatar-default-symbolic")
        avatar.set_pixel_size(28)
        box.append(avatar)
        text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, hexpand=True)
        title = Gtk.Label(label=contact.name or "(unnamed)", xalign=0.0)
        title.add_css_class("heading")
        text.append(title)
        if subtitle_bits:
            sub = Gtk.Label(label="  ·  ".join(subtitle_bits), xalign=0.0)
            sub.add_css_class("dim-label")
            sub.add_css_class("caption")
            text.append(sub)
        box.append(text)
        click = Gtk.GestureClick()
        click.connect("released", lambda *_: self._dial(contact))
        box.add_controller(click)
        return box

    def _row_menu(self, contact: Contact) -> Gio.Menu:
        menu = Gio.Menu()
        # If a contact has multiple numbers, list them all so the user
        # can pick which to dial. The "id|index" target lets the dial
        # action find the contact and pick a specific phone.
        for idx, phone in enumerate(contact.phones[:5]):
            label = phone.get("label") or "phone"
            number = phone.get("number") or ""
            if not number:
                continue
            item = Gio.MenuItem.new(f"Call {label}: {number}", None)
            item.set_action_and_target_value(
                "contacts.dial",
                GLib.Variant.new_string(f"{contact.id}|{idx}"),
            )
            menu.append_item(item)

        edit_section = Gio.Menu()
        edit = Gio.MenuItem.new("Edit…", None)
        edit.set_action_and_target_value(
            "contacts.edit", GLib.Variant.new_string(contact.id))
        edit_section.append_item(edit)

        delete = Gio.MenuItem.new("Delete", None)
        delete.set_action_and_target_value(
            "contacts.delete", GLib.Variant.new_string(contact.id))
        edit_section.append_item(delete)

        menu.append_section(None, edit_section)
        return menu

    # ------------------------------------------------------------------
    # Edit / Delete / Dial action handlers
    # ------------------------------------------------------------------

    def _find(self, contact_id: str) -> Optional[Contact]:
        return next((c for c in self._contacts if c.id == contact_id), None)

    def _on_dial_action(self, _action, param) -> None:
        value = param.get_string()
        contact_id, sep, idx_str = value.rpartition("|")
        if not sep:
            contact_id, idx_str = value, "0"
        contact = self._find(contact_id)
        if contact is None:
            return
        try:
            idx = int(idx_str)
        except ValueError:
            idx = 0
        if 0 <= idx < len(contact.phones):
            number = contact.phones[idx].get("number", "")
            if number:
                logger.info("dial from contact menu: %s -> %s", contact.name, number)
                self.emit("call-requested", number)

    def _on_edit_action(self, _action, param) -> None:
        contact = self._find(param.get_string())
        if contact is None:
            return
        self._open_edit_dialog(contact)

    def _on_delete_action(self, _action, param) -> None:
        contact = self._find(param.get_string())
        if contact is None:
            return
        self._confirm_delete(contact)

    def _open_edit_dialog(self, contact: Contact) -> None:
        from ..dialogs.add_contact_dialog import AddContactDialog
        win = self.get_root()
        AddContactDialog(
            parent=win,
            on_save=self._on_contact_saved,
            contact=contact,
        ).present()

    def _confirm_delete(self, contact: Contact) -> None:
        win = self.get_root()
        if _USE_ADW and hasattr(Adw, "MessageDialog"):
            dialog = Adw.MessageDialog.new(
                win,
                "Delete contact?",
                f"Remove “{contact.name}” from the contact list? This cannot be undone.",
            )
            dialog.add_response("cancel", "Cancel")
            dialog.add_response("delete", "Delete")
            dialog.set_response_appearance(
                "delete", Adw.ResponseAppearance.DESTRUCTIVE)
            dialog.set_default_response("cancel")
            dialog.set_close_response("cancel")
            # Defer the actual delete work via idle_add so the
            # Adw.MessageDialog can finish its close animation first.
            dialog.connect(
                "response",
                lambda _d, response: GLib.idle_add(
                    self._on_delete_confirmed, contact, response),
            )
            dialog.present()
            return

        # Fallback for libadwaita < 1.2: plain Gtk.MessageDialog.
        dialog = Gtk.MessageDialog(
            transient_for=win,
            modal=True,
            buttons=Gtk.ButtonsType.NONE,
            message_type=Gtk.MessageType.WARNING,
            text="Delete contact?",
            secondary_text=f"Remove '{contact.name}'?",
        )
        dialog.add_button("Cancel", Gtk.ResponseType.CANCEL)
        dialog.add_button("Delete", Gtk.ResponseType.OK)
        dialog.connect(
            "response",
            lambda d, response: (
                self._on_delete_confirmed(
                    contact,
                    "delete" if response == Gtk.ResponseType.OK else "cancel",
                ),
                d.destroy(),
            ),
        )
        dialog.present()

    def _on_delete_confirmed(self, contact: Contact, response: str) -> None:
        if response != "delete":
            return
        logger.info("delete contact: %s (%s)", contact.name, contact.id)

        # Drop the contact from memory and remove ONLY the matching row
        # from the listbox; rebuilding all rows is expensive (each row
        # is an Adw.ExpanderRow with one sub-row per phone) and used
        # to make the delete confirmation feel laggy.
        self._contacts = [c for c in self._contacts if c.id != contact.id]
        self._remove_row_for(contact.id)
        if not self._contacts and hasattr(self, "_stack"):
            self._stack.set_visible_child_name("empty")

        # Persist to disk in the next idle slot so the row disappearance
        # paints first, before the (cheap but still synchronous) JSON
        # write happens.
        GLib.idle_add(self._persist_contacts_idle)

    def _remove_row_for(self, contact_id: str) -> None:
        child = self._listbox.get_first_child()
        while child is not None:
            nxt = child.get_next_sibling()
            row_contact = getattr(child, "_contact", None)
            if row_contact is not None and row_contact.id == contact_id:
                self._listbox.remove(child)
                return
            child = nxt

    def _persist_contacts_idle(self) -> bool:
        try:
            contacts_store.save_contacts(self._contacts)
        except Exception:
            logger.exception("save_contacts failed")
        return False  # one-shot

    # ------------------------------------------------------------------
    # Dialing
    # ------------------------------------------------------------------

    def _dial(self, contact: Contact) -> None:
        target = contact.primary_target()
        if not target:
            return
        logger.info("dial from contact: %s -> %s", contact.name, target)
        self.emit("call-requested", target)

    # ------------------------------------------------------------------
    # Add + Import
    # ------------------------------------------------------------------

    def _open_add_dialog(self) -> None:
        from ..dialogs.add_contact_dialog import AddContactDialog
        win = self.get_root()
        AddContactDialog(parent=win, on_save=self._on_contact_saved).present()

    def _on_contact_saved(self, contact: Contact) -> None:
        # Replace if id exists, otherwise append.
        for i, existing in enumerate(self._contacts):
            if existing.id == contact.id:
                self._contacts[i] = contact
                break
        else:
            self._contacts.append(contact)
        contacts_store.save_contacts(self._contacts)
        self._rebuild_rows()

    def _open_import_picker(self) -> None:
        dialog = Gtk.FileDialog()
        dialog.set_title("Import contacts")
        filt_all = Gtk.FileFilter()
        filt_all.set_name("vCard or Google CSV")
        filt_all.add_suffix("vcf")
        filt_all.add_suffix("csv")
        filt_all.add_mime_type("text/vcard")
        filt_all.add_mime_type("text/x-vcard")
        filt_all.add_mime_type("text/csv")
        filt_vcf = Gtk.FileFilter()
        filt_vcf.set_name("vCard (.vcf)")
        filt_vcf.add_suffix("vcf")
        filt_csv = Gtk.FileFilter()
        filt_csv.set_name("CSV (.csv)")
        filt_csv.add_suffix("csv")
        filters = Gio.ListStore.new(Gtk.FileFilter)
        filters.append(filt_all)
        filters.append(filt_vcf)
        filters.append(filt_csv)
        dialog.set_filters(filters)
        dialog.set_default_filter(filt_all)

        win = self.get_root()
        dialog.open(win, None, self._on_import_picked)

    def _on_import_picked(self, dialog: Gtk.FileDialog, result) -> None:
        try:
            f = dialog.open_finish(result)
        except GLib.Error as exc:
            # User cancelled, etc.
            if "dismissed" not in str(exc).lower():
                logger.info("import picker error: %s", exc)
            return
        path = f.get_path()
        if not path:
            return
        try:
            imported = import_file(path)
        except Exception as exc:
            logger.error("import failed: %s", exc)
            self._toast(f"Import failed: {exc}")
            return
        added = contacts_store.merge_imported(self._contacts, imported)
        contacts_store.save_contacts(self._contacts)
        self._rebuild_rows()
        logger.info("imported %d new contacts from %s", added, path)
        self._toast(f"Imported {added} new contact{'' if added == 1 else 's'}")

    # ------------------------------------------------------------------
    # Misc
    # ------------------------------------------------------------------

    def _toast(self, message: str) -> None:
        logger.info("%s", message)
