"""Main application window.

Mirrors MicroSIP's main window layout — a tabbed view with Dialer,
Contacts, Calls (history) and Messages — using native GNOME widgets
(Adw.ViewStack + Adw.ViewSwitcher) instead of the Win32 tab control.
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

from gi.repository import Gio, Gtk  # noqa: E402

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
        app.set_accels_for_action("win.settings", ["<Control>p"])

    def _build_adw_layout(self, menu_model: Gio.MenuModel) -> None:
        header = Adw.HeaderBar()

        menu_button = Gtk.MenuButton(icon_name="open-menu-symbolic")
        menu_button.set_menu_model(menu_model)
        header.pack_end(menu_button)

        self._status_label = Gtk.Label(label="Offline")
        self._status_label.add_css_class("dim-label")
        header.pack_start(self._status_label)

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
            "settings": self._action_settings,
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
        account_section.append("Settings…", "win.settings")
        menu.append_section(None, account_section)

        meta_section = Gio.Menu()
        meta_section.append("About gsipper", "win.about")
        meta_section.append("Quit", "win.quit")
        menu.append_section(None, meta_section)
        return menu

    def _action_account(self, *_args) -> None:
        # TODO step 2: Account dialog
        self._toast("Account configuration: coming in step 2")

    def _action_settings(self, *_args) -> None:
        # TODO step 8: Adw.PreferencesWindow
        self._toast("Settings: coming in step 8")

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
        if not _USE_ADW:
            return
        # Toasts need an Adw.ToastOverlay; wire one in later.
        print(f"[gsipper] {text}")

    def handle_call_uri(self, uri: str) -> None:
        """Honor tel:/sip: command-line arg by pre-filling the dialer."""
        number = uri.split(":", 1)[1] if ":" in uri else uri
        self.dialer.set_number(number)
        if _USE_ADW and hasattr(self, "_stack"):
            self._stack.set_visible_child_name("dialer")
