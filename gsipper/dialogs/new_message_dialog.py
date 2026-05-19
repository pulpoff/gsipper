"""New-conversation dialog.

Small Adw.Window that asks for a peer SIP URI / number and an
optional first message body. On Send, the parent receives a callback
with (peer_uri, body) and is expected to create / open the
conversation.
"""

from __future__ import annotations

import logging
from typing import Callable

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk  # noqa: E402


logger = logging.getLogger(__name__)


class NewMessageDialog(Adw.Window):
    def __init__(
        self,
        parent: Gtk.Window,
        on_send: Callable[[str, str], None],
    ) -> None:
        super().__init__()
        self.set_title("New conversation")
        self.set_transient_for(parent)
        self.set_modal(True)
        self.set_default_size(420, 320)

        self._on_send = on_send

        header = Adw.HeaderBar()
        send_btn = Gtk.Button(label="Send")
        send_btn.add_css_class("suggested-action")
        send_btn.connect("clicked", lambda *_: self._on_send_clicked())
        header.pack_end(send_btn)

        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12,
                       margin_top=12, margin_bottom=12,
                       margin_start=12, margin_end=12)

        peer_group = Adw.PreferencesGroup(title="Recipient")
        self._row_peer = Adw.EntryRow(title="SIP URI or number")
        peer_group.add(self._row_peer)
        body.append(peer_group)

        msg_group = Adw.PreferencesGroup(title="Message")
        # Adw doesn't have a multi-line PreferencesRow; embed a TextView
        # in a styled card.
        text_frame = Gtk.Frame()
        text_frame.add_css_class("card")
        self._text = Gtk.TextView()
        self._text.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        self._text.set_top_margin(6)
        self._text.set_bottom_margin(6)
        self._text.set_left_margin(8)
        self._text.set_right_margin(8)
        self._text.set_hexpand(True)
        text_scroll = Gtk.ScrolledWindow()
        text_scroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        text_scroll.set_min_content_height(120)
        text_scroll.set_child(self._text)
        text_frame.set_child(text_scroll)
        msg_group.add(text_frame)
        body.append(msg_group)

        toolbar = Adw.ToolbarView()
        toolbar.add_top_bar(header)
        toolbar.set_content(body)
        self.set_content(toolbar)

    def _on_send_clicked(self) -> None:
        peer = self._row_peer.get_text().strip()
        buf = self._text.get_buffer()
        start, end = buf.get_start_iter(), buf.get_end_iter()
        msg_body = buf.get_text(start, end, False).strip()
        if not peer:
            return
        try:
            self._on_send(peer, msg_body)
        except Exception:
            logger.exception("new-message on_send raised")
        self.close()
