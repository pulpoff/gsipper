"""Account preferences dialog.

Adw.PreferencesWindow with one page covering everything MicroSIP's
AccountDlg exposes that we support today: server, transport,
credentials, display name, and an enabled toggle.
"""

from __future__ import annotations

from typing import Callable

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk  # noqa: E402

from ..storage.settings import AccountSettings


_TRANSPORTS = ["UDP", "TCP", "TLS"]


class AccountDialog(Adw.PreferencesWindow):
    def __init__(
        self,
        parent: Gtk.Window,
        account: AccountSettings,
        on_save: Callable[[AccountSettings], None],
    ) -> None:
        super().__init__()
        self.set_title("Account")
        self.set_transient_for(parent)
        self.set_modal(True)
        self.set_search_enabled(False)
        # All nine rows + three group headers + headerbar -> ~820 px;
        # open big enough that nothing requires scrolling/resizing.
        self.set_default_size(560, 860)

        self._account = account
        self._on_save = on_save

        page = Adw.PreferencesPage()
        page.set_title("Account")
        page.set_icon_name("network-server-symbolic")

        server_group = Adw.PreferencesGroup(title="Server")
        self._row_server = self._entry_row("SIP server", account.server)
        self._row_domain = self._entry_row("Domain (optional)", account.domain)
        self._row_transport = self._combo_row("Transport", _TRANSPORTS, account.transport)
        for r in (self._row_server, self._row_domain, self._row_transport):
            server_group.add(r)
        page.add(server_group)

        creds_group = Adw.PreferencesGroup(title="Credentials")
        self._row_username = self._entry_row("Username", account.username)
        self._row_password = self._password_row("Password", account.password)
        self._row_auth_id = self._entry_row("Auth ID (optional)", account.auth_id)
        for r in (self._row_username, self._row_password, self._row_auth_id):
            creds_group.add(r)
        page.add(creds_group)

        meta_group = Adw.PreferencesGroup(title="Display")
        self._row_name = self._entry_row("Account name", account.name)
        self._row_display = self._entry_row("Display name", account.display_name)
        for r in (self._row_name, self._row_display):
            meta_group.add(r)
        page.add(meta_group)

        enable_group = Adw.PreferencesGroup()
        self._row_enabled = self._switch_row(
            "Enabled", "Register and stay online", account.enabled,
        )
        enable_group.add(self._row_enabled)
        page.add(enable_group)

        self.add(page)
        self.connect("close-request", self._on_close)

    # ------------------------------------------------------------------
    # Row helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _entry_row(title: str, value: str):
        row = Adw.EntryRow(title=title)
        row.set_text(value or "")
        return row

    @staticmethod
    def _password_row(title: str, value: str):
        row = Adw.PasswordEntryRow(title=title)
        row.set_text(value or "")
        return row

    @staticmethod
    def _combo_row(title: str, options, value: str):
        row = Adw.ComboRow(title=title)
        model = Gtk.StringList()
        for o in options:
            model.append(o)
        row.set_model(model)
        try:
            row.set_selected(options.index(value))
        except ValueError:
            row.set_selected(0)
        return row

    @staticmethod
    def _switch_row(title: str, subtitle: str, active: bool):
        if hasattr(Adw, "SwitchRow"):
            row = Adw.SwitchRow(title=title, subtitle=subtitle)
            row.set_active(active)
            return row
        # Fallback for libadwaita < 1.4
        row = Adw.ActionRow(title=title, subtitle=subtitle)
        sw = Gtk.Switch(valign=Gtk.Align.CENTER, active=active)
        row.add_suffix(sw)
        row.set_activatable_widget(sw)
        row._switch = sw  # type: ignore[attr-defined]
        return row

    @staticmethod
    def _switch_row_get(row) -> bool:
        if hasattr(row, "get_active"):
            try:
                return bool(row.get_active())
            except Exception:
                pass
        sw = getattr(row, "_switch", None)
        return bool(sw.get_active()) if sw is not None else False

    # ------------------------------------------------------------------
    # Save + close
    # ------------------------------------------------------------------

    def _on_close(self, *_args) -> bool:
        a = self._account
        a.server = self._row_server.get_text().strip()
        a.domain = self._row_domain.get_text().strip()
        a.transport = _TRANSPORTS[self._row_transport.get_selected()]
        a.username = self._row_username.get_text().strip()
        a.password = self._row_password.get_text()
        a.auth_id = self._row_auth_id.get_text().strip()
        a.name = self._row_name.get_text().strip()
        a.display_name = self._row_display.get_text().strip()
        a.enabled = self._switch_row_get(self._row_enabled)
        try:
            self._on_save(a)
        except Exception:
            import traceback
            traceback.print_exc()
        return False
