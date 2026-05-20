"""Modal MP3 playback for recorded calls.

Adw.Window holding a Gst.Playbin and a small transport: play / pause /
stop + a draggable Gtk.Scale showing elapsed / total. Tick driven by
GLib.timeout_add_seconds so we don't need a Gst bus thread on the GTK
loop.
"""

from __future__ import annotations

import logging
import os
from typing import Callable, Optional

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Gst", "1.0")
from gi.repository import Adw, GLib, Gst, Gtk  # noqa: E402


logger = logging.getLogger(__name__)

_GST_READY = False
try:
    Gst.init(None)
    _GST_READY = True
except Exception:
    logger.exception("GStreamer init failed")


def _fmt(seconds: float) -> str:
    s = max(0, int(seconds))
    return f"{s // 60:d}:{s % 60:02d}"


class PlaybackDialog(Adw.Window):
    def __init__(self, parent: Gtk.Window, path: str,
                 on_deleted: Optional["Callable[[], None]"] = None) -> None:
        super().__init__()
        self.set_title(os.path.basename(path))
        self.set_transient_for(parent)
        self.set_modal(True)
        self.set_default_size(420, 160)

        self._path = path
        self._on_deleted = on_deleted
        self._playbin: Optional[Gst.Element] = None
        self._duration_ns = 0
        self._tick_id = 0
        self._scale_grabbed = False
        self._is_playing = False

        header = Adw.HeaderBar()
        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12,
                       margin_top=12, margin_bottom=12,
                       margin_start=12, margin_end=12)
        toolbar = Adw.ToolbarView()
        toolbar.add_top_bar(header)
        toolbar.set_content(body)
        self.set_content(toolbar)

        title_label = Gtk.Label(label=os.path.basename(path), xalign=0.0)
        title_label.add_css_class("heading")
        body.append(title_label)

        # Timeline: elapsed | scale | total
        line = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self._elapsed_lbl = Gtk.Label(label="0:00")
        self._total_lbl = Gtk.Label(label="--:--")
        self._scale = Gtk.Scale(orientation=Gtk.Orientation.HORIZONTAL,
                                hexpand=True)
        self._scale.set_draw_value(False)
        self._scale.set_range(0, 1)
        self._scale.set_increments(1, 5)
        # GTK4: Gtk.GestureClick detects press/release so we know when
        # the user is dragging the slider and we should stop overwriting
        # its value from the tick callback.
        click = Gtk.GestureClick()
        click.connect("pressed", self._on_scale_grab)
        click.connect("released", self._on_scale_release)
        self._scale.add_controller(click)
        self._scale.connect("value-changed", self._on_scale_changed)
        line.append(self._elapsed_lbl)
        line.append(self._scale)
        line.append(self._total_lbl)
        body.append(line)

        # Transport row: play/pause toggle + stop + delete. The
        # play button doubles as pause — its icon flips between
        # media-playback-start / media-playback-pause depending on
        # the playbin state, no separate pause button needed.
        transport = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL,
                            spacing=8, halign=Gtk.Align.CENTER)
        self._btn_play = Gtk.Button.new_from_icon_name("media-playback-start-symbolic")
        self._btn_stop = Gtk.Button.new_from_icon_name("media-playback-stop-symbolic")
        self._btn_delete = Gtk.Button.new_from_icon_name("user-trash-symbolic")
        self._btn_delete.set_tooltip_text("Delete recording")
        self._btn_delete.add_css_class("destructive-action")
        for b in (self._btn_play, self._btn_stop, self._btn_delete):
            b.set_size_request(48, 40)
        self._btn_play.add_css_class("suggested-action")
        self._btn_play.connect("clicked", self._on_play_pause)
        self._btn_stop.connect("clicked", lambda *_: self._stop())
        self._btn_delete.connect("clicked", lambda *_: self._delete())
        transport.append(self._btn_play)
        transport.append(self._btn_stop)
        transport.append(self._btn_delete)
        body.append(transport)

        # GStreamer setup
        if not _GST_READY:
            self._set_error("GStreamer is not available.")
            return
        if not os.path.exists(path):
            self._set_error("Recording file is missing.")
            return

        self._playbin = Gst.ElementFactory.make("playbin", "gsipper-player")
        if self._playbin is None:
            self._set_error("playbin element not available; install gstreamer1.0-plugins-base.")
            return
        self._playbin.set_property("uri", Gst.filename_to_uri(path))

        bus = self._playbin.get_bus()
        bus.add_signal_watch()
        bus.connect("message::eos", self._on_eos)
        bus.connect("message::error", self._on_bus_error)
        bus.connect("message::duration-changed", lambda *_: self._refresh_duration())

        self.connect("close-request", lambda *_: (self._stop(), False)[1])

        # Auto-play.
        self._set_state(Gst.State.PLAYING)

    # ----------------------------------------------------------------------
    # Transport
    # ----------------------------------------------------------------------

    def _set_state(self, state) -> None:
        if self._playbin is None:
            return
        self._playbin.set_state(state)
        self._is_playing = (state == Gst.State.PLAYING)
        self._refresh_play_button()
        if state == Gst.State.PLAYING and self._tick_id == 0:
            # 2 Hz is fine for the elapsed-time label + slider; faster
            # ticks were burning CPU without a visible UX gain.
            self._tick_id = GLib.timeout_add(500, self._tick)

    def _refresh_play_button(self) -> None:
        """Sync the play/pause toggle's icon + colour to playbin state.
        Playing = yellow pause-glyph (".warning"); paused / stopped =
        blue play-glyph (".suggested-action"). Adwaita ships both
        accent classes, so we just swap which one is attached."""
        if self._is_playing:
            self._btn_play.set_icon_name("media-playback-pause-symbolic")
            self._btn_play.set_tooltip_text("Pause")
            self._btn_play.remove_css_class("suggested-action")
            self._btn_play.add_css_class("warning")
        else:
            self._btn_play.set_icon_name("media-playback-start-symbolic")
            self._btn_play.set_tooltip_text("Play")
            self._btn_play.remove_css_class("warning")
            self._btn_play.add_css_class("suggested-action")

    def _on_play_pause(self, *_args) -> None:
        # Single button, two behaviours: toggle between PLAYING and
        # PAUSED. The colour + icon flip via _refresh_play_button in
        # _set_state.
        if self._playbin is None:
            return
        if self._is_playing:
            self._set_state(Gst.State.PAUSED)
        else:
            self._set_state(Gst.State.PLAYING)

    def _stop(self) -> None:
        if self._playbin is not None:
            self._playbin.set_state(Gst.State.NULL)
        if self._tick_id:
            GLib.source_remove(self._tick_id)
            self._tick_id = 0
        self._scale.set_value(0)
        self._elapsed_lbl.set_text("0:00")
        self._is_playing = False
        self._refresh_play_button()

    def _delete(self) -> None:
        # No confirmation per the user's request — clicking the trash
        # button immediately unlinks the MP3, clears recording_path
        # on any history row pointing at it (so Recent loses the ▶
        # button), notifies the caller so they can refresh, and
        # closes the dialog.
        self._stop()
        try:
            os.unlink(self._path)
            logger.info("recording deleted: %s", self._path)
        except FileNotFoundError:
            pass
        except OSError:
            logger.exception("recording delete failed: %s", self._path)
        try:
            from ..storage.history import clear_recording_path
            clear_recording_path(self._path)
        except Exception:
            logger.exception("history clear_recording_path failed")
        if self._on_deleted is not None:
            try:
                self._on_deleted()
            except Exception:
                logger.exception("on_deleted callback raised")
        self.close()

    # ----------------------------------------------------------------------
    # Timeline
    # ----------------------------------------------------------------------

    def _refresh_duration(self) -> None:
        if self._playbin is None:
            return
        ok, dur = self._playbin.query_duration(Gst.Format.TIME)
        if ok and dur > 0:
            self._duration_ns = dur
            self._scale.set_range(0, dur / Gst.SECOND)
            self._total_lbl.set_text(_fmt(dur / Gst.SECOND))

    def _tick(self) -> bool:
        if self._playbin is None:
            return False
        if self._duration_ns == 0:
            self._refresh_duration()
        ok, pos = self._playbin.query_position(Gst.Format.TIME)
        if ok and not self._scale_grabbed:
            secs = pos / Gst.SECOND
            self._scale.set_value(secs)
            self._elapsed_lbl.set_text(_fmt(secs))
        return True  # keep ticking

    def _on_scale_grab(self, *_args) -> None:
        self._scale_grabbed = True

    def _on_scale_release(self, *_args) -> None:
        if self._playbin is None:
            self._scale_grabbed = False
            return
        target = int(self._scale.get_value() * Gst.SECOND)
        self._playbin.seek_simple(
            Gst.Format.TIME,
            Gst.SeekFlags.FLUSH | Gst.SeekFlags.KEY_UNIT,
            target,
        )
        self._scale_grabbed = False

    def _on_scale_changed(self, *_args) -> None:
        self._elapsed_lbl.set_text(_fmt(self._scale.get_value()))

    # ----------------------------------------------------------------------
    # Bus
    # ----------------------------------------------------------------------

    def _on_eos(self, *_args) -> None:
        self._stop()

    def _on_bus_error(self, _bus, msg) -> None:
        err, dbg = msg.parse_error()
        logger.error("playback error: %s (%s)", err, dbg)
        self._set_error(f"Playback error: {err.message}")

    def _set_error(self, text: str) -> None:
        # Keep the delete button enabled even on a load error — the
        # user might want to clean a bad recording out of Recent.
        for b in (self._btn_play, self._btn_stop):
            b.set_sensitive(False)
        self._scale.set_sensitive(False)
        self._total_lbl.set_text("--:--")
        self._elapsed_lbl.set_text(text)
