"""Incoming-call ring-in popup.

Mirrors MicroSIP's RinginDlg: a small always-on-top window with the
caller's identity and big Answer / Decline buttons. Auto-closes if the
remote cancels (handled by the window controller calling .dismiss()).
"""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GObject, Gtk  # noqa: E402


class RinginWindow(Adw.Window):
    __gsignals__ = {
        "answer-requested": (GObject.SignalFlags.RUN_FIRST, None, ()),
        "decline-requested": (GObject.SignalFlags.RUN_FIRST, None, ()),
    }

    def __init__(self, parent: Gtk.Window, peer_display: str, peer_uri: str) -> None:
        super().__init__()
        self.set_title("Incoming call")
        # We do NOT call set_transient_for here on purpose. Mutter /
        # Wayland tear down a transient window whose parent isn't
        # mapped — and the MainWindow can be hidden (Favorites-only
        # mode, Start-minimized, X-button-to-tray). The teardown
        # immediately emits close-request, which our handler turns
        # into 'decline-requested' -> hangup(486), all before the
        # ringin popup is ever visible to the user. Standing on our
        # own keeps the popup alive regardless of MainWindow state.
        # We still register with the application (via 'parent') so
        # the WM groups us correctly and Ctrl+W etc. work.
        if parent is not None:
            app = parent.get_application()
            if app is not None:
                self.set_application(app)
        self.set_modal(False)
        self.set_default_size(380, 320)
        self.set_resizable(False)

        header = Adw.HeaderBar()
        header.set_show_start_title_buttons(False)
        header.set_show_end_title_buttons(False)
        # A small "minimize to tray" affordance — closing the popup
        # explicitly declines the call.
        header.set_title_widget(Adw.WindowTitle(title="Incoming call", subtitle=""))

        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10,
                       margin_top=18, margin_bottom=18,
                       margin_start=24, margin_end=24)

        avatar = Gtk.Image.new_from_icon_name("call-start-symbolic")
        avatar.set_pixel_size(72)
        avatar.set_halign(Gtk.Align.CENTER)
        avatar.add_css_class("ringin-avatar")
        body.append(avatar)

        peer = Gtk.Label(label=peer_display or "Unknown caller")
        peer.add_css_class("title-1")
        peer.set_halign(Gtk.Align.CENTER)
        peer.set_wrap(True)
        body.append(peer)

        if peer_uri and peer_uri != peer_display:
            uri = Gtk.Label(label=peer_uri)
            uri.add_css_class("dim-label")
            uri.set_halign(Gtk.Align.CENTER)
            uri.set_wrap(True)
            uri.set_max_width_chars(40)
            body.append(uri)

        state = Gtk.Label(label="Ringing…")
        state.add_css_class("dim-label")
        state.set_halign(Gtk.Align.CENTER)
        body.append(state)

        body.append(Gtk.Box(vexpand=True))

        buttons = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=16,
                          halign=Gtk.Align.CENTER, margin_top=8)
        decline = Gtk.Button(label="Decline")
        decline.set_icon_name("call-stop-symbolic")
        decline.add_css_class("destructive-action")
        decline.add_css_class("pill")
        decline.set_size_request(140, 52)
        decline.connect("clicked", lambda *_: self.emit("decline-requested"))
        buttons.append(decline)

        answer = Gtk.Button(label="Answer")
        answer.set_icon_name("call-start-symbolic")
        answer.add_css_class("suggested-action")
        answer.add_css_class("pill")
        answer.set_size_request(140, 52)
        answer.connect("clicked", lambda *_: self.emit("answer-requested"))
        buttons.append(answer)
        body.append(buttons)

        toolbar = Adw.ToolbarView()
        toolbar.add_top_bar(header)
        toolbar.set_content(body)
        self.set_content(toolbar)

        # Closing the window with the WM = decline.
        self.connect("close-request", lambda *_: (self.emit("decline-requested"), False)[1])

    def dismiss(self) -> None:
        """Called by the controller when the remote cancels."""
        self.close()
