"""D-Bus surface exposed to the gsipper@pulpoff.com GNOME extension.

GApplication already owns com.pulpoff.gsipper on the session bus, so
we register an extra object at /com/pulpoff/gsipper/Status on its
connection rather than calling bus_own_name a second time.

Interface
    com.pulpoff.gsipper.Status
        Status        : s   ("online" | "connecting" | "offline")
        MissedCalls   : u
        Show()              — bring the main window to front
        Quit()              — quit gsipper
        IncomingCall(s)     — emitted when an INVITE arrives
"""

from __future__ import annotations

import logging
from typing import Callable, Optional

from gi.repository import Gio, GLib

logger = logging.getLogger(__name__)


OBJECT_PATH = "/com/pulpoff/gsipper/Status"
INTERFACE_NAME = "com.pulpoff.gsipper.Status"

_INTROSPECTION_XML = """
<node>
  <interface name='com.pulpoff.gsipper.Status'>
    <property name='Status' type='s' access='read'/>
    <property name='MissedCalls' type='u' access='read'/>
    <method name='Show'/>
    <method name='Quit'/>
    <signal name='IncomingCall'>
      <arg type='s' name='peer'/>
    </signal>
  </interface>
</node>
"""


class StatusService:
    def __init__(
        self,
        connection: Gio.DBusConnection,
        on_show: Callable[[], None],
        on_quit: Callable[[], None],
    ) -> None:
        self._connection = connection
        self._on_show = on_show
        self._on_quit = on_quit
        self._registration_id = 0

        self._status = "offline"
        self._missed_calls = 0

        try:
            node_info = Gio.DBusNodeInfo.new_for_xml(_INTROSPECTION_XML)
            iface_info = node_info.lookup_interface(INTERFACE_NAME)
            self._registration_id = connection.register_object(
                OBJECT_PATH,
                iface_info,
                self._on_method_call,
                self._on_get_property,
                None,  # all properties read-only
            )
            logger.info("D-Bus Status service registered at %s", OBJECT_PATH)
        except Exception as exc:
            logger.warning("D-Bus Status registration failed: %s", exc)
            self._registration_id = 0

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @property
    def active(self) -> bool:
        return self._registration_id != 0

    def set_status(self, status: str) -> None:
        if status not in ("online", "connecting", "offline"):
            status = "offline"
        if status == self._status:
            return
        self._status = status
        self._emit_properties_changed({"Status": GLib.Variant("s", status)})

    def set_missed_calls(self, count: int) -> None:
        count = max(0, int(count))
        if count == self._missed_calls:
            return
        self._missed_calls = count
        self._emit_properties_changed({"MissedCalls": GLib.Variant("u", count)})

    def emit_incoming_call(self, peer: str) -> None:
        if not self.active:
            return
        try:
            self._connection.emit_signal(
                None, OBJECT_PATH, INTERFACE_NAME,
                "IncomingCall",
                GLib.Variant("(s)", (peer or "",)),
            )
        except Exception as exc:
            logger.warning("emit IncomingCall failed: %s", exc)

    def shutdown(self) -> None:
        if self._registration_id and self._connection is not None:
            try:
                self._connection.unregister_object(self._registration_id)
            except Exception:
                pass
            self._registration_id = 0

    # ------------------------------------------------------------------
    # D-Bus callbacks
    # ------------------------------------------------------------------

    def _on_method_call(self, _connection, _sender, _object_path, _interface_name,
                        method_name, _parameters, invocation):
        try:
            if method_name == "Show":
                GLib.idle_add(self._on_show)
                invocation.return_value(None)
            elif method_name == "Quit":
                GLib.idle_add(self._on_quit)
                invocation.return_value(None)
            else:
                invocation.return_error_literal(
                    Gio.DBusError.quark(),
                    Gio.DBusError.UNKNOWN_METHOD,
                    f"Unknown method: {method_name}",
                )
        except Exception as exc:
            invocation.return_error_literal(
                Gio.DBusError.quark(), Gio.DBusError.FAILED, str(exc),
            )

    def _on_get_property(self, _connection, _sender, _object_path,
                         _interface_name, property_name):
        if property_name == "Status":
            return GLib.Variant("s", self._status)
        if property_name == "MissedCalls":
            return GLib.Variant("u", self._missed_calls)
        return None

    def _emit_properties_changed(self, changed: dict) -> None:
        if not self.active:
            return
        try:
            self._connection.emit_signal(
                None,
                OBJECT_PATH,
                "org.freedesktop.DBus.Properties",
                "PropertiesChanged",
                GLib.Variant(
                    "(sa{sv}as)",
                    (INTERFACE_NAME, changed, []),
                ),
            )
        except Exception as exc:
            logger.warning("PropertiesChanged emit failed: %s", exc)
