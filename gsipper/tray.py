"""System-tray status indicator via AyatanaAppIndicator3.

Green icon when the SIP account is registered, red when offline. The
indicator surfaces in the GNOME top bar (with the AppIndicator
extension, which is default on Ubuntu / Pop! / Mint), in KDE's tray,
and in XFCE / Cinnamon natively. On vanilla GNOME without the
extension the bindings still load but the icon is simply hidden by
the shell.

If the bindings aren't present we degrade silently — the headerbar
dot remains the source of truth.
"""

from __future__ import annotations

import logging
from typing import Optional

logger = logging.getLogger(__name__)

try:
    import gi
    gi.require_version("AyatanaAppIndicator3", "0.1")
    from gi.repository import AyatanaAppIndicator3 as AppIndicator  # noqa: E402
    HAVE_INDICATOR = True
except (ValueError, ImportError):
    AppIndicator = None  # type: ignore
    HAVE_INDICATOR = False


class TrayIndicator:
    """Thin wrapper. set_online() changes the icon to green/red."""

    def __init__(self) -> None:
        self._indicator: Optional["AppIndicator.Indicator"] = None
        if not HAVE_INDICATOR:
            logger.info("AyatanaAppIndicator unavailable; skipping tray icon")
            return
        try:
            ind = AppIndicator.Indicator.new(
                "gsipper",
                "user-offline",
                AppIndicator.IndicatorCategory.COMMUNICATIONS,
            )
            ind.set_status(AppIndicator.IndicatorStatus.ACTIVE)
            ind.set_title("gsipper")
            self._indicator = ind
        except Exception as exc:
            logger.warning("AyatanaAppIndicator init failed: %s", exc)

    def set_state(self, state: str) -> None:
        """state: 'online' | 'connecting' | 'offline'."""
        if self._indicator is None:
            return
        icon = {
            "online":     "user-available",   # green dot
            "connecting": "user-busy",        # yellow
            "offline":    "user-offline",     # grey/red dot
        }.get(state, "user-offline")
        try:
            self._indicator.set_icon_full(icon, "gsipper")
        except Exception:
            pass
