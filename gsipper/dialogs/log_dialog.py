"""Log viewer dialog.

Streams from gsipper.log's in-memory ring buffer. Auto-scrolls to
bottom on new entries; "Copy" puts the full buffer on the clipboard;
"Open file" reveals the on-disk log in the user's text viewer.
"""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, GLib, Gtk  # noqa: E402

from .. import log as gslog


class LogDialog(Adw.Window):
    def __init__(self, parent: Gtk.Window) -> None:
        super().__init__()
        self.set_title("Log")
        self.set_transient_for(parent)
        self.set_modal(False)
        self.set_default_size(820, 540)

        header = Adw.HeaderBar()

        copy_btn = Gtk.Button(icon_name="edit-copy-symbolic")
        copy_btn.set_tooltip_text("Copy log to clipboard")
        copy_btn.connect("clicked", self._on_copy)
        header.pack_end(copy_btn)

        open_btn = Gtk.Button(icon_name="document-open-symbolic")
        open_btn.set_tooltip_text(f"Open {gslog.get_log_path()}")
        open_btn.connect("clicked", self._on_open_file)
        header.pack_end(open_btn)

        clear_btn = Gtk.Button(icon_name="edit-clear-symbolic")
        clear_btn.set_tooltip_text("Clear this view")
        clear_btn.connect("clicked", self._on_clear)
        header.pack_end(clear_btn)

        toolbar_view = Adw.ToolbarView()
        toolbar_view.add_top_bar(header)

        self._buffer = Gtk.TextBuffer()
        text = gslog.get_log_text()
        if text:
            self._buffer.set_text(text)

        self._view = Gtk.TextView(buffer=self._buffer)
        self._view.set_editable(False)
        self._view.set_cursor_visible(False)
        self._view.set_monospace(True)
        self._view.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        self._view.set_top_margin(8)
        self._view.set_bottom_margin(8)
        self._view.set_left_margin(8)
        self._view.set_right_margin(8)

        scrolled = Gtk.ScrolledWindow()
        scrolled.set_child(self._view)
        scrolled.set_vexpand(True)
        scrolled.set_hexpand(True)
        toolbar_view.set_content(scrolled)

        self.set_content(toolbar_view)

        gslog.add_listener(self._on_log_line)
        self.connect("close-request", self._on_close)
        GLib.idle_add(self._scroll_to_end)

    # Logger thread → GTK main thread.
    def _on_log_line(self, line: str) -> None:
        GLib.idle_add(self._append, line)

    def _append(self, line: str) -> bool:
        end = self._buffer.get_end_iter()
        if self._buffer.get_char_count() > 0:
            self._buffer.insert(end, "\n")
        self._buffer.insert(end, line)
        self._scroll_to_end()
        return False

    def _scroll_to_end(self) -> bool:
        end = self._buffer.get_end_iter()
        mark = self._buffer.create_mark(None, end, False)
        self._view.scroll_to_mark(mark, 0.0, True, 0.0, 1.0)
        self._buffer.delete_mark(mark)
        return False

    def _on_copy(self, *_args) -> None:
        text = self._buffer.get_text(
            self._buffer.get_start_iter(),
            self._buffer.get_end_iter(),
            False,
        )
        display = Gdk.Display.get_default()
        if display is not None:
            display.get_clipboard().set(text)

    def _on_open_file(self, *_args) -> None:
        path = gslog.get_log_path()
        try:
            Gio.AppInfo.launch_default_for_uri(
                Gio.File.new_for_path(path).get_uri(), None,
            )
        except Exception:
            pass

    def _on_clear(self, *_args) -> None:
        self._buffer.set_text("")

    def _on_close(self, *_args) -> bool:
        gslog.remove_listener(self._on_log_line)
        return False
