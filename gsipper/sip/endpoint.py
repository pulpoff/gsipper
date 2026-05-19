"""PJSUA2 Endpoint singleton.

Owns the SIP transport, codec configuration and the active account.

Threading model
---------------
A single dedicated worker thread (_SipWorker) owns every pjsua2 call
we initiate — libCreate / libInit / libStart, account.create,
call.makeCall, call.hangup, account.sendInstantMessage, etc. Public
methods on SipEndpoint enqueue a closure into the worker so the GTK
main loop never blocks on PJSIP. PJSIP's own internal worker threads
fire onCallState / onRegState / onInstantMessage from yet other
threads; those callbacks already marshal back to the GTK main loop
via GLib.idle_add, so the rest of the app can pretend SIP is
single-threaded.

The default codec list (G.711a/u, G.722, G.726) lives in
gsipper.storage.settings.default_codecs and is editable per-account via
the Advanced > Codecs list. We translate user order into pjsua2
priorities (highest = first row).
"""

from __future__ import annotations

import logging
import os
import queue
import re
import shutil
import subprocess
import threading
from datetime import datetime
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
from ..storage.settings import AccountSettings, load_settings


_RECORDINGS_DIR = os.path.join(
    os.environ.get("XDG_DATA_HOME", os.path.expanduser("~/.local/share")),
    "gsipper", "recordings",
)


def _build_record_path(peer: str) -> Optional[str]:
    """Recording target for a call if the user enabled call records,
    else None. Path is in ~/.local/share/gsipper/recordings."""
    if not load_settings().general.call_records:
        return None
    if shutil.which("ffmpeg") is None:
        logger.warning("call records enabled but ffmpeg missing; skipping")
        return None
    safe = re.sub(r"\W+", "_", peer or "unknown")[:32] or "unknown"
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    return os.path.join(_RECORDINGS_DIR, f"{ts}_{safe}.wav")


def _convert_wav_to_mp3(wav_path: str, mp3_path: str) -> None:
    """Run ffmpeg off the worker thread. Deletes the WAV on success."""
    try:
        subprocess.run(
            ["ffmpeg", "-y", "-loglevel", "error", "-i", wav_path,
             "-c:a", "libmp3lame", "-q:a", "4", mp3_path],
            check=True,
        )
        try:
            os.unlink(wav_path)
        except OSError:
            pass
        logger.info("recording converted: %s", mp3_path)
    except Exception:
        logger.exception("ffmpeg conversion failed: %s -> %s", wav_path, mp3_path)

if False:  # type-check only
    from .call import SipCall


logger = logging.getLogger(__name__)
pj_logger = logging.getLogger("gsipper.pjsua2")


RegStateHandler = Callable[[bool, int, str], None]


class _SipWorker(threading.Thread):
    """One thread that owns every pjsua2 API call we initiate.

    All public methods of SipEndpoint enqueue closures here. PJSIP's
    internal worker threads (which fire onCallState etc.) are NOT this
    thread — they're created by libStart and fire callbacks from
    inside the C library; those callbacks must marshal back to the
    GTK main loop themselves.
    """

    def __init__(self) -> None:
        super().__init__(daemon=True, name="gsipper-sip-worker")
        self._queue: "queue.Queue" = queue.Queue()

    def submit(self, fn: Callable, *args, **kwargs) -> None:
        self._queue.put((fn, args, kwargs))

    def stop(self) -> None:
        self._queue.put(None)

    def run(self) -> None:
        while True:
            item = self._queue.get()
            if item is None:
                return
            fn, args, kwargs = item
            try:
                fn(*args, **kwargs)
            except Exception:
                logger.exception("sip worker: %s failed", getattr(fn, "__name__", fn))


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
                level = int(getattr(entry, "level", 3))
            except Exception:
                return
            # Drop pjsua2 TRACE / verbose logs (level >= 5) BEFORE we
            # stringify — during a call PJSIP emits hundreds of these
            # per second (per-packet, per-timer), and just building
            # the str() and dispatching idle_add hooks would burn
            # measurable CPU on the GTK loop.
            if level >= 5:
                return
            try:
                msg = str(getattr(entry, "msg", "") or "").rstrip()
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
        self._available_codec_ids: List[str] = []
        self._unavailable_codecs: List[str] = []
        self._reg_handler: Optional[RegStateHandler] = None
        self._call_state_handler = None  # type: Optional[Callable]
        self._message_handler = None  # type: Optional[Callable]
        self._message_status_handler = None  # type: Optional[Callable]
        self._active_call = None  # type: Optional["SipCall"]
        self._worker: Optional[_SipWorker] = None
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

    def available_codec_ids(self) -> List[str]:
        """Codec IDs reported by pjsua2.Endpoint.codecEnum2() — the
        ones that are ACTUALLY built into the .so and can be enabled.
        Empty if pjsua2 is not loaded or hasn't been started yet.

        Returns the cached list populated on the worker thread once
        libStart completes; calling codecEnum2() from the GTK main
        thread would trip pj_thread_this' 'unknown/external thread'
        assertion and SIGABRT the process."""
        return list(self._available_codec_ids)

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

    @property
    def active_call(self):
        return self._active_call

    # ------------------------------------------------------------------
    # Public API (each method ENQUEUES onto the worker thread so the
    # GTK main loop never blocks on a pjsua2 call.)
    # ------------------------------------------------------------------

    def configure_account(self, settings: AccountSettings) -> None:
        if not HAVE_PJSUA2:
            logger.error("configure_account: pjsua2 missing (%s)",
                         PJSUA2_IMPORT_ERROR or "unknown")
            # Surface immediately so the UI shows Offline + tooltip
            # without waiting for the worker.
            self._notify_reg(False, 0, "python3-pjsua2 not installed")
            return
        self._ensure_worker()
        self._worker.submit(self._do_configure_account, settings)

    def shutdown(self) -> None:
        if self._worker is None:
            return
        self._worker.submit(self._do_shutdown)
        self._worker.stop()
        self._worker.join(timeout=5)
        self._worker = None

    def make_call(self, uri: str) -> None:
        if not HAVE_PJSUA2:
            logger.error("make_call: pjsua2 missing")
            return
        self._ensure_worker()
        self._worker.submit(self._do_make_call, uri)

    def hangup_active(self, status_code: int = 0) -> None:
        if self._worker is None:
            return
        # Snapshot the active call now so a later worker tick still
        # has something to operate on even if state changes.
        call = self._active_call
        if call is None:
            return
        self._worker.submit(call.safe_hangup, status_code)

    def answer_active(self) -> None:
        if self._worker is None:
            return
        call = self._active_call
        if call is None:
            return
        self._worker.submit(call.safe_answer, 200)

    def send_message(self, to_uri: str, body: str, message_id: str = "") -> bool:
        """Enqueue a SIP MESSAGE. Returns False only if pjsua2 is missing
        — actual delivery success/failure flows through the status
        handler. Always returns True if the request was queued."""
        if not HAVE_PJSUA2:
            return False
        self._ensure_worker()
        self._worker.submit(self._do_send_message, to_uri, body, message_id)
        return True

    def set_registration(self, active: bool) -> None:
        """Toggle the account's REGISTER binding. True re-registers
        (also used to refresh an existing registration), False sends
        an un-REGISTER."""
        if not HAVE_PJSUA2:
            return
        self._ensure_worker()
        self._worker.submit(self._do_set_registration, active)

    # ------------------------------------------------------------------
    # Worker-side implementations (run on _SipWorker; never on GTK)
    # ------------------------------------------------------------------

    def _ensure_worker(self) -> None:
        if self._worker is None:
            self._worker = _SipWorker()
            self._worker.start()

    def _do_configure_account(self, settings: AccountSettings) -> None:
        logger.info(
            "configure_account: server=%s user=%s transport=%s stun=%s enabled=%s",
            settings.server, settings.username, settings.transport,
            settings.stun_server or "-", settings.enabled,
        )
        with self._lock:
            try:
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
                logger.info("new account created; REGISTER initiated")
            except Exception as exc:
                logger.error("configure_account failed: %s", exc, exc_info=True)
                self._notify_reg(False, 0, f"Init failed: {exc}")

    def _do_shutdown(self) -> None:
        with self._lock:
            self._teardown_account_locked()
            if self._ep is not None:
                try:
                    self._ep.libDestroy()
                except Exception:
                    pass
                self._ep = None
                self._started = False

    def _do_make_call(self, uri: str) -> None:
        from .call import SipCall
        if self._account is None:
            logger.error("make_call: no registered account")
            return
        if self._active_call is not None:
            logger.warning("make_call: another call is active; ignoring")
            return
        logger.info("make_call: %s", uri)
        try:
            from .call import _short_peer
            call = SipCall(
                self._account,
                self._on_call_state_internal,
                record_to=_build_record_path(_short_peer(uri) or "outgoing"),
            )
            call.peer_uri = uri
            call.peer_display = uri
            op = pj.CallOpParam(True)
            call.makeCall(uri, op)
        except Exception as exc:
            logger.error("make_call failed: %s", exc, exc_info=True)
            return
        self._active_call = call

    def _do_set_registration(self, active: bool) -> None:
        # If the account was torn down because credentials were missing
        # or the user previously disabled it, 'Connect' has to go back
        # through configure_account to rebuild the pjsua2.Account.
        if self._account is None:
            if active:
                self._do_configure_account(load_settings().account)
            else:
                self._notify_reg(False, 0, "Disconnected")
            return
        try:
            self._account.setRegistration(active)
            logger.info("setRegistration(%s) submitted", active)
            if not active:
                # PJSIP doesn't always fire onRegState for unregisters
                # promptly; surface the change in the UI immediately.
                self._notify_reg(False, 0, "Disconnected")
        except Exception:
            logger.exception("setRegistration(%s) failed", active)

    def _do_send_message(self, to_uri: str, body: str, message_id: str) -> None:
        if self._account is None:
            logger.error("send_message: no registered account")
            if message_id and self._message_status_handler is not None:
                GLib.idle_add(self._on_instant_message_status_internal,
                              message_id, 500, "Not registered")
            return
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
        except Exception as exc:
            logger.error("send_message failed: %s", exc, exc_info=True)
            if message_id and self._message_status_handler is not None:
                GLib.idle_add(self._on_instant_message_status_internal,
                              message_id, 500, str(exc))

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
        # level 3 = INFO. The previous default (4 = DEBUG) made PJSIP
        # emit hundreds of lines per second during an active call,
        # all of which crossed SWIG into our log bridge — visible CPU
        # cost for messages that just got dropped at logger.debug().
        ep_cfg.logConfig.level = 3
        ep_cfg.logConfig.consoleLevel = 0  # avoid double-printing on stderr
        try:
            self._pj_log_bridge = _PjLogBridge()
            ep_cfg.logConfig.writer = self._pj_log_bridge
        except Exception as exc:
            logger.warning("could not install pjsua2 log writer: %s", exc)
        # 'name/version' is the RFC 3261 product-token form. The
        # space-separated form some servers' admin UIs use to derive a
        # 'Device' name picks the first token before whitespace, so
        # 'gsipper 1.3.x' would show up as just 'gsipper'. The slash
        # keeps the whole token together — matches what FRITZ!OS,
        # MicroSIP, PJSUA, etc. send.
        ep_cfg.uaConfig.userAgent = f"gsipper/{__version__}"
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

        # Snapshot the codec list on the worker thread (which IS
        # registered with pjlib because it called libCreate). The GTK
        # main thread later reads this via available_codec_ids() to
        # gray out unsupported entries in Account > Advanced > Codecs.
        try:
            self._available_codec_ids = [c.codecId for c in ep.codecEnum2()]
            logger.info("available codecs: %s", self._available_codec_ids)
        except Exception:
            logger.exception("codecEnum2() failed")
            self._available_codec_ids = []

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
        # Tell pjsua2 to send an un-REGISTER and detach the account.
        # shutdown() does not wait for the un-REGISTER's 200 OK; the
        # caller (e.g. _do_configure_account on reconnect) immediately
        # builds a fresh _Account and create()s it, so two packets go
        # back-to-back on the wire: un-REGISTER (Expires: 0) then a
        # new REGISTER. The trunk's 200 OK we log next belongs to the
        # latter, not to the cached previous registration.
        logger.info("tearing down existing account")
        try:
            self._account.shutdown()
        except Exception as exc:
            logger.warning("account.shutdown raised: %s", exc)
        try:
            self._account.delete()
        except Exception:
            pass
        self._account = None
        logger.info("account torn down")

    def _on_reg_state_internal(self, active: bool, code: int, reason: str) -> bool:
        self._notify_reg(active, code, reason)
        return False  # GLib.idle_add: do not repeat

    def _notify_reg(self, active: bool, code: int, reason: str) -> None:
        if self._reg_handler is not None:
            try:
                self._reg_handler(active, code, reason)
            except Exception:
                logger.exception("handler raised")

    # ------------------------------------------------------------------
    # Calls
    # ------------------------------------------------------------------

    def _on_call_state_internal(self, call, state: str) -> bool:
        if state == "ended":
            wav_path = None
            mp3_path = None
            try:
                wav_path = call.stop_recording()
            except Exception:
                logger.exception("stop_recording raised")
            # Detach the live recorder ref from the call now so it
            # doesn't get GC'd on this (GTK) thread when self._active_call
            # is cleared below. _dispose_via_worker re-hands it to the
            # pjsua2-registered worker for the actual ref-drop.
            recorder = None
            try:
                recorder = call.take_recorder()
            except Exception:
                logger.exception("take_recorder raised")
            if wav_path:
                mp3_path = wav_path[:-4] + ".mp3"
                threading.Thread(
                    target=_convert_wav_to_mp3,
                    args=(wav_path, mp3_path),
                    name="gsipper-mp3",
                    daemon=True,
                ).start()
            self._record_history(call, recording_path=mp3_path or "")
            if call is self._active_call:
                self._active_call = None
            # Hold both refs for 5 s so PJSIP can finish BYE / 200 OK /
            # TXN cleanup, then hand them to the worker thread which
            # lets the local closure vars go out of scope on a
            # pjsua2-registered thread — that's where SWIG fires the
            # pjmedia_wav_writer_port_destroy / pjsua_call_close
            # destructors safely.
            GLib.timeout_add_seconds(
                5, self._dispose_via_worker, call, recorder,
            )
        if self._call_state_handler is not None:
            try:
                self._call_state_handler(call, state)
            except Exception:
                logger.exception("handler raised")
        return False  # GLib.idle_add: do not repeat

    def _dispose_via_worker(self, call, recorder) -> bool:
        """GLib timeout callback (GTK thread). Submits a no-op closure
        whose default args hold the call + recorder refs onto the
        worker thread. When the closure exits on the worker, those
        refs drop and the SWIG destructors run on a registered
        thread."""
        # The lambda's default args keep call + recorder alive until
        # it runs on the worker; the lambda body itself is a no-op.
        self._worker.submit(lambda c=call, r=recorder: None)
        return False  # one-shot

    @staticmethod
    def _record_history(call, recording_path: str = "") -> None:
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
            recording_path=recording_path,
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
                logger.exception("handler raised")
        return False

    def _on_instant_message_status_internal(self, message_id: str, code: int, reason: str) -> bool:
        if self._message_status_handler is not None:
            try:
                self._message_status_handler(message_id, code, reason)
            except Exception:
                logger.exception("handler raised")
        return False

    def _on_incoming_call_internal(self, account, call_id: int) -> None:
        """Wrap the incoming call as a SipCall, send 180 Ringing, then
        let onCallState bubble the 'incoming' state up to the UI."""
        from .call import SipCall, _short_peer
        try:
            # Probe the peer URI first so the recording filename can use
            # a human-readable name (we need a SipCall instance first
            # though — pjsua2 ties them to the call_id).
            tmp = SipCall(account, self._on_call_state_internal,
                          call_id=call_id, incoming=True)
            try:
                info = tmp.getInfo()
                peer_uri = str(info.remoteUri or "")
            except Exception:
                peer_uri = ""
            tmp.peer_uri = peer_uri
            tmp.peer_display = _short_peer(peer_uri) or peer_uri
            tmp.record_to = _build_record_path(tmp.peer_display or "incoming")
            call = tmp
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
