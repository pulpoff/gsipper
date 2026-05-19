"""GTK4 + libadwaita application entry point for gsipper.

Mirrors midiplayer's Adw.Application bootstrap: single-instance app with
HANDLES_COMMAND_LINE, native GNOME theme integration via libadwaita,
fallback to plain Gtk.Application when libadwaita is unavailable.
"""

from __future__ import annotations

import os
import sys
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

from gi.repository import Gdk, Gio, Gtk  # noqa: E402

from . import log as gslog
from .widgets.window import MainWindow


APP_ID = "com.pulpoff.gsipper"
_RESOURCE_DIR = os.path.join(os.path.dirname(__file__), "resources")

_BaseApp = Adw.Application if _USE_ADW else Gtk.Application


class GsipperApp(_BaseApp):
    def __init__(self) -> None:
        super().__init__(
            application_id=APP_ID,
            flags=Gio.ApplicationFlags.HANDLES_COMMAND_LINE,
        )
        self._window: MainWindow | None = None
        self.connect("command-line", self._on_command_line)

    def do_startup(self) -> None:
        _BaseApp.do_startup(self)

        icon_path = os.path.join(_RESOURCE_DIR, "gsipper.svg")
        if os.path.exists(icon_path):
            display = Gdk.Display.get_default()
            if display is not None:
                theme = Gtk.IconTheme.get_for_display(display)
                theme.add_search_path(_RESOURCE_DIR)

        css_path = os.path.join(_RESOURCE_DIR, "style.css")
        if os.path.exists(css_path):
            provider = Gtk.CssProvider()
            provider.load_from_path(css_path)
            display = Gdk.Display.get_default()
            if display is not None:
                Gtk.StyleContext.add_provider_for_display(
                    display,
                    provider,
                    Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
                )

    def do_activate(self) -> None:
        self._ensure_window().present()

    def _on_command_line(self, app, cmdline) -> int:
        args = cmdline.get_arguments()
        tel_uri: str | None = None
        for arg in args[1:]:
            if arg.startswith("tel:") or arg.startswith("sip:"):
                tel_uri = arg
                break

        window = self._ensure_window()
        if tel_uri:
            window.handle_call_uri(tel_uri)
        window.present()
        return 0

    def _ensure_window(self) -> MainWindow:
        if self._window is None:
            self._window = MainWindow(self)
        return self._window


def main(argv: List[str] | None = None) -> int:
    if argv is None:
        argv = sys.argv
    gslog.setup_logging()
    try:
        app = GsipperApp()
        return app.run(argv)
    except Exception:
        import traceback
        log_dir = os.path.join(
            os.environ.get("XDG_STATE_HOME", os.path.expanduser("~/.local/state")),
            "gsipper",
        )
        os.makedirs(log_dir, exist_ok=True)
        log_path = os.path.join(log_dir, "crash.log")
        with open(log_path, "w") as f:
            traceback.print_exc(file=f)
        raise


if __name__ == "__main__":
    raise SystemExit(main())
