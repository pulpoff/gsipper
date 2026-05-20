"""Window > Settings dialog.

Three app-level preferences (Adw switch rows on a single page):

- Start minimized       : do_activate skips window.present(); the tray
                          icon remains visible and SIP still registers.
- Run on start          : write / remove
                          ~/.config/autostart/com.pulpoff.gsipper.desktop
- Record calls          : record every connected call to WAV via
                          PJSUA2's AudioMediaRecorder, then convert to
                          MP3 with ffmpeg on hangup. The mp3 path is
                          stored on the matching CallRecord and the
                          Calls view grows a play button per row.
"""

from __future__ import annotations

import logging
import os
import shutil
from typing import Callable

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, Gtk  # noqa: E402

from ..storage.settings import GeneralSettings, Settings


logger = logging.getLogger(__name__)


_AUTOSTART_DIR = os.path.join(
    os.environ.get("XDG_CONFIG_HOME", os.path.expanduser("~/.config")),
    "autostart",
)
_AUTOSTART_FILE = os.path.join(_AUTOSTART_DIR, "com.pulpoff.gsipper.desktop")


def install_autostart(enabled: bool) -> None:
    """Write or remove the per-user autostart .desktop file."""
    if enabled:
        os.makedirs(_AUTOSTART_DIR, exist_ok=True)
        # Prefer the system-wide .desktop installed by the .deb so the
        # Exec line points at /usr/bin/gsipper; fall back to a minimal
        # inline body for source-tree runs.
        src = "/usr/share/applications/com.pulpoff.gsipper.desktop"
        if os.path.exists(src):
            shutil.copyfile(src, _AUTOSTART_FILE)
        else:
            with open(_AUTOSTART_FILE, "w") as f:
                f.write(
                    "[Desktop Entry]\n"
                    "Type=Application\n"
                    "Name=gsipper\n"
                    "Exec=gsipper\n"
                    "Icon=gsipper\n"
                    "Terminal=false\n"
                    "X-GNOME-Autostart-enabled=true\n"
                )
        logger.info("autostart enabled: %s", _AUTOSTART_FILE)
    else:
        try:
            os.unlink(_AUTOSTART_FILE)
            logger.info("autostart disabled: %s removed", _AUTOSTART_FILE)
        except FileNotFoundError:
            pass


class SettingsDialog(Adw.PreferencesDialog):
    def __init__(
        self,
        settings: Settings,
        on_save: Callable[[GeneralSettings], None],
    ) -> None:
        super().__init__()
        self.set_title("Settings")
        self._settings = settings
        self._on_save = on_save

        page = Adw.PreferencesPage(title="General",
                                   icon_name="emblem-system-symbolic")
        group = Adw.PreferencesGroup(title="Behaviour")

        g = settings.general

        self._row_minimized = Adw.SwitchRow(
            title="Start minimized",
            subtitle="Launch hidden in the tray instead of opening the main window.",
        )
        self._row_minimized.set_active(g.start_minimized)
        group.add(self._row_minimized)

        self._row_autostart = Adw.SwitchRow(
            title="Run on start",
            subtitle="Auto-start gsipper when you log in.",
        )
        self._row_autostart.set_active(g.run_on_start)
        group.add(self._row_autostart)

        self._row_records = Adw.SwitchRow(
            title="Record calls",
            subtitle="Save each call as MP3 and show a Play button in the "
                     "Calls history. Requires ffmpeg.",
        )
        self._row_records.set_active(g.call_records)
        if shutil.which("ffmpeg") is None:
            self._row_records.set_sensitive(False)
            self._row_records.set_subtitle(
                "ffmpeg not found — install it (apt install ffmpeg) to enable.")
            self._row_records.set_active(False)
        group.add(self._row_records)

        self._row_messages = Adw.SwitchRow(
            title="Enable messages",
            subtitle="Show the Messages tab and accept incoming SIP MESSAGE "
                     "requests. Off by default.",
        )
        self._row_messages.set_active(g.enable_messages)
        group.add(self._row_messages)

        self._row_favorites_only = Adw.SwitchRow(
            title="Favorites only",
            subtitle="Hide the tab bar and show favourite contacts as a 2-column "
                     "grid of quick-dial cards. Tapping a card places a call. "
                     "The usual in-call view appears for the duration of the call.",
        )
        self._row_favorites_only.set_active(g.favorites_only)
        group.add(self._row_favorites_only)

        page.add(group)
        self.add(page)

        self.connect("closed", self._on_closed)

    def _on_closed(self, *_args) -> None:
        # Preserve any GeneralSettings fields that the dialog doesn't
        # expose (window_width / window_height, etc.) — otherwise
        # constructing a fresh dataclass here would wipe them back to
        # their dataclass defaults on every Settings close.
        new = GeneralSettings(
            start_minimized=self._row_minimized.get_active(),
            run_on_start=self._row_autostart.get_active(),
            call_records=self._row_records.get_active(),
            enable_messages=self._row_messages.get_active(),
            favorites_only=self._row_favorites_only.get_active(),
            window_width=self._settings.general.window_width,
            window_height=self._settings.general.window_height,
        )
        # Side-effect: autostart file follows the toggle immediately.
        try:
            install_autostart(new.run_on_start)
        except OSError:
            logger.exception("autostart write failed")
        try:
            self._on_save(new)
        except Exception:
            logger.exception("settings on_save raised")
