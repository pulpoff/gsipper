"""PJSUA2 Endpoint singleton.

Owns the SIP transport, codec configuration and the active account.
All PJSIP callbacks marshal back to the GTK main loop via GLib.idle_add
so the rest of the app can pretend SIP is single-threaded.

The default codec list (G.711a/u, G.722, G.726) lives in
gsipper.storage.settings.default_codecs and is editable per-account via
the Advanced > Codecs list. We translate user order into pjsua2
priorities (highest = first row).
"""

from __future__ import annotations

import logging
import threading
from typing import Callable, List, Optional

from gi.repository import GLib

PJSUA2_IMPORT_ERROR: Optional[str] = None
try:
    import pjsua2 as pj
    HAVE_PJSUA2 = True
except Exception as _exc:  # ImportError, but also catches loader errors
    pj = None  # type: ignore
    HAVE_PJSUA2 = False
    PJSUA2_IMPORT_ERROR = f"{type(_exc).__name__}: {_exc}"

from .. import __version__
from ..storage.settings import AccountSettings

if False:  # type-check only
    from .call import SipCall


logger = logging.getLogger(__name__)
pj_logger = logging.getLogger("gsipper.pjsua2")


RegStateHandler = Callable[[bool, int, str], None]


if HAVE_PJSUA2:

    class _Account(pj.Account):
        def __init__(
            self,
            on_reg_state: RegStateHandler,
            on_incoming,
            on_instant_message,
            on_instant_message_status,
        ) -> None:
            super().__init__()
            self._on_reg_state = on_reg_state
            self._on_incoming = on_incoming
            self._on_instant_message = on_instant_message
            self._on_instant_message_status = on_instant_message_status

        def onRegState(self, prm):  # noqa: N802 (pjsua2 naming)
            try:
                info = self.getInfo()
                active = bool(info.regIsActive)
                code = int(info.regStatus)
                reason = str(info.regStatusText or "")
            except Exception as exc:  # pjsua2 can raise here on teardown
                active, code, reason = False, 0, str(exc)
            logger.info("registration state: active=%s code=%s reason=%s",
                        active, code, reason)
            GLib.idle_add(self._on_reg_state, active, code, reason)

        def onIncomingCall(self, prm):  # noqa: N802
            try:
                call_id = int(prm.callId)
            except Exception:
                logger.error("onIncomingCall: missing callId")
                return
            self._on_incoming(self, call_id)

        def onInstantMessage(self, prm):  # noqa: N802
            try:
                from_uri = str(prm.fromUri or "")
                content = str(prm.msgBody or "")
                content_type = str(getattr(prm, "contentType", "text/plain") or "")
            except Exception as exc:
                logger.error("onInstantMessage parse failed: %s", exc)
                return
            logger.info("instant message from %s: %d chars",
                        from_uri, len(content))
            GLib.idle_add(self._on_instant_message, from_uri, content, content_type)

        def onInstantMessageStatus(self, prm):  # noqa: N802
            try:
                to_uri = str(prm.toUri or "")
                user_data = str(getattr(prm, "userData", "") or "")
                status_code = int(getattr(prm, "code", 0))
                reason = str(getattr(prm, "reason", "") or "")
            except Exception as exc:
                logger.error("onInstantMessageStatus parse failed: %s", exc)
                return
            logger.info("message status to %s: %s %s (id=%s)",
                        to_uri, status_code, reason, user_data)
            GLib.idle_add(
                self._on_instant_message_status,
                user_data, status_code, reason,
            )


    class _PjLogBridge(pj.LogWriter):
        """Bridge pjsua2's log stream into the Python logger."""

        def write(self, entry):  # noqa: N802 (pjsua2 naming)
            try:
                msg = str(getattr(entry, "msg", "") or "").rstrip()
                level = int(getattr(entry, "level", 3))
            except Exception:
                return
            if not msg:
                return
            if level <= 1:
                pj_logger.error(msg)
            elif level == 2:
                pj_logger.warning(msg)
            elif level == 3:
                pj_logger.info(msg)
            else:
                pj_logger.debug(msg)


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
        self._call_state_handler = None  # type: Optional[Callable]
        self._message_handler = None  # type: Optional[Callable]
        self._message_status_handler = None  # type: Optional[Callable]
        self._active_call = None  # type: Optional["SipCall"]
        # PJSUA2's Python wrapper releases the underlying pj_call as
        # soon as the last Python reference goes away. Setting
        # _active_call=None at the 'ended' state can therefore trigger
        # GC BEFORE pjsua2 has finished delivering the BYE on the wire
        # — the remote / provider then keeps the dialog open.
        # We park ended calls here for a few seconds to let PJSIP's
        # post-disconnect cleanup (BYE retransmits, TXN settle, RTP
        # teardown) complete before GC.
        self._call_graveyard: List = []
        self._pj_log_bridge = None  # must outlive the endpoint
        if not HAVE_PJSUA2:
            logger.error(
                "pjsua2 unavailable: %s — install python3-pjsua2",
                PJSUA2_IMPORT_ERROR or "module not found",
            )

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

    def set_call_state_handler(self, handler) -> None:
        """handler(call: SipCall, state: str) — called on GTK main thread."""
        self._call_state_handler = handler

    def set_message_handler(self, handler) -> None:
        """handler(from_uri: str, body: str, content_type: str) — GTK thread."""
        self._message_handler = handler

    def set_message_status_handler(self, handler) -> None:
        """handler(message_id: str, status_code: int, reason: str) — GTK thread."""
        self._message_status_handler = handler

    def send_message(self, to_uri: str, body: str, message_id: str = "") -> bool:
        """Send a SIP MESSAGE. Returns True on submit (not delivery)."""
        if not HAVE_PJSUA2 or self._account is None:
            logger.error("send_message: no registered account")
            return False
        try:
            prm = pj.SendInstantMessageParam()
            prm.toUri = to_uri
            prm.contentType = "text/plain"
            prm.content = body
            if message_id:
                prm.userData = message_id
            self._account.sendInstantMessage(prm)
            logger.info("MESSAGE submitted to %s (id=%s, %d chars)",
                        to_uri, message_id or "-", len(body))
            return True
        except Exception as exc:
            logger.error("send_message failed: %s", exc, exc_info=True)
            return False

    @property
    def active_call(self):
        return self._active_call

    def make_call(self, uri: str):
        """Place an outgoing call. Returns the SipCall, or None on failure."""
        if not HAVE_PJSUA2 or self._account is None:
            logger.error("make_call: no registered account")
            return None
        if self._active_call is not None:
            logger.warning("make_call: another call is active; ignoring")
            return None
        from .call import SipCall
        logger.info("make_call: %s", uri)
        try:
            call = SipCall(self._account, self._on_call_state_internal)
            call.peer_uri = uri
            call.peer_display = uri
            op = pj.CallOpParam(True)
            call.makeCall(uri, op)
        except Exception as exc:
            logger.error("make_call failed: %s", exc)
            return None
        self._active_call = call
        return call

    def hangup_active(self, status_code: int = 0) -> None:
        if self._active_call is None:
            return
        self._active_call.safe_hangup(status_code)

    def answer_active(self) -> None:
        if self._active_call is None:
            return
        self._active_call.safe_answer(200)

    def configure_account(self, settings: AccountSettings) -> None:
        """Start the endpoint if needed and (re)register the account."""
        if not HAVE_PJSUA2:
            logger.error("configure_account: pjsua2 missing (%s)",
                         PJSUA2_IMPORT_ERROR or "unknown")
            raise RuntimeError(
                "python3-pjsua2 is not installed. "
                "Run `sudo apt install python3-pjsua2`."
            )
        logger.info(
            "configure_account: server=%s user=%s transport=%s stun=%s enabled=%s",
            settings.server, settings.username, settings.transport,
            settings.stun_server or "-", settings.enabled,
        )
        with self._lock:
            self._ensure_started(settings.transport, settings.stun_server)
            self._configure_codecs(settings.codecs)
            self._teardown_account_locked()
            if not (settings.enabled and settings.server
                    and settings.username and settings.password):
                self._notify_reg(False, 0, "Not configured")
                return
            self._account = _Account(
                self._on_reg_state_internal,
                self._on_incoming_call_internal,
                self._on_instant_message_internal,
                self._on_instant_message_status_internal,
            )
            acfg = self._build_account_config(settings)
            self._account.create(acfg)

    def shutdown(self) -> None:
        with self._lock:
            self._teardown_account_locked()
            # Drop graveyard references; pjsua2 is going down anyway.
            self._call_graveyard.clear()
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

    def _ensure_started(self, transport: str, stun_server: str = "") -> None:
        if self._started:
            return
        logger.info("starting pjsua2 endpoint transport=%s stun=%s",
                    transport, stun_server or "-")
        ep = pj.Endpoint()
        ep.libCreate()

        ep_cfg = pj.EpConfig()
        ep_cfg.logConfig.level = 4
        ep_cfg.logConfig.consoleLevel = 0  # avoid double-printing on stderr
        try:
            self._pj_log_bridge = _PjLogBridge()
            ep_cfg.logConfig.writer = self._pj_log_bridge
        except Exception as exc:
            logger.warning("could not install pjsua2 log writer: %s", exc)
        ep_cfg.uaConfig.userAgent = f"gsipper {__version__}"
        if stun_server:
            ep_cfg.uaConfig.stunServer.append(stun_server)
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
        logger.info("pjsua2 endpoint started")

    def _configure_codecs(self, codec_settings: list) -> None:
        """Apply the user's codec list to pjsua2.

        codec_settings is the ordered list from AccountSettings.codecs:
        [{"id": "PCMA/8000", "name": "...", "enabled": True}, ...].
        Higher-indexed entries get lower priority; disabled entries get
        priority 0.
        """
        assert self._ep is not None
        available = list(self._ep.codecEnum2())

        for c in available:
            self._ep.codecSetPriority(c.codecId, 0)

        enabled_ids: List[str] = []
        unavailable: List[str] = []
        for idx, entry in enumerate(codec_settings):
            if not entry.get("enabled"):
                continue
            prefix = entry["id"]
            priority = max(1, 250 - idx * 10)
            matches = [c for c in available if c.codecId.startswith(prefix)]
            if not matches:
                unavailable.append(prefix)
                continue
            for c in matches:
                self._ep.codecSetPriority(c.codecId, priority)
                enabled_ids.append(c.codecId)

        self._enabled_codecs = enabled_ids
        self._unavailable_codecs = unavailable
        logger.info("codecs enabled: %s; unavailable: %s",
                    enabled_ids or "-", unavailable or "-")

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

    # ------------------------------------------------------------------
    # Calls
    # ------------------------------------------------------------------

    def _on_call_state_internal(self, call, state: str) -> bool:
        if state == "ended":
            self._record_history(call)
            if call is self._active_call:
                self._active_call = None
            # Hold a reference so PJSIP can finish BYE/200 OK + TXN
            # cleanup before SWIG drops pj_call. 5 s is plenty for any
            # sane RTT.
            self._call_graveyard.append(call)
            GLib.timeout_add_seconds(5, self._reap_call, call)
        if self._call_state_handler is not None:
            try:
                self._call_state_handler(call, state)
            except Exception:
                import traceback
                traceback.print_exc()
        return False  # GLib.idle_add: do not repeat

    def _reap_call(self, call) -> bool:
        try:
            self._call_graveyard.remove(call)
        except ValueError:
            pass
        return False  # one-shot timer

    @staticmethod
    def _record_history(call) -> None:
        from datetime import datetime

        from ..storage.history import CallRecord, append_call

        direction = "incoming" if getattr(call, "incoming", False) else "outgoing"
        connected_at = getattr(call, "connected_at", None)
        started_at = getattr(call, "started_at", 0.0)
        ended_at = getattr(call, "ended_at", None) or 0.0
        duration = int(ended_at - connected_at) if connected_at else 0
        if duration < 0:
            duration = 0
        last_code = int(getattr(call, "last_status_code", 0))
        if connected_at:
            status = "completed"
        elif direction == "incoming":
            # 486 Busy / 603 Decline = we explicitly declined.
            # 487 Request Terminated (or 0) = remote cancelled / timed out.
            if last_code in (486, 603):
                status = "declined"
            else:
                status = "missed"
        else:
            status = "failed"
        record = CallRecord(
            direction=direction,
            peer=getattr(call, "peer_display", "") or getattr(call, "peer_uri", ""),
            peer_uri=getattr(call, "peer_uri", ""),
            started_at=datetime.fromtimestamp(started_at).isoformat(timespec="seconds"),
            duration_seconds=duration,
            status=status,
            status_code=int(getattr(call, "last_status_code", 0)),
            status_reason=str(getattr(call, "last_status_text", "")),
        )
        try:
            append_call(record)
            logger.info("history: recorded %s call peer=%s dur=%ds status=%s",
                        direction, record.peer, duration, status)
        except Exception as exc:
            logger.error("history: failed to append: %s", exc)

    def _on_instant_message_internal(self, from_uri: str, body: str, content_type: str) -> bool:
        if self._message_handler is not None:
            try:
                self._message_handler(from_uri, body, content_type)
            except Exception:
                import traceback
                traceback.print_exc()
        return False

    def _on_instant_message_status_internal(self, message_id: str, code: int, reason: str) -> bool:
        if self._message_status_handler is not None:
            try:
                self._message_status_handler(message_id, code, reason)
            except Exception:
                import traceback
                traceback.print_exc()
        return False

    def _on_incoming_call_internal(self, account, call_id: int) -> None:
        """Wrap the incoming call as a SipCall, send 180 Ringing, then
        let onCallState bubble the 'incoming' state up to the UI."""
        from .call import SipCall, _short_peer
        try:
            call = SipCall(account, self._on_call_state_internal,
                           call_id=call_id, incoming=True)
            try:
                info = call.getInfo()
                call.peer_uri = str(info.remoteUri or "")
                call.peer_display = _short_peer(call.peer_uri) or call.peer_uri
            except Exception:
                pass
            logger.info("incoming call from %s", call.peer_display or "?")
            # Reject second concurrent call with 486 Busy.
            if self._active_call is not None:
                logger.info("already in a call; rejecting second incoming")
                call.safe_hangup(486)
                return
            # Send 180 Ringing.
            try:
                op = pj.CallOpParam()
                op.statusCode = 180
                call.answer(op)
            except Exception as exc:
                logger.error("180 Ringing failed: %s", exc)
            self._active_call = call
            # The pjsua2 INCOMING state has already fired internally;
            # mirror it through our handler so the UI shows the popup.
            self._on_call_state_internal(call, "incoming")
        except Exception as exc:
            logger.error("incoming call handling failed: %s", exc)
