"""Main application window.

Mirrors MicroSIP's main window layout — a tabbed view with Dialer,
Contacts, Calls (history) and Messages — using native GNOME widgets
(Adw.ViewStack + Adw.ViewSwitcher) instead of the Win32 tab control.
"""

from __future__ import annotations

import logging

import gi

gi.require_version("Gtk", "4.0")

_USE_ADW = False
try:
    gi.require_version("Adw", "1")
    from gi.repository import Adw  # noqa: E402
    _USE_ADW = True
except (ValueError, ImportError):
    pass

from gi.repository import Gio, Gtk  # noqa: E402

from .. import log as gslog
from ..dialogs.account_dialog import AccountDialog
from ..dialogs.log_dialog import LogDialog
from ..sip.endpoint import PJSUA2_IMPORT_ERROR, SipEndpoint
from ..storage.settings import load_settings, save_settings


logger = logging.getLogger(__name__)
from .dialer_view import DialerView
from .contacts_view import ContactsView
from .calls_view import CallsView
from .messages_view import MessagesView


_BaseWindow = Adw.ApplicationWindow if _USE_ADW else Gtk.ApplicationWindow


class MainWindow(_BaseWindow):
    def __init__(self, app) -> None:
        super().__init__(application=app, title="gsipper")
        self.set_default_size(360, 560)
        self.set_icon_name("gsipper")

        self._settings = load_settings()
        self._sip = SipEndpoint.get()
        self._sip.set_reg_handler(self._on_reg_state)

        self._install_actions(app)
        menu_model = self._build_menu_model()

        self.dialer = DialerView()
        self.contacts = ContactsView()
        self.calls = CallsView()
        self.messages = MessagesView()

        if _USE_ADW:
            self._build_adw_layout(menu_model)
        else:
            self._build_fallback_layout(menu_model)

        app.set_accels_for_action("win.quit", ["<Control>q"])
        app.set_accels_for_action("win.close", ["<Control>w"])
        app.set_accels_for_action("win.account", ["<Control>comma"])

        self.connect("close-request", self._on_window_close)

        # Kick off registration with whatever's already on disk.
        self._apply_account_settings()

    def _build_adw_layout(self, menu_model: Gio.MenuModel) -> None:
        header = Adw.HeaderBar()

        menu_button = Gtk.MenuButton(icon_name="open-menu-symbolic")
        menu_button.set_menu_model(menu_model)
        header.pack_end(menu_button)

        # Tiny coloured dot: green = online, yellow = connecting,
        # red = offline / error. Real text goes in the tooltip.
        self._status_dot = Gtk.Box()
        self._status_dot.set_valign(Gtk.Align.CENTER)
        self._status_dot.set_size_request(12, 12)
        self._status_dot.add_css_class("status-dot")
        self._status_dot.add_css_class("offline")
        header.pack_start(self._status_dot)

        stack = Adw.ViewStack()
        stack.add_titled_with_icon(self.dialer, "dialer", "Dialer", "input-dialpad-symbolic")
        stack.add_titled_with_icon(self.contacts, "contacts", "Contacts", "system-users-symbolic")
        stack.add_titled_with_icon(self.calls, "calls", "Calls", "call-start-symbolic")
        stack.add_titled_with_icon(self.messages, "messages", "Messages", "mail-unread-symbolic")
        self._stack = stack

        header.set_title_widget(Adw.WindowTitle(title="", subtitle=""))

        switcher_bar = Adw.ViewSwitcherBar()
        switcher_bar.set_stack(stack)
        switcher_bar.set_reveal(True)

        toolbar_view = Adw.ToolbarView()
        toolbar_view.add_top_bar(header)
        toolbar_view.set_content(stack)
        toolbar_view.add_bottom_bar(switcher_bar)
        self.set_content(toolbar_view)

    def _build_fallback_layout(self, menu_model: Gio.MenuModel) -> None:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        box.append(Gtk.PopoverMenuBar.new_from_model(menu_model))

        notebook = Gtk.Notebook()
        notebook.append_page(self.dialer, Gtk.Label(label="Dialer"))
        notebook.append_page(self.contacts, Gtk.Label(label="Contacts"))
        notebook.append_page(self.calls, Gtk.Label(label="Calls"))
        notebook.append_page(self.messages, Gtk.Label(label="Messages"))
        notebook.set_vexpand(True)
        box.append(notebook)
        self.set_child(box)

    def _install_actions(self, app) -> None:
        for name, handler in {
            "account": self._action_account,
            "log": self._action_log,
            "about": self._action_about,
            "quit": self._action_quit,
            "close": self._action_close,
        }.items():
            action = Gio.SimpleAction.new(name, None)
            action.connect("activate", handler)
            self.add_action(action)

    def _build_menu_model(self) -> Gio.Menu:
        menu = Gio.Menu()

        account_section = Gio.Menu()
        account_section.append("Account…", "win.account")
        menu.append_section(None, account_section)

        tools_section = Gio.Menu()
        tools_section.append("Log…", "win.log")
        menu.append_section(None, tools_section)

        meta_section = Gio.Menu()
        meta_section.append("About gsipper", "win.about")
        meta_section.append("Quit", "win.quit")
        menu.append_section(None, meta_section)
        return menu

    def _action_account(self, *_args) -> None:
        if not _USE_ADW:
            self._toast("Account dialog requires libadwaita")
            return
        dialog = AccountDialog(
            parent=self,
            account=self._settings.account,
            on_save=self._on_account_saved,
        )
        dialog.present()

    def _action_log(self, *_args) -> None:
        if not _USE_ADW:
            self._toast("Log viewer requires libadwaita")
            return
        LogDialog(parent=self).present()

    def _action_settings(self, *_args) -> None:
        # TODO step 8: Adw.PreferencesWindow with audio / codecs / network
        self._toast("Settings: coming in step 8")

    def _on_account_saved(self, _account) -> None:
        save_settings(self._settings)
        self._apply_account_settings()

    def _apply_account_settings(self) -> None:
        if not self._sip.available:
            tip = (
                "python3-pjsua2 could not be imported. "
                "Open the Log… menu for the full traceback."
            )
            if PJSUA2_IMPORT_ERROR:
                tip += f"\n\n{PJSUA2_IMPORT_ERROR}"
            logger.error("SIP backend missing: %s",
                         PJSUA2_IMPORT_ERROR or "module not found")
            self._set_status("offline", tooltip=tip)
            return
        if not self._settings.account.enabled:
            self._set_status("offline", tooltip="Account disabled")
            try:
                self._sip.configure_account(self._settings.account)
            except Exception:
                pass
            return
        self._set_status("connecting", tooltip="Registering…")
        try:
            self._sip.configure_account(self._settings.account)
        except Exception as exc:
            self._set_status("offline", tooltip=str(exc))

    def _on_reg_state(self, active: bool, code: int, reason: str) -> None:
        logger.info("status: active=%s code=%s reason=%s", active, code, reason)
        if active:
            tip = "Online"
            codec_tip = self._codec_tooltip()
            if codec_tip:
                tip += "\n" + codec_tip
            self._set_status("online", tooltip=tip)
        elif code >= 400:
            self._set_status("offline", tooltip=f"Error {code}: {reason}")
        elif not self._settings.account.enabled:
            self._set_status("offline", tooltip="Account disabled")
        else:
            self._set_status("connecting",
                             tooltip=f"Registering… {reason}" if reason else "Registering…")

    def _codec_tooltip(self) -> str:
        enabled = self._sip.enabled_codecs
        unavail = self._sip.unavailable_codecs
        parts = []
        if enabled:
            parts.append("Enabled codecs: " + ", ".join(enabled))
        if unavail:
            parts.append("Unavailable: " + ", ".join(unavail))
        return "\n".join(parts) if parts else ""

    def _set_status(self, state: str, tooltip: str | None = None) -> None:
        """state: 'online', 'connecting', or 'offline'."""
        if not hasattr(self, "_status_dot"):
            return
        dot = self._status_dot
        for c in ("online", "connecting", "offline"):
            dot.remove_css_class(c)
        dot.add_css_class(state)
        dot.set_tooltip_text(tooltip or state.capitalize())

    def _on_window_close(self, *_args) -> bool:
        try:
            self._sip.shutdown()
        except Exception:
            pass
        return False

    def _action_about(self, *_args) -> None:
        if _USE_ADW:
            about = Adw.AboutWindow(
                transient_for=self,
                application_name="gsipper",
                application_icon="gsipper",
                version="0.1.0",
                developer_name="pulpoff",
                license_type=Gtk.License.GPL_2_0,
                website="https://github.com/pulpoff/gsipper",
                comments="Modern GNOME SIP client",
            )
            about.present()

    def _action_quit(self, *_args) -> None:
        self.get_application().quit()

    def _action_close(self, *_args) -> None:
        self.close()

    def _toast(self, text: str) -> None:
        # Toasts need an Adw.ToastOverlay; wire one in later.
        logger.info("%s", text)

    def handle_call_uri(self, uri: str) -> None:
        """Honor tel:/sip: command-line arg by pre-filling the dialer."""
        number = uri.split(":", 1)[1] if ":" in uri else uri
        self.dialer.set_number(number)
        if _USE_ADW and hasattr(self, "_stack"):
            self._stack.set_visible_child_name("dialer")
