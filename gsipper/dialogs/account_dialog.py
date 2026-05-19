"""Account preferences dialogs.

Two-pane design modelled on MicroSIP's "Account" / "Advanced" split:

- AccountDialog: the essentials (SIP server, username, password,
  online switch). Has a "More options" row that pops up...
- AdvancedAccountDialog: identity overrides (display name, auth ID,
  domain, account name), transport, STUN, and a per-codec list where
  each row can be toggled on/off and reordered up/down to change
  negotiation priority.

Both dialogs mutate the same AccountSettings dataclass in-place;
the parent persists once the main dialog closes.
"""

from __future__ import annotations

from typing import Callable, List, Optional

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk  # noqa: E402

from ..storage.settings import AccountSettings


_TRANSPORTS = ["UDP", "TCP", "TLS"]


# ----------------------------------------------------------------------
# Shared row helpers
# ----------------------------------------------------------------------

def _entry_row(title: str, value: str) -> Adw.EntryRow:
    row = Adw.EntryRow(title=title)
    row.set_text(value or "")
    return row


def _password_row(title: str, value: str) -> Adw.PasswordEntryRow:
    row = Adw.PasswordEntryRow(title=title)
    row.set_text(value or "")
    return row


def _combo_row(title: str, options, value: str) -> Adw.ComboRow:
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


def _switch_row(title: str, subtitle: str, active: bool):
    if hasattr(Adw, "SwitchRow"):
        row = Adw.SwitchRow(title=title, subtitle=subtitle)
        row.set_active(active)
        return row
    row = Adw.ActionRow(title=title, subtitle=subtitle)
    sw = Gtk.Switch(valign=Gtk.Align.CENTER, active=active)
    row.add_suffix(sw)
    row.set_activatable_widget(sw)
    row._switch = sw  # type: ignore[attr-defined]
    return row


def _switch_row_value(row) -> bool:
    if hasattr(row, "get_active"):
        try:
            return bool(row.get_active())
        except Exception:
            pass
    sw = getattr(row, "_switch", None)
    return bool(sw.get_active()) if sw is not None else False


# ----------------------------------------------------------------------
# Main account dialog
# ----------------------------------------------------------------------

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
        self.set_default_size(480, 460)

        self._account = account
        self._on_save = on_save

        page = Adw.PreferencesPage()

        login_group = Adw.PreferencesGroup(title="Login")
        self._row_server = _entry_row("SIP server", account.server)
        self._row_username = _entry_row("Username", account.username)
        self._row_password = _password_row("Password", account.password)
        for r in (self._row_server, self._row_username, self._row_password):
            login_group.add(r)
        page.add(login_group)

        state_group = Adw.PreferencesGroup()
        self._row_enabled = _switch_row(
            "Online", "Register and stay online", account.enabled,
        )
        state_group.add(self._row_enabled)

        more_row = Adw.ActionRow(title="More options")
        more_row.set_subtitle("Transport, STUN, identity overrides, codecs")
        more_row.set_activatable(True)
        more_row.add_suffix(Gtk.Image.new_from_icon_name("go-next-symbolic"))
        more_row.connect("activated", lambda *_: self._open_advanced())
        state_group.add(more_row)
        page.add(state_group)

        self.add(page)
        self.connect("close-request", self._on_close)

    def _open_advanced(self) -> None:
        # Sync current edits into the dataclass first so the advanced
        # dialog sees fresh values for the basic fields.
        self._sync_basic()
        dlg = AdvancedAccountDialog(self, self._account)
        dlg.present()

    def _sync_basic(self) -> None:
        a = self._account
        a.server = self._row_server.get_text().strip()
        a.username = self._row_username.get_text().strip()
        a.password = self._row_password.get_text()
        a.enabled = _switch_row_value(self._row_enabled)

    def _on_close(self, *_args) -> bool:
        self._sync_basic()
        try:
            self._on_save(self._account)
        except Exception:
            import traceback
            traceback.print_exc()
        return False


# ----------------------------------------------------------------------
# Advanced account dialog
# ----------------------------------------------------------------------

class AdvancedAccountDialog(Adw.PreferencesWindow):
    def __init__(
        self,
        parent: Gtk.Window,
        account: AccountSettings,
    ) -> None:
        super().__init__()
        self.set_title("Advanced")
        self.set_transient_for(parent)
        self.set_modal(True)
        self.set_search_enabled(False)
        self.set_default_size(560, 820)

        self._account = account
        # Work on a copy so unsaved changes don't leak if the user
        # force-closes the parent without coming back through here.
        self._codecs: List[dict] = [dict(c) for c in account.codecs]
        self._codec_rows: List[Adw.ActionRow] = []

        page = Adw.PreferencesPage()

        identity = Adw.PreferencesGroup(title="Identity")
        self._row_display = _entry_row("Display name", account.display_name)
        self._row_name = _entry_row("Account name", account.name)
        self._row_auth_id = _entry_row("Auth ID", account.auth_id)
        self._row_domain = _entry_row("Domain", account.domain)
        for r in (self._row_display, self._row_name, self._row_auth_id, self._row_domain):
            identity.add(r)
        page.add(identity)

        network = Adw.PreferencesGroup(title="Network")
        self._row_transport = _combo_row("Transport", _TRANSPORTS, account.transport)
        self._row_stun = _entry_row("STUN server", account.stun_server)
        for r in (self._row_transport, self._row_stun):
            network.add(r)
        page.add(network)

        self._codecs_group = Adw.PreferencesGroup(
            title="Codecs",
            description="Drag priority with the up/down buttons. "
                        "Disabled codecs are not offered in SDP.",
        )
        self._rebuild_codec_rows()
        page.add(self._codecs_group)

        self.add(page)
        self.connect("close-request", self._on_close)

    # ------------------------------------------------------------------
    # Codec list
    # ------------------------------------------------------------------

    def _rebuild_codec_rows(self) -> None:
        for row in self._codec_rows:
            self._codecs_group.remove(row)
        self._codec_rows.clear()

        total = len(self._codecs)
        for index, codec in enumerate(self._codecs):
            row = self._make_codec_row(codec, index, total)
            self._codecs_group.add(row)
            self._codec_rows.append(row)

    def _make_codec_row(self, codec: dict, index: int, total: int) -> Adw.ActionRow:
        row = Adw.ActionRow(title=codec["name"])
        row.set_subtitle(codec["id"])

        up = Gtk.Button(icon_name="go-up-symbolic", valign=Gtk.Align.CENTER)
        up.add_css_class("flat")
        up.set_tooltip_text("Move up")
        up.set_sensitive(index > 0)
        up.connect("clicked", lambda *_: self._move(index, -1))

        down = Gtk.Button(icon_name="go-down-symbolic", valign=Gtk.Align.CENTER)
        down.add_css_class("flat")
        down.set_tooltip_text("Move down")
        down.set_sensitive(index < total - 1)
        down.connect("clicked", lambda *_: self._move(index, 1))

        sw = Gtk.Switch(valign=Gtk.Align.CENTER, active=bool(codec.get("enabled", True)))
        sw.connect("notify::active",
                   lambda s, *_: self._set_enabled(index, s.get_active()))

        row.add_suffix(up)
        row.add_suffix(down)
        row.add_suffix(sw)
        return row

    def _move(self, index: int, delta: int) -> None:
        new_index = index + delta
        if not (0 <= new_index < len(self._codecs)):
            return
        self._codecs[index], self._codecs[new_index] = \
            self._codecs[new_index], self._codecs[index]
        self._rebuild_codec_rows()

    def _set_enabled(self, index: int, value: bool) -> None:
        self._codecs[index]["enabled"] = value

    # ------------------------------------------------------------------
    # Save + close
    # ------------------------------------------------------------------

    def _on_close(self, *_args) -> bool:
        a = self._account
        a.display_name = self._row_display.get_text().strip()
        a.name = self._row_name.get_text().strip()
        a.auth_id = self._row_auth_id.get_text().strip()
        a.domain = self._row_domain.get_text().strip()
        a.transport = _TRANSPORTS[self._row_transport.get_selected()]
        a.stun_server = self._row_stun.get_text().strip()
        a.codecs = self._codecs
        return False
