"""Calls view — incoming / outgoing / missed history.

Reads from gsipper.storage.history.load_history(). Each row gets a
direction icon coloured via CSS (.call-outgoing = blue, .call-incoming
= green, .call-missed = red).
"""

from __future__ import annotations

import os
from datetime import datetime
from typing import List

import gi

gi.require_version("Gtk", "4.0")

_USE_ADW = False
try:
    gi.require_version("Adw", "1")
    from gi.repository import Adw  # noqa: E402
    _USE_ADW = True
except (ValueError, ImportError):
    pass

from gi.repository import GObject, Gtk  # noqa: E402

from ..storage.history import CallRecord, load_history


def _format_duration(seconds: int) -> str:
    if seconds <= 0:
        return ""
    if seconds < 60:
        return f"{seconds}s"
    m, s = divmod(seconds, 60)
    if m < 60:
        return f"{m}m {s:02d}s" if s else f"{m}m"
    h, m = divmod(m, 60)
    return f"{h}h {m:02d}m"


def _format_when(iso_ts: str) -> str:
    if not iso_ts:
        return ""
    try:
        dt = datetime.fromisoformat(iso_ts)
    except ValueError:
        return iso_ts
    now = datetime.now()
    if dt.date() == now.date():
        return f"Today {dt.strftime('%H:%M')}"
    delta = (now.date() - dt.date()).days
    if delta == 1:
        return f"Yesterday {dt.strftime('%H:%M')}"
    if delta < 7:
        return dt.strftime("%a %H:%M")
    return dt.strftime("%b %d, %H:%M")


def _row_subtitle(record: CallRecord) -> str:
    when = _format_when(record.started_at)
    duration = _format_duration(record.duration_seconds)
    if record.status == "completed":
        bits = [when, duration] if duration else [when]
    elif record.status == "missed":
        bits = [when, "missed"]
    elif record.status == "declined":
        bits = [when, "declined"]
    else:
        # failed / unknown
        reason = record.status_reason or record.status
        bits = [when, reason]
    return "  •  ".join(b for b in bits if b)


def _direction_icon(record: CallRecord) -> Gtk.Image:
    if record.direction == "outgoing":
        # Blue arrow up = outgoing
        name = "go-up-symbolic"
        css = "call-outgoing"
    else:
        # Green arrow down = incoming. Red when missed.
        name = "go-down-symbolic"
        css = "call-missed" if record.status == "missed" else "call-incoming"
    img = Gtk.Image.new_from_icon_name(name)
    img.add_css_class(css)
    img.set_pixel_size(16)
    return img


class CallsView(Gtk.Box):
    __gsignals__ = {
        # Fired when a history row is activated; payload is the dial
        # target (sip_uri if present, else the displayed peer).
        "redial-requested": (GObject.SignalFlags.RUN_FIRST, None, (str,)),
    }

    def __init__(self) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=0)

        self._stack = Gtk.Stack()
        self._stack.set_vexpand(True)

        # Empty state
        if _USE_ADW:
            self._empty = Adw.StatusPage(
                icon_name="call-start-symbolic",
                title="No calls yet",
                description="Incoming, outgoing and missed calls will appear here.",
            )
        else:
            self._empty = Gtk.Label(label="No calls yet")
            self._empty.add_css_class("dim-label")
        self._stack.add_named(self._empty, "empty")

        # Scrollable list
        self._listbox = Gtk.ListBox()
        self._listbox.set_selection_mode(Gtk.SelectionMode.NONE)
        self._listbox.add_css_class("navigation-sidebar")
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scrolled.set_child(self._listbox)
        scrolled.set_vexpand(True)
        self._stack.add_named(scrolled, "list")

        self.append(self._stack)
        self.refresh()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def refresh(self) -> None:
        # Clear existing rows.
        child = self._listbox.get_first_child()
        while child is not None:
            nxt = child.get_next_sibling()
            self._listbox.remove(child)
            child = nxt

        records: List[CallRecord] = load_history()
        # Build a one-shot digits -> name map for this refresh so the
        # row factory can show a friendly contact name above the
        # number when the call's peer matches a stored contact.
        self._contact_names_by_digits = self._build_contact_name_index()

        if not records:
            self._stack.set_visible_child_name("empty")
            return

        for record in records:
            self._listbox.append(self._build_row(record))
        self._stack.set_visible_child_name("list")

    def _build_contact_name_index(self) -> dict:
        """digits-only phone-number -> contact name. Built once per
        refresh() so the row factory doesn't pay the contacts.json
        load + per-phone scan O(n) on every redraw."""
        from ..storage.contacts import load_contacts
        index: dict = {}
        try:
            for c in load_contacts():
                if not c.name:
                    continue
                for phone in c.phones:
                    digits = "".join(ch for ch in phone.get("number", "")
                                     if ch.isdigit())
                    if digits:
                        index.setdefault(digits, c.name)
        except Exception:
            pass
        return index

    def _contact_name_for(self, record: CallRecord) -> str:
        """Return the contact name whose phone matches record.peer
        (digit-suffix match — covers '00…' / '+…' / 'national vs
        international' formatting), or '' if no match."""
        index = getattr(self, "_contact_names_by_digits", None) or {}
        if not index:
            return ""
        digits = "".join(ch for ch in (record.peer or "") if ch.isdigit())
        if not digits:
            return ""
        if digits in index:
            return index[digits]
        # Tail-match in either direction: the call might have stripped
        # a country prefix, or the stored contact might lack one.
        for stored_digits, name in index.items():
            if stored_digits.endswith(digits) or digits.endswith(stored_digits):
                return name
        return ""

    # ------------------------------------------------------------------
    # Row factory
    # ------------------------------------------------------------------

    def _redial_target(self, record: CallRecord) -> str:
        # Prefer the short display peer (the user-part / number we
        # already show in the row) over the full SIP URI. Dropping a
        # raw '"110049" <sip:110049@host>' into the dialer entry was
        # confusing — the user wants to redial 110049, not edit a
        # quoted URI. The endpoint adds the domain back at dial time.
        return record.peer or record.peer_uri or ""

    def _open_player(self, path: str) -> None:
        from ..dialogs.playback_dialog import PlaybackDialog
        PlaybackDialog(parent=self.get_root(), path=path).present()

    def _build_row(self, record: CallRecord):
        has_recording = bool(record.recording_path) and \
                        os.path.exists(record.recording_path)
        # When the call's peer matches a stored contact, the row's
        # title shows the friendly name and the number is folded
        # into the subtitle. Plain number-only otherwise.
        contact_name = self._contact_name_for(record)
        title = contact_name or (record.peer or "(unknown)")
        meta = _row_subtitle(record)
        if contact_name and record.peer:
            subtitle = f"{record.peer}  ·  {meta}"
        else:
            subtitle = meta

        if _USE_ADW:
            row = Adw.ActionRow(
                title=title,
                subtitle=subtitle,
            )
            row.add_prefix(_direction_icon(record))
            if has_recording:
                play_btn = Gtk.Button.new_from_icon_name(
                    "media-playback-start-symbolic")
                play_btn.add_css_class("flat")
                play_btn.set_valign(Gtk.Align.CENTER)
                play_btn.set_tooltip_text("Play recording")
                play_btn.connect(
                    "clicked",
                    lambda *_, p=record.recording_path: self._open_player(p))
                row.add_suffix(play_btn)
            target = self._redial_target(record)
            if target:
                row.set_activatable(True)
                row.connect("activated",
                            lambda *_: self.emit("redial-requested", target))
            return row

        # Fallback for vanilla Gtk
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8,
                      margin_top=8, margin_bottom=8,
                      margin_start=12, margin_end=12)
        box.append(_direction_icon(record))
        text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0,
                       hexpand=True)
        title_lbl = Gtk.Label(label=title, xalign=0.0)
        title_lbl.add_css_class("heading")
        text.append(title_lbl)
        sub = Gtk.Label(label=subtitle, xalign=0.0)
        sub.add_css_class("dim-label")
        sub.add_css_class("caption")
        text.append(sub)
        box.append(text)
        target = self._redial_target(record)
        if target:
            click = Gtk.GestureClick()
            click.connect("released",
                          lambda *_: self.emit("redial-requested", target))
            box.add_controller(click)
        return box
