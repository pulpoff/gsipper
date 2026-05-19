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

from gi.repository import GLib, Gio, Gtk  # noqa: E402

from .. import __version__
from .. import log as gslog
from ..dialogs.account_dialog import AccountDialog
from ..dialogs.log_dialog import LogDialog
# SipEndpoint and PJSUA2_IMPORT_ERROR are imported lazily inside
# _init_sip so the 40 MB pjsua2 shared library doesn't load until
# the first idle slot after the window is painted.
from ..sound import Ringer
from ..storage.settings import load_settings, save_settings
from ..tray import TrayIndicator
from .dialer_view import DialerView
from .contacts_view import ContactsView
from .calls_view import CallsView
from .messages_view import MessagesView
from .ringin_window import RinginWindow


logger = logging.getLogger(__name__)


_BaseWindow = Adw.ApplicationWindow if _USE_ADW else Gtk.ApplicationWindow


class MainWindow(_BaseWindow):
    def __init__(self, app) -> None:
        super().__init__(application=app, title="gsipper")
        self.set_default_size(360, 560)
        self.set_icon_name("gsipper")

        self._settings = load_settings()
        # SIP endpoint is created lazily in _init_sip via GLib.idle_add
        # so the window paints before the pjsua2 module loads.
        self._sip = None  # type: ignore[assignment]
        # Latched when the user picks Disconnect from the status menu
        # so the subsequent active=False onRegState (carrying the
        # trunk's 200 OK to our un-REGISTER) doesn't flap the dot back
        # to yellow / "Registering…".
        self._user_disconnected = False
        self._tray = TrayIndicator()
        self._ringer = Ringer()
        self._ringin_window: RinginWindow | None = None
        self._incoming_notification_id = "gsipper-incoming"
        # Count of missed calls since the user last visited the Calls tab.
        # Reset by _on_view_switched / the Calls action.
        self._missed_calls: int = 0

        self._install_actions(app)
        menu_model = self._build_menu_model()

        self.dialer = DialerView()
        self.dialer.connect("call-requested", self._on_dial_requested)
        self.dialer.connect("hangup-requested", self._on_hangup_requested)
        self.contacts = ContactsView()
        self.contacts.connect("call-requested", self._on_contact_call)
        self.calls = CallsView()
        self.calls.connect("redial-requested", self._on_redial_requested)
        self.messages = MessagesView()
        self.messages.connect("send-message", self._on_messages_send)
        self.messages.connect("call-peer", self._on_messages_call)

        if _USE_ADW:
            self._build_adw_layout(menu_model)
        else:
            self._build_fallback_layout(menu_model)

        app.set_accels_for_action("win.quit", ["<Control>q"])
        app.set_accels_for_action("win.close", ["<Control>w"])
        app.set_accels_for_action("win.account", ["<Control>comma"])

        self.connect("close-request", self._on_window_close)

        # Surface the 'connecting' state immediately so the headerbar
        # dot, the tray indicator and the shell-extension D-Bus
        # property reflect the right colour from frame 1 — before SIP
        # has done any work. Yellow if we have something to register
        # with, red otherwise.
        acct = self._settings.account
        if acct.enabled and acct.server and acct.username:
            self._set_status("connecting", tooltip="Registering…")
        else:
            self._set_status("offline", tooltip="Not configured")

        # Defer pjsua2 import + SIP setup to the next idle slot. That
        # lets GTK paint the window, the tray icon and the extension
        # before the 40 MB pjsua2 .so loads and before libCreate /
        # libInit / transportCreate / REGISTER fire on the worker.
        GLib.idle_add(self._init_sip)

    def _build_adw_layout(self, menu_model: Gio.MenuModel) -> None:
        header = Adw.HeaderBar()

        menu_button = Gtk.MenuButton(icon_name="open-menu-symbolic")
        menu_button.set_menu_model(menu_model)
        header.pack_end(menu_button)

        # Tiny coloured dot: green = online, yellow = connecting,
        # red = offline / error. Real text goes in the tooltip.
        # Both alignments must be CENTER so the headerbar doesn't
        # stretch the box vertically into an oval.
        self._status_dot = Gtk.Box(
            halign=Gtk.Align.CENTER,
            valign=Gtk.Align.CENTER,
            hexpand=False,
            vexpand=False,
        )
        self._status_dot.set_size_request(12, 12)
        self._status_dot.add_css_class("status-dot")
        self._status_dot.add_css_class("offline")
        # The dot is the child of a flat MenuButton so clicking it
        # opens a state-dependent menu (Disconnect/Reconnect/Exit when
        # online, Connect/Exit when offline). _update_status_menu
        # rebuilds the model on every state transition.
        self._status_btn = Gtk.MenuButton()
        self._status_btn.set_child(self._status_dot)
        self._status_btn.add_css_class("flat")
        self._status_btn.set_tooltip_text("Offline")
        self._update_status_menu("offline")
        header.pack_start(self._status_btn)

        stack = Adw.ViewStack()
        stack.add_titled_with_icon(self.dialer, "dialer", "Dialer", "input-dialpad-symbolic")
        stack.add_titled_with_icon(self.contacts, "contacts", "Contacts", "system-users-symbolic")
        stack.add_titled_with_icon(self.calls, "calls", "Calls", "call-start-symbolic")
        self._stack = stack
        self._apply_messages_visibility()
        stack.connect("notify::visible-child-name", self._on_view_switched)

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
        if self._settings.general.enable_messages:
            notebook.append_page(self.messages, Gtk.Label(label="Messages"))
        self._notebook = notebook
        notebook.set_vexpand(True)
        box.append(notebook)
        self.set_child(box)

    def _install_actions(self, app) -> None:
        for name, handler in {
            "account":    self._action_account,
            "settings":   self._action_settings,
            "log":        self._action_log,
            "about":      self._action_about,
            "quit":       self._action_quit,
            "close":      self._action_close,
            "connect":    self._action_connect,
            "disconnect": self._action_disconnect,
            "reconnect":  self._action_reconnect,
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
        if not _USE_ADW:
            self._toast("Settings dialog requires libadwaita")
            return
        from ..dialogs.settings_dialog import SettingsDialog
        SettingsDialog(self._settings, on_save=self._on_general_saved).present(self)

    def _on_general_saved(self, general) -> None:
        self._settings.general = general
        save_settings(self._settings)
        self._apply_messages_visibility()

    def _apply_messages_visibility(self) -> None:
        """Add / remove the Messages tab depending on the
        general.enable_messages toggle. MessagesView itself stays
        constructed either way, so toggling preserves stored chats."""
        enabled = self._settings.general.enable_messages
        in_view = self.messages.get_parent() is not None
        if hasattr(self, "_stack"):
            if enabled and not in_view:
                self._stack.add_titled_with_icon(
                    self.messages, "messages",
                    "Messages", "mail-unread-symbolic")
            elif not enabled and in_view:
                # If the user happened to be viewing it, switch to Dialer.
                if self._stack.get_visible_child_name() == "messages":
                    self._stack.set_visible_child_name("dialer")
                self._stack.remove(self.messages)
        elif hasattr(self, "_notebook"):
            if enabled and not in_view:
                self._notebook.append_page(
                    self.messages, Gtk.Label(label="Messages"))
            elif not enabled and in_view:
                idx = self._notebook.page_num(self.messages)
                if idx != -1:
                    self._notebook.remove_page(idx)

    def _on_account_saved(self, _account) -> None:
        save_settings(self._settings)
        self._apply_account_settings()

    def _init_sip(self) -> bool:
        """Idle-time hook: import pjsua2, create the SipEndpoint, wire
        callbacks, then kick off the first registration. Runs exactly
        once; further changes go through _apply_account_settings."""
        from ..sip.endpoint import SipEndpoint
        self._sip = SipEndpoint.get()
        self._sip.set_reg_handler(self._on_reg_state)
        self._sip.set_call_state_handler(self._on_call_state)
        self._sip.set_message_handler(self._on_sip_message)
        self._sip.set_message_status_handler(self._on_sip_message_status)
        self._apply_account_settings()
        return False  # one-shot

    def _apply_account_settings(self) -> None:
        if self._sip is None:
            # Called before _init_sip (e.g. by an account-saved hook).
            # Schedule it to run after SIP comes up.
            GLib.idle_add(self._apply_account_settings)
            return
        if not self._sip.available:
            from ..sip.endpoint import PJSUA2_IMPORT_ERROR
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
            # Any successful REGISTER clears the user-disconnect latch.
            self._user_disconnected = False
            tip = "Online"
            codec_tip = self._codec_tooltip()
            if codec_tip:
                tip += "\n" + codec_tip
            self._set_status("online", tooltip=tip)
            self._tray.set_state("online")
            self._publish_dbus_status("online")
        elif self._user_disconnected:
            # User clicked Disconnect; don't flap back to yellow when
            # the trunk's 200 OK to our un-REGISTER arrives.
            self._set_status("offline", tooltip="Disconnected")
            self._tray.set_state("offline")
            self._publish_dbus_status("offline")
        elif code >= 400:
            self._set_status("offline", tooltip=f"Error {code}: {reason}")
            self._tray.set_state("offline")
            self._publish_dbus_status("offline")
        elif not self._settings.account.enabled:
            self._set_status("offline", tooltip="Account disabled")
            self._tray.set_state("offline")
            self._publish_dbus_status("offline")
        else:
            self._set_status("connecting",
                             tooltip=f"Registering… {reason}" if reason else "Registering…")
            self._tray.set_state("connecting")
            self._publish_dbus_status("connecting")

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
        tip = tooltip or state.capitalize()
        dot.set_tooltip_text(tip)
        if hasattr(self, "_status_btn"):
            self._status_btn.set_tooltip_text(tip)
            self._update_status_menu(state)

    def _update_status_menu(self, state: str) -> None:
        """Rebuild the dropdown menu shown on the headerbar dot."""
        menu = Gio.Menu()
        if state == "online":
            menu.append("Disconnect", "win.disconnect")
            menu.append("Reconnect", "win.reconnect")
        else:
            # 'connecting' and 'offline' both expose Connect — useful
            # if the user wants to force a fresh REGISTER instead of
            # waiting out a retry backoff.
            menu.append("Connect", "win.connect")
        menu.append("Exit", "win.quit")
        if hasattr(self, "_status_btn"):
            self._status_btn.set_menu_model(menu)

    # ------------------------------------------------------------------
    # Status-menu actions
    # ------------------------------------------------------------------

    def _action_connect(self, *_args) -> None:
        if self._sip is None:
            return
        self._user_disconnected = False
        self._set_status("connecting", tooltip="Connecting…")
        self._sip.set_registration(True)

    def _action_disconnect(self, *_args) -> None:
        if self._sip is None:
            return
        # Latch + paint red immediately so the dot doesn't flap to
        # yellow when the trunk's 200 OK to our un-REGISTER arrives.
        self._user_disconnected = True
        self._set_status("offline", tooltip="Disconnected")
        self._tray.set_state("offline")
        self._publish_dbus_status("offline")
        self._sip.set_registration(False)

    def _action_reconnect(self, *_args) -> None:
        # 'setRegistration(True)' on an already-registered account
        # often short-circuits inside PJSIP — it returns the cached
        # 200 OK without putting a fresh REGISTER on the wire (user
        # log: setRegistration(True) submitted -> active=True 200 OK
        # in the same wall-clock second). Force a real reconnect by
        # going through the full configure_account path: it unregisters
        # the existing account, tears it down, builds a new pjsua2
        # Account and registers fresh.
        if self._sip is None:
            return
        self._user_disconnected = False
        self._set_status("connecting", tooltip="Reconnecting…")
        self._sip.configure_account(self._settings.account)

    def _on_window_close(self, *_args) -> bool:
        """X button: hide to tray. SIP keeps running so we still ring on
        incoming calls. Real quit goes through win.quit / Ctrl+Q / the
        tray-extension menu, which call app.quit() → do_shutdown."""
        self.set_visible(False)
        return True  # inhibit destroy

    # ------------------------------------------------------------------
    # Outgoing calls
    # ------------------------------------------------------------------

    def _on_dial_requested(self, _dialer, number: str) -> None:
        if self._sip is None or not self._sip.available:
            self._toast("SIP backend unavailable")
            return
        uri = self._build_dial_uri(number)
        logger.info("dial: %s -> %s", number, uri)
        # Fire-and-forget — the SIP worker thread will dispatch and call
        # state will arrive via _on_call_state. Show the in-call view
        # optimistically so the user sees instant feedback.
        self._sip.make_call(uri)
        self.dialer.show_call(peer=number, state="calling")
        if _USE_ADW and hasattr(self, "_stack"):
            self._stack.set_visible_child_name("dialer")

    def _on_hangup_requested(self, *_args) -> None:
        if self._sip is None:
            return
        self._sip.hangup_active()

    def _dialer_friendly(self, target: str) -> str:
        """Strip 'sip:' / '@domain' off a target so what lands in the
        dialer entry is human-editable digits (or the user-part). The
        real SIP URI is rebuilt against the registered account at dial
        time by _build_dial_uri."""
        s = (target or "").strip()
        for scheme in ("sip:", "sips:", "tel:"):
            if s.startswith(scheme):
                s = s[len(scheme):]
                break
        if "@" in s:
            s = s.split("@", 1)[0]
        return s or target

    def _on_contact_call(self, _view, target: str) -> None:
        """ContactsView.call-requested: route through the dialer flow."""
        number = self._dialer_friendly(target)
        self.dialer.set_number(number)
        self._on_dial_requested(self.dialer, number)

    def _on_redial_requested(self, _view, target: str) -> None:
        """Calls history row activated: pre-fill the dialer and switch
        to the Dialer tab so the user can review and press Call."""
        self.dialer.set_number(self._dialer_friendly(target))
        if _USE_ADW and hasattr(self, "_stack"):
            self._stack.set_visible_child_name("dialer")

    # ------------------------------------------------------------------
    # SIP messages
    # ------------------------------------------------------------------

    def _on_sip_message(self, from_uri: str, body: str, _content_type: str) -> None:
        logger.info("incoming MESSAGE from %s", from_uri)
        self.messages.on_incoming_message(
            from_uri, body, display_resolver=self._lookup_contact_display,
        )

    def _on_sip_message_status(self, message_id: str, code: int, reason: str) -> None:
        self.messages.on_message_status(message_id, code, reason)

    def _on_messages_send(self, _view, peer_uri: str, body: str, message_id: str) -> None:
        if self._sip is None or \
                not self._sip.send_message(peer_uri, body, message_id=message_id):
            # pjsua2 entirely unavailable (not even queued). Mark
            # the outgoing bubble as failed immediately.
            self.messages.on_message_status(message_id, 500, "SIP unavailable")

    def _on_messages_call(self, _view, peer_uri: str) -> None:
        # Re-use the existing dial path so we get the same URI rewriting
        # ('+' -> '00') and the in-call view swap. Show the number only
        # (no 'sip:.../@host') in the dialer so the user can edit it.
        number = self._dialer_friendly(peer_uri)
        self.dialer.set_number(number)
        self._on_dial_requested(self.dialer, number)

    def _lookup_contact_display(self, peer_uri: str) -> str:
        """Best-effort: look up a stored contact whose SIP URI or phone
        matches `peer_uri`, return its display name. Falls back to ''."""
        try:
            from ..storage.contacts import load_contacts
            from ..storage.messages import normalise_uri
            canonical = normalise_uri(peer_uri)
            user_part = canonical.split("@", 1)[0] if "@" in canonical else canonical
            digits = "".join(ch for ch in user_part if ch.isdigit())
            for c in load_contacts():
                if c.sip_uri and normalise_uri(c.sip_uri) == canonical:
                    return c.name
                for phone in c.phones:
                    num_digits = "".join(ch for ch in phone.get("number", "") if ch.isdigit())
                    if digits and num_digits and digits == num_digits:
                        return c.name
        except Exception:
            pass
        return ""

    def _on_call_state(self, call, state: str) -> None:
        logger.info("UI call state: %s peer=%s", state,
                    getattr(call, "peer_display", ""))

        if state == "incoming":
            self._open_ringin(call)
            self._emit_dbus_incoming(getattr(call, "peer_display", ""))
            return

        if state == "ended":
            self._close_ringin()
            self._ringer.stop()
            self._withdraw_incoming_notification()
            self.dialer.show_keypad()
            self.calls.refresh()
            # If this was an unanswered incoming, bump the missed badge.
            if (getattr(call, "incoming", False)
                    and getattr(call, "connected_at", None) is None
                    and int(getattr(call, "last_status_code", 0)) not in (486, 603)):
                self._missed_calls += 1
                self._publish_dbus_missed()
            return

        # calling / ringing / connected
        if call is not None and getattr(call, "incoming", False) and state in ("ringing", "connected"):
            # We answered an incoming call — tear down the ring-in
            # popup and ringer; the in-call view takes over.
            self._close_ringin()
            self._ringer.stop()
            self._withdraw_incoming_notification()
        peer = getattr(call, "peer_display", "") or "—"
        self.dialer.show_call(peer=peer, state=state)
        if _USE_ADW and hasattr(self, "_stack"):
            self._stack.set_visible_child_name("dialer")

    # ------------------------------------------------------------------
    # Ring-in popup + notification
    # ------------------------------------------------------------------

    def _open_ringin(self, call) -> None:
        if self._ringin_window is not None:
            return
        peer_display = getattr(call, "peer_display", "") or "Unknown caller"
        peer_uri = getattr(call, "peer_uri", "") or ""
        win = RinginWindow(parent=self, peer_display=peer_display, peer_uri=peer_uri)
        win.connect("answer-requested", self._on_ringin_answer)
        win.connect("decline-requested", self._on_ringin_decline)
        self._ringin_window = win
        win.present()
        self._ringer.start()
        self._send_incoming_notification(peer_display)

    def _close_ringin(self) -> None:
        win = self._ringin_window
        if win is None:
            return
        self._ringin_window = None
        try:
            win.close()
        except Exception:
            pass

    def _on_ringin_answer(self, *_args) -> None:
        if self._sip is None:
            return
        self._sip.answer_active()

    def _on_ringin_decline(self, *_args) -> None:
        if self._sip is None:
            return
        # 486 Busy Here = explicit decline.
        self._sip.hangup_active(486)

    def _send_incoming_notification(self, peer_display: str) -> None:
        app = self.get_application()
        if app is None:
            return
        try:
            notif = Gio.Notification.new("Incoming call")
            notif.set_body(peer_display or "Unknown caller")
            notif.set_priority(Gio.NotificationPriority.URGENT)
            notif.set_icon(Gio.ThemedIcon.new("call-start-symbolic"))
            notif.add_button("Answer", "app.answer-incoming")
            notif.add_button("Decline", "app.decline-incoming")
            notif.set_default_action("app.show-main")
            app.send_notification(self._incoming_notification_id, notif)
        except Exception as exc:
            logger.warning("notification send failed: %s", exc)

    def _withdraw_incoming_notification(self) -> None:
        app = self.get_application()
        if app is None:
            return
        try:
            app.withdraw_notification(self._incoming_notification_id)
        except Exception:
            pass

    # ------------------------------------------------------------------
    # D-Bus status service (consumed by gsipper@pulpoff.com extension)
    # ------------------------------------------------------------------

    def _dbus_service(self):
        app = self.get_application()
        if app is None:
            return None
        return getattr(app, "status_service", None)

    def _publish_dbus_status(self, status: str) -> None:
        svc = self._dbus_service()
        if svc is not None:
            svc.set_status(status)

    def _publish_dbus_missed(self) -> None:
        svc = self._dbus_service()
        if svc is not None:
            svc.set_missed_calls(self._missed_calls)

    def _emit_dbus_incoming(self, peer: str) -> None:
        svc = self._dbus_service()
        if svc is not None:
            svc.emit_incoming_call(peer or "")

    def _on_view_switched(self, *_args) -> None:
        # Clear the missed badge when the user opens the Calls tab.
        if not _USE_ADW or not hasattr(self, "_stack"):
            return
        if self._stack.get_visible_child_name() == "calls" and self._missed_calls:
            self._missed_calls = 0
            self._publish_dbus_missed()

    def _build_dial_uri(self, target: str) -> str:
        target = target.strip()
        # Provider-side trunks generally strip '+' from E.164 numbers;
        # we translate to the international access prefix at dial time.
        # Contacts and call history still display the original '+'.
        if target.startswith("+"):
            target = "00" + target[1:]
        if target.startswith(("sip:", "sips:", "tel:")):
            return target
        a = self._settings.account
        domain = a.domain or a.server
        if not domain:
            return target
        return f"sip:{target}@{domain}"

    def _action_about(self, *_args) -> None:
        if _USE_ADW:
            about = Adw.AboutWindow(
                transient_for=self,
                application_name="gsipper",
                application_icon="gsipper",
                version=__version__,
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
