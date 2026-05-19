"""Messages tab — conversation list + chat view.

Uses Adw.NavigationView to swap between the conversation list and an
open chat. Each chat shows incoming / outgoing bubbles + a compose
entry; new conversations are started from a '+' button in the list
header.
"""

from __future__ import annotations

import logging
from datetime import datetime
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

from gi.repository import GLib, GObject, Gtk  # noqa: E402

from ..storage import messages as msg_store
from ..storage.messages import Conversation, Message, new_id, normalise_uri


logger = logging.getLogger(__name__)


def _format_time(iso_ts: str) -> str:
    if not iso_ts:
        return ""
    try:
        dt = datetime.fromisoformat(iso_ts)
    except ValueError:
        return iso_ts
    now = datetime.now()
    if dt.date() == now.date():
        return dt.strftime("%H:%M")
    delta = (now.date() - dt.date()).days
    if delta == 1:
        return f"Yesterday {dt.strftime('%H:%M')}"
    if delta < 7:
        return dt.strftime("%a %H:%M")
    return dt.strftime("%b %d, %H:%M")


# ----------------------------------------------------------------------
# Chat bubble
# ----------------------------------------------------------------------

class _Bubble(Gtk.Box):
    def __init__(self, message: Message) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        self.set_margin_top(2)
        self.set_margin_bottom(2)
        self.set_margin_start(8)
        self.set_margin_end(8)

        inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        inner.add_css_class("chat-bubble")
        if message.direction == "outgoing":
            inner.add_css_class("chat-bubble-outgoing")
            self.set_halign(Gtk.Align.END)
        else:
            inner.add_css_class("chat-bubble-incoming")
            self.set_halign(Gtk.Align.START)

        body = Gtk.Label(label=message.body, xalign=0.0)
        body.set_wrap(True)
        body.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        body.set_max_width_chars(40)
        body.set_selectable(True)
        inner.append(body)

        meta_text = _format_time(message.timestamp)
        if message.direction == "outgoing":
            if message.delivery_error:
                meta_text += "  ·  failed"
            elif not message.delivered:
                meta_text += "  ·  sending…"
            else:
                meta_text += "  ·  delivered"
        meta = Gtk.Label(label=meta_text, xalign=0.0 if message.direction == "incoming" else 1.0)
        meta.add_css_class("dim-label")
        meta.add_css_class("caption")
        inner.append(meta)

        self.append(inner)


# ----------------------------------------------------------------------
# Chat page
# ----------------------------------------------------------------------

class _ChatPage(Adw.NavigationPage if _USE_ADW else Gtk.Box):
    """One conversation's transcript + compose row."""

    __gsignals__ = {
        "send-message": (GObject.SignalFlags.RUN_FIRST, None, (str, str)),
        "call-peer":    (GObject.SignalFlags.RUN_FIRST, None, (str,)),
    }

    def __init__(self, conversation: Conversation) -> None:
        if _USE_ADW:
            super().__init__(title=conversation.peer_display or conversation.peer_uri,
                             tag=f"chat-{normalise_uri(conversation.peer_uri)}")
        else:
            super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self._conversation = conversation

        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)

        if _USE_ADW:
            header = Adw.HeaderBar()
            call_btn = Gtk.Button(icon_name="call-start-symbolic",
                                  tooltip_text="Call this contact")
            call_btn.connect("clicked", lambda *_: self.emit("call-peer",
                                                             self._conversation.peer_uri))
            header.pack_end(call_btn)
            toolbar = Adw.ToolbarView()
            toolbar.add_top_bar(header)
            toolbar.set_content(body)
            self.set_child(toolbar)
        else:
            self.append(body)

        # Transcript.
        self._transcript = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4,
                                   margin_top=8, margin_bottom=8)
        self._scrolled = Gtk.ScrolledWindow()
        self._scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self._scrolled.set_vexpand(True)
        self._scrolled.set_child(self._transcript)
        body.append(self._scrolled)

        # Compose row.
        compose = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6,
                          margin_top=6, margin_bottom=6,
                          margin_start=8, margin_end=8)
        self._entry = Gtk.Entry()
        self._entry.set_placeholder_text("Message…")
        self._entry.set_hexpand(True)
        self._entry.connect("activate", lambda *_: self._on_send_clicked())
        compose.append(self._entry)
        self._send_btn = Gtk.Button(icon_name="send-to-symbolic")
        self._send_btn.add_css_class("suggested-action")
        self._send_btn.set_tooltip_text("Send (Enter)")
        self._send_btn.connect("clicked", lambda *_: self._on_send_clicked())
        compose.append(self._send_btn)
        body.append(compose)

        self.refresh_messages()

    # --------------------------------------------------------------
    # API used by MessagesView controller
    # --------------------------------------------------------------

    @property
    def conversation(self) -> Conversation:
        return self._conversation

    def refresh_messages(self) -> None:
        child = self._transcript.get_first_child()
        while child is not None:
            nxt = child.get_next_sibling()
            self._transcript.remove(child)
            child = nxt
        for msg in self._conversation.messages:
            self._transcript.append(_Bubble(msg))
        GLib.idle_add(self._scroll_to_end)

    def _scroll_to_end(self) -> bool:
        adj = self._scrolled.get_vadjustment()
        if adj is not None:
            adj.set_value(adj.get_upper() - adj.get_page_size())
        return False

    # --------------------------------------------------------------

    def _on_send_clicked(self) -> None:
        body = self._entry.get_text().strip()
        if not body:
            return
        self._entry.set_text("")
        self.emit("send-message", self._conversation.peer_uri, body)


# ----------------------------------------------------------------------
# Conversation list page
# ----------------------------------------------------------------------

class _ListPage(Adw.NavigationPage if _USE_ADW else Gtk.Box):
    __gsignals__ = {
        "open-conversation": (GObject.SignalFlags.RUN_FIRST, None, (str,)),
        "new-conversation":  (GObject.SignalFlags.RUN_FIRST, None, ()),
    }

    def __init__(self) -> None:
        if _USE_ADW:
            super().__init__(title="Messages", tag="messages-list")
        else:
            super().__init__(orientation=Gtk.Orientation.VERTICAL)

        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)

        if _USE_ADW:
            header = Adw.HeaderBar()
            new_btn = Gtk.Button(icon_name="document-edit-symbolic",
                                 tooltip_text="New conversation")
            new_btn.connect("clicked", lambda *_: self.emit("new-conversation"))
            header.pack_end(new_btn)
            toolbar = Adw.ToolbarView()
            toolbar.add_top_bar(header)
            toolbar.set_content(body)
            self.set_child(toolbar)
        else:
            new_btn = Gtk.Button(label="New conversation")
            new_btn.connect("clicked", lambda *_: self.emit("new-conversation"))
            self.append(new_btn)
            self.append(body)

        self._stack = Gtk.Stack()
        self._stack.set_vexpand(True)

        if _USE_ADW:
            self._empty = Adw.StatusPage(
                icon_name="mail-unread-symbolic",
                title="No conversations",
                description="Use the new-conversation button to send a SIP message.",
            )
        else:
            self._empty = Gtk.Label(label="No conversations")
            self._empty.add_css_class("dim-label")
        self._stack.add_named(self._empty, "empty")

        self._listbox = Gtk.ListBox()
        self._listbox.set_selection_mode(Gtk.SelectionMode.NONE)
        self._listbox.add_css_class("navigation-sidebar")
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scrolled.set_child(self._listbox)
        scrolled.set_vexpand(True)
        self._stack.add_named(scrolled, "list")

        body.append(self._stack)

    def set_conversations(self, convos: List[Conversation]) -> None:
        child = self._listbox.get_first_child()
        while child is not None:
            nxt = child.get_next_sibling()
            self._listbox.remove(child)
            child = nxt

        if not convos:
            self._stack.set_visible_child_name("empty")
            return

        for c in convos:
            self._listbox.append(self._build_row(c))
        self._stack.set_visible_child_name("list")

    def _build_row(self, convo: Conversation):
        title = convo.peer_display or convo.peer_uri
        subtitle = convo.last_body or "(no messages yet)"
        if _USE_ADW:
            row = Adw.ActionRow(title=title, subtitle=subtitle)
            row.set_activatable(True)
            avatar = Gtk.Image.new_from_icon_name("avatar-default-symbolic")
            avatar.set_pixel_size(28)
            row.add_prefix(avatar)
            time_label = Gtk.Label(label=_format_time(convo.last_activity),
                                   valign=Gtk.Align.CENTER)
            time_label.add_css_class("dim-label")
            time_label.add_css_class("caption")
            row.add_suffix(time_label)
            if convo.unread_count:
                badge = Gtk.Label(label=str(convo.unread_count),
                                  valign=Gtk.Align.CENTER)
                badge.add_css_class("chat-unread-badge")
                row.add_suffix(badge)
            row.connect("activated",
                        lambda *_: self.emit("open-conversation", convo.peer_uri))
            return row

        # Fallback
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8,
                      margin_top=8, margin_bottom=8,
                      margin_start=12, margin_end=12)
        avatar = Gtk.Image.new_from_icon_name("avatar-default-symbolic")
        avatar.set_pixel_size(28)
        box.append(avatar)
        text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, hexpand=True)
        title_l = Gtk.Label(label=title, xalign=0.0)
        title_l.add_css_class("heading")
        text.append(title_l)
        sub = Gtk.Label(label=subtitle, xalign=0.0)
        sub.add_css_class("dim-label")
        sub.add_css_class("caption")
        text.append(sub)
        box.append(text)
        click = Gtk.GestureClick()
        click.connect("released",
                      lambda *_: self.emit("open-conversation", convo.peer_uri))
        box.add_controller(click)
        return box


# ----------------------------------------------------------------------
# Top-level view
# ----------------------------------------------------------------------

class MessagesView(Gtk.Box):
    __gsignals__ = {
        "send-message": (GObject.SignalFlags.RUN_FIRST, None, (str, str, str)),
        "call-peer":    (GObject.SignalFlags.RUN_FIRST, None, (str,)),
    }

    def __init__(self) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL)

        self._conversations: List[Conversation] = msg_store.load_conversations()
        self._open_uri: Optional[str] = None
        self._open_chat_page: Optional[_ChatPage] = None
        self._pending_send_ids: dict[str, str] = {}  # message_id -> peer_uri

        if _USE_ADW and hasattr(Adw, "NavigationView"):
            self._nav = Adw.NavigationView()
            self._list_page = _ListPage()
            self._list_page.connect("open-conversation", self._on_open_conversation)
            self._list_page.connect("new-conversation", self._on_new_conversation)
            self._nav.add(self._list_page)
            self.append(self._nav)
        else:
            # Plain box fallback — only the list, no detail view.
            self._nav = None
            self._list_page = _ListPage()
            self._list_page.connect("open-conversation", self._on_open_conversation)
            self._list_page.connect("new-conversation", self._on_new_conversation)
            self.append(self._list_page)

        self._refresh_list()

    # --------------------------------------------------------------
    # Public API used by MainWindow
    # --------------------------------------------------------------

    def on_incoming_message(self, from_uri: str, body: str,
                            display_resolver: Callable[[str], str]) -> None:
        """Called from MainWindow when a SIP MESSAGE arrives."""
        canonical = normalise_uri(from_uri)
        convo = msg_store.find_conversation(self._conversations, canonical)
        if convo is None:
            convo = Conversation(
                peer_uri=canonical,
                peer_display=display_resolver(canonical) or _short_peer(canonical),
            )
            self._conversations.insert(0, convo)
        else:
            self._conversations.remove(convo)
            self._conversations.insert(0, convo)
        msg = Message(
            id=new_id(),
            direction="incoming",
            body=body,
            timestamp=datetime.now().isoformat(timespec="seconds"),
            read=(canonical == self._open_uri),
        )
        convo.messages.append(msg)
        convo.last_activity = msg.timestamp
        msg_store.save_conversations(self._conversations)
        self._refresh_list()
        if self._open_chat_page is not None and \
                normalise_uri(self._open_chat_page.conversation.peer_uri) == canonical:
            self._open_chat_page.refresh_messages()

    def on_message_status(self, message_id: str, code: int, reason: str) -> None:
        peer_uri = self._pending_send_ids.pop(message_id, None)
        if peer_uri is None:
            return
        convo = msg_store.find_conversation(self._conversations, peer_uri)
        if convo is None:
            return
        for m in convo.messages:
            if m.id == message_id:
                if 200 <= code < 300:
                    m.delivered = True
                    m.delivery_error = ""
                else:
                    m.delivered = False
                    m.delivery_error = f"{code} {reason}".strip()
                break
        msg_store.save_conversations(self._conversations)
        if self._open_chat_page is not None and \
                normalise_uri(self._open_chat_page.conversation.peer_uri) == \
                normalise_uri(peer_uri):
            self._open_chat_page.refresh_messages()

    def total_unread(self) -> int:
        return sum(c.unread_count for c in self._conversations)

    def open_conversation(self, peer_uri: str, peer_display: str = "") -> None:
        """External entry point — e.g. contact menu wants to start a chat."""
        canonical = normalise_uri(peer_uri)
        convo = msg_store.find_conversation(self._conversations, canonical)
        if convo is None:
            convo = Conversation(
                peer_uri=canonical,
                peer_display=peer_display or _short_peer(canonical),
            )
            self._conversations.insert(0, convo)
            msg_store.save_conversations(self._conversations)
            self._refresh_list()
        self._open_conversation_obj(convo)

    # --------------------------------------------------------------
    # List interactions
    # --------------------------------------------------------------

    def _on_open_conversation(self, _src, peer_uri: str) -> None:
        convo = msg_store.find_conversation(self._conversations, peer_uri)
        if convo is None:
            return
        self._open_conversation_obj(convo)

    def _on_new_conversation(self, *_args) -> None:
        from ..dialogs.new_message_dialog import NewMessageDialog
        win = self.get_root()
        NewMessageDialog(parent=win, on_send=self._on_new_message_send).present()

    def _on_new_message_send(self, peer_uri: str, body: str) -> None:
        canonical = normalise_uri(peer_uri)
        if not canonical:
            return
        convo = msg_store.find_conversation(self._conversations, canonical)
        if convo is None:
            convo = Conversation(
                peer_uri=canonical,
                peer_display=_short_peer(canonical),
            )
            self._conversations.insert(0, convo)
        if body:
            self._append_outgoing(convo, body)
        msg_store.save_conversations(self._conversations)
        self._refresh_list()
        self._open_conversation_obj(convo)

    # --------------------------------------------------------------
    # Chat interactions
    # --------------------------------------------------------------

    def _open_conversation_obj(self, convo: Conversation) -> None:
        # Mark as read.
        changed = False
        for m in convo.messages:
            if m.direction == "incoming" and not m.read:
                m.read = True
                changed = True
        if changed:
            msg_store.save_conversations(self._conversations)
            self._refresh_list()

        self._open_uri = normalise_uri(convo.peer_uri)
        page = _ChatPage(convo)
        page.connect("send-message", self._on_chat_send)
        page.connect("call-peer", self._on_chat_call_peer)
        self._open_chat_page = page

        if self._nav is not None:
            existing = self._nav.find_page(page.get_tag())
            if existing is not None:
                self._nav.pop_to_page(existing)
                # We replaced the data — refresh the existing page.
                if isinstance(existing, _ChatPage):
                    existing.refresh_messages()
                    self._open_chat_page = existing
            else:
                self._nav.push(page)

    def _on_chat_send(self, _src, peer_uri: str, body: str) -> None:
        convo = msg_store.find_conversation(self._conversations, peer_uri)
        if convo is None:
            return
        msg = self._append_outgoing(convo, body)
        msg_store.save_conversations(self._conversations)
        self._pending_send_ids[msg.id] = convo.peer_uri
        self._refresh_list()
        if self._open_chat_page is not None:
            self._open_chat_page.refresh_messages()
        self.emit("send-message", convo.peer_uri, body, msg.id)

    def _on_chat_call_peer(self, _src, peer_uri: str) -> None:
        self.emit("call-peer", peer_uri)

    # --------------------------------------------------------------
    # Helpers
    # --------------------------------------------------------------

    def _append_outgoing(self, convo: Conversation, body: str) -> Message:
        msg = Message(
            id=new_id(),
            direction="outgoing",
            body=body,
            timestamp=datetime.now().isoformat(timespec="seconds"),
            delivered=False,
        )
        convo.messages.append(msg)
        convo.last_activity = msg.timestamp
        # Move to top of list.
        if convo in self._conversations:
            self._conversations.remove(convo)
        self._conversations.insert(0, convo)
        return msg

    def _refresh_list(self) -> None:
        self._list_page.set_conversations(self._conversations)


def _short_peer(uri: str) -> str:
    if not uri:
        return ""
    inner = uri
    for scheme in ("sip:", "sips:", "tel:"):
        if inner.startswith(scheme):
            inner = inner[len(scheme):]
            break
    if "@" in inner:
        inner = inner.split("@", 1)[0]
    return inner or uri
