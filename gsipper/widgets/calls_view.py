"""Calls view — incoming / outgoing / missed history.

Reads from gsipper.storage.history.load_history(). Each row gets a
direction icon coloured via CSS (.call-outgoing = blue, .call-incoming
= green, .call-missed = red).
"""

from __future__ import annotations

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
        if not records:
            self._stack.set_visible_child_name("empty")
            return

        for record in records:
            self._listbox.append(self._build_row(record))
        self._stack.set_visible_child_name("list")

    # ------------------------------------------------------------------
    # Row factory
    # ------------------------------------------------------------------

    def _redial_target(self, record: CallRecord) -> str:
        return record.peer_uri or record.peer or ""

    def _open_player(self, path: str) -> None:
        from ..dialogs.playback_dialog import PlaybackDialog
        PlaybackDialog(parent=self.get_root(), path=path).present()

    def _build_row(self, record: CallRecord):
        import os
        has_recording = bool(record.recording_path) and \
                        os.path.exists(record.recording_path)

        if _USE_ADW:
            row = Adw.ActionRow(
                title=record.peer or "(unknown)",
                subtitle=_row_subtitle(record),
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
        title = Gtk.Label(label=record.peer or "(unknown)",
                          xalign=0.0)
        title.add_css_class("heading")
        text.append(title)
        sub = Gtk.Label(label=_row_subtitle(record), xalign=0.0)
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
