"""Single active SIP call wrapper.

Carries enough state (started_at / connected_at / direction / final
status) for the endpoint to write a history record when the call
disconnects.

pjsua2.Call subclass that surfaces state transitions and connects audio
media to the system audio devices when the call goes confirmed.
All callbacks marshal back to the GTK main loop via GLib.idle_add.

State strings (used across the codebase):
    "calling"    — INVITE sent, no response yet
    "ringing"    — 1xx received (or sent, for incoming)
    "connected"  — 200 OK, call active
    "ended"      — disconnected for any reason
    "incoming"   — INVITE arrived, awaiting accept/reject
"""

from __future__ import annotations

import logging
import time
from typing import Callable, Optional

from gi.repository import GLib

logger = logging.getLogger(__name__)

try:
    import pjsua2 as pj
    HAVE_PJSUA2 = True
except Exception:
    pj = None  # type: ignore
    HAVE_PJSUA2 = False


STATE_CALLING = "calling"
STATE_RINGING = "ringing"
STATE_CONNECTED = "connected"
STATE_ENDED = "ended"
STATE_INCOMING = "incoming"


CallStateHandler = Callable[["SipCall", str], None]


def _short_peer(uri: str) -> str:
    """sip:"Bob" <sip:bob@host>;tag=… → bob (or the raw URI when we can't parse)."""
    if not uri:
        return ""
    # Strip display-name and angle brackets if present.
    inner = uri
    if "<" in inner and ">" in inner:
        inner = inner[inner.find("<") + 1: inner.rfind(">")]
    for scheme in ("sip:", "sips:", "tel:"):
        if inner.startswith(scheme):
            inner = inner[len(scheme):]
            break
    if "@" in inner:
        inner = inner.split("@", 1)[0]
    if ";" in inner:
        inner = inner.split(";", 1)[0]
    return inner or uri


if HAVE_PJSUA2:

    class SipCall(pj.Call):
        def __init__(
            self,
            account,
            on_state: CallStateHandler,
            call_id: int = -1,
            incoming: bool = False,
        ) -> None:
            super().__init__(account, call_id)
            self._on_state = on_state
            self.incoming = incoming
            self.peer_uri = ""
            self.peer_display = ""
            self.state = STATE_INCOMING if incoming else STATE_CALLING
            self.last_status_code = 0
            self.last_status_text = ""
            # For history bookkeeping.
            self.started_at: float = time.time()
            self.connected_at: Optional[float] = None
            self.ended_at: Optional[float] = None

        # --------------------------------------------------------------
        # pjsua2 callbacks
        # --------------------------------------------------------------

        def onCallState(self, prm):  # noqa: N802 (pjsua2 naming)
            try:
                info = self.getInfo()
                pj_state = int(info.state)
                self.peer_uri = str(info.remoteUri or self.peer_uri)
                self.peer_display = _short_peer(self.peer_uri)
                self.last_status_code = int(info.lastStatusCode)
                self.last_status_text = str(info.lastReason or "")
            except Exception as exc:
                logger.error("onCallState getInfo failed: %s", exc)
                self._notify(STATE_ENDED)
                return

            mapped = self._map_state(pj_state)
            self.state = mapped
            if mapped == STATE_CONNECTED and self.connected_at is None:
                self.connected_at = time.time()
            if mapped == STATE_ENDED and self.ended_at is None:
                self.ended_at = time.time()
            logger.info("call state: %s peer=%s code=%s reason=%s",
                        mapped, self.peer_display,
                        self.last_status_code, self.last_status_text)
            self._notify(mapped)

        def onCallMediaState(self, prm):  # noqa: N802
            try:
                info = self.getInfo()
            except Exception as exc:
                logger.error("onCallMediaState getInfo failed: %s", exc)
                return
            media = info.media
            try:
                count = media.size()
            except Exception:
                count = len(media)
            for idx in range(count):
                media_info = media[idx]
                if media_info.type != pj.PJMEDIA_TYPE_AUDIO:
                    continue
                if media_info.status not in (
                    pj.PJSUA_CALL_MEDIA_ACTIVE,
                    pj.PJSUA_CALL_MEDIA_REMOTE_HOLD,
                ):
                    continue
                try:
                    aud = self.getAudioMedia(idx)
                    ep = pj.Endpoint.instance()
                    ep.audDevManager().getCaptureDevMedia().startTransmit(aud)
                    aud.startTransmit(ep.audDevManager().getPlaybackDevMedia())
                    logger.info("audio media connected (idx=%d)", idx)
                except Exception as exc:
                    logger.error("audio media setup failed: %s", exc)

        # --------------------------------------------------------------
        # Helpers
        # --------------------------------------------------------------

        def _notify(self, state: str) -> None:
            GLib.idle_add(self._on_state, self, state)

        @staticmethod
        def _map_state(s: int) -> str:
            try:
                if s == pj.PJSIP_INV_STATE_CALLING:
                    return STATE_CALLING
                if s == pj.PJSIP_INV_STATE_INCOMING:
                    return STATE_INCOMING
                if s in (pj.PJSIP_INV_STATE_EARLY,
                         pj.PJSIP_INV_STATE_CONNECTING):
                    return STATE_RINGING
                if s == pj.PJSIP_INV_STATE_CONFIRMED:
                    return STATE_CONNECTED
                if s == pj.PJSIP_INV_STATE_DISCONNECTED:
                    return STATE_ENDED
            except Exception:
                pass
            return STATE_ENDED

        # --------------------------------------------------------------
        # Control API (called from GTK main thread)
        # --------------------------------------------------------------

        def safe_hangup(self, status_code: int = 0) -> None:
            try:
                op = pj.CallOpParam()
                if status_code:
                    op.statusCode = status_code
                self.hangup(op)
            except Exception as exc:
                logger.error("hangup failed: %s", exc)

        def safe_answer(self, status_code: int = 200) -> None:
            try:
                op = pj.CallOpParam()
                op.statusCode = status_code
                self.answer(op)
            except Exception as exc:
                logger.error("answer failed: %s", exc)
