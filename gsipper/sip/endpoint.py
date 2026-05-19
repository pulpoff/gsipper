"""PJSUA2 Endpoint singleton.

Owns the SIP transport, codec configuration and the active account.
All PJSIP callbacks marshal back to the GTK main loop via GLib.idle_add
so the rest of the app can pretend SIP is single-threaded.

G.722, G.711 (PCMU+PCMA) and G.726 are always in stock pjproject.
G.729 is intentionally skipped for now — re-enable by adding
("G729/8000", 220) to _CODEC_PRIORITIES if you're on a pjproject
built with bcg729.
"""

from __future__ import annotations

import threading
from typing import Callable, List, Optional, Tuple

from gi.repository import GLib

try:
    import pjsua2 as pj
    HAVE_PJSUA2 = True
except ImportError:
    pj = None  # type: ignore
    HAVE_PJSUA2 = False

from ..storage.settings import AccountSettings


# (codec id prefix, priority). Higher priority = preferred.
# Priorities below match MicroSIP's default ordering (a-law first).
_CODEC_PRIORITIES: List[Tuple[str, int]] = [
    ("PCMA/8000", 250),
    ("PCMU/8000", 245),
    ("G722/16000", 240),
    ("G726-32/8000", 230),
]


RegStateHandler = Callable[[bool, int, str], None]


if HAVE_PJSUA2:

    class _Account(pj.Account):
        def __init__(self, on_reg_state: RegStateHandler):
            super().__init__()
            self._on_reg_state = on_reg_state

        def onRegState(self, prm):  # noqa: N802 (pjsua2 naming)
            try:
                info = self.getInfo()
                active = bool(info.regIsActive)
                code = int(info.regStatus)
                reason = str(info.regStatusText or "")
            except Exception as exc:  # pjsua2 can raise here on teardown
                active, code, reason = False, 0, str(exc)
            GLib.idle_add(self._on_reg_state, active, code, reason)


class SipEndpoint:
    """Singleton wrapper around pjsua2.Endpoint."""

    _instance: Optional["SipEndpoint"] = None

    def __init__(self) -> None:
        self._ep = None  # type: Optional["pj.Endpoint"]
        self._account = None  # type: Optional["_Account"]
        self._lock = threading.Lock()
        self._started = False
        self._enabled_codecs: List[str] = []
        self._unavailable_codecs: List[str] = []
        self._reg_handler: Optional[RegStateHandler] = None

    @classmethod
    def get(cls) -> "SipEndpoint":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @property
    def available(self) -> bool:
        return HAVE_PJSUA2

    @property
    def enabled_codecs(self) -> List[str]:
        return list(self._enabled_codecs)

    @property
    def unavailable_codecs(self) -> List[str]:
        return list(self._unavailable_codecs)

    def set_reg_handler(self, handler: RegStateHandler) -> None:
        self._reg_handler = handler

    def configure_account(self, settings: AccountSettings) -> None:
        """Start the endpoint if needed and (re)register the account."""
        if not HAVE_PJSUA2:
            raise RuntimeError(
                "python3-pjsua2 is not installed. "
                "Run `sudo apt install python3-pjsua2`."
            )
        with self._lock:
            self._ensure_started(settings.transport)
            self._teardown_account_locked()
            if not (settings.enabled and settings.server
                    and settings.username and settings.password):
                self._notify_reg(False, 0, "Not configured")
                return
            self._account = _Account(self._on_reg_state_internal)
            acfg = self._build_account_config(settings)
            self._account.create(acfg)

    def shutdown(self) -> None:
        with self._lock:
            self._teardown_account_locked()
            if self._ep is not None:
                try:
                    self._ep.libDestroy()
                except Exception:
                    pass
                self._ep = None
                self._started = False

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _ensure_started(self, transport: str) -> None:
        if self._started:
            return
        ep = pj.Endpoint()
        ep.libCreate()

        ep_cfg = pj.EpConfig()
        ep_cfg.logConfig.level = 3
        ep_cfg.uaConfig.userAgent = "gsipper/0.1"
        ep.libInit(ep_cfg)

        ttype = {
            "UDP": pj.PJSIP_TRANSPORT_UDP,
            "TCP": pj.PJSIP_TRANSPORT_TCP,
            "TLS": pj.PJSIP_TRANSPORT_TLS,
        }.get(transport.upper(), pj.PJSIP_TRANSPORT_UDP)
        tcfg = pj.TransportConfig()
        tcfg.port = 0  # ephemeral
        ep.transportCreate(ttype, tcfg)

        ep.libStart()
        self._ep = ep
        self._started = True
        self._configure_codecs()

    def _configure_codecs(self) -> None:
        assert self._ep is not None
        codecs = self._ep.codecEnum2()
        # Disable everything first.
        for c in codecs:
            self._ep.codecSetPriority(c.codecId, 0)

        enabled: List[str] = []
        for c in codecs:
            for prefix, prio in _CODEC_PRIORITIES:
                if c.codecId.startswith(prefix):
                    self._ep.codecSetPriority(c.codecId, prio)
                    enabled.append(c.codecId)
                    break

        available_ids = [c.codecId for c in codecs]
        unavailable = [
            prefix for prefix, _ in _CODEC_PRIORITIES
            if not any(a.startswith(prefix) for a in available_ids)
        ]
        self._enabled_codecs = enabled
        self._unavailable_codecs = unavailable

    def _build_account_config(self, s: AccountSettings) -> "pj.AccountConfig":
        domain = s.domain or s.server
        display = f'"{s.display_name}" ' if s.display_name else ""
        acfg = pj.AccountConfig()
        acfg.idUri = f"{display}<sip:{s.username}@{domain}>"
        acfg.regConfig.registrarUri = f"sip:{s.server}"

        cred = pj.AuthCredInfo(
            "digest", "*",
            s.auth_id or s.username, 0,
            s.password,
        )
        acfg.sipConfig.authCreds.append(cred)
        return acfg

    def _teardown_account_locked(self) -> None:
        if self._account is None:
            return
        try:
            self._account.shutdown()
        except Exception:
            pass
        try:
            self._account.delete()
        except Exception:
            pass
        self._account = None

    def _on_reg_state_internal(self, active: bool, code: int, reason: str) -> bool:
        self._notify_reg(active, code, reason)
        return False  # GLib.idle_add: do not repeat

    def _notify_reg(self, active: bool, code: int, reason: str) -> None:
        if self._reg_handler is not None:
            try:
                self._reg_handler(active, code, reason)
            except Exception:
                import traceback
                traceback.print_exc()
