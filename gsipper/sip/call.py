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
            record_to: Optional[str] = None,
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
            # Recording: only starts once the call reaches CONNECTED
            # (so we never persist a WAV for a call that was cancelled
            # while ringing). Audio media references that come up during
            # EARLY are cached here so onCallState can start the
            # recorder the moment the call truly answers.
            self.record_to: Optional[str] = record_to
            self._recorder = None  # type: ignore[assignment]
            self._recorder_started = False
            self._cached_call_audio = None
            self._cached_mic_audio = None
            self._mic_muted = False

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
                # Audio media may have come up during EARLY already;
                # if so its references are cached on the call and we
                # can start the recorder now that the call has truly
                # been answered. If they're not cached yet, the
                # later onCallMediaState (which fires after the
                # answer / ACK exchange completes) starts the
                # recorder instead.
                has_refs = (self._cached_call_audio is not None
                            and self._cached_mic_audio is not None)
                logger.info("connected; cached audio refs=%s record_to=%s",
                            has_refs, self.record_to or "(none)")
                if has_refs:
                    self._maybe_start_recording(
                        self._cached_call_audio,
                        self._cached_mic_audio,
                    )
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
                    # Restore real capture + playback devices in case
                    # the previous call's _do_release_sound_dev left
                    # pjsua2 routed to the null device. -1 means use
                    # the user's system default. pjsua2's Python SWIG
                    # binding exposes individual setCaptureDev /
                    # setPlaybackDev — there's no setSndDev(c,p)
                    # convenience method like in the C API.
                    mgr = ep.audDevManager()
                    try:
                        mgr.setCaptureDev(-1)
                        mgr.setPlaybackDev(-1)
                    except Exception:
                        logger.exception("setCaptureDev/setPlaybackDev failed; "
                                         "relying on PJSUA defaults")
                    mic = ep.audDevManager().getCaptureDevMedia()
                    spk = ep.audDevManager().getPlaybackDevMedia()
                    mic.startTransmit(aud)
                    aud.startTransmit(spk)
                    # Preserve mute across mid-call media re-bridges
                    # (hold/resume, codec renegotiation): if the user
                    # had muted before this onCallMediaState fired,
                    # tear the mic→peer link straight back down.
                    if self._mic_muted:
                        try:
                            mic.stopTransmit(aud)
                        except Exception:
                            logger.exception("re-mute after media re-bridge failed")
                    logger.info("audio media connected (idx=%d)", idx)
                    # Cache for onCallState in case audio came up before
                    # the call reached CONFIRMED (early media).
                    self._cached_call_audio = aud
                    self._cached_mic_audio = mic
                    # Only record once the call is actually answered;
                    # ringing / early-media audio never lands in a WAV.
                    logger.info("media bridged; state=%s record_to=%s",
                                self.state,
                                self.record_to or "(none)")
                    if self.state == STATE_CONNECTED:
                        self._maybe_start_recording(aud, mic)
                except Exception as exc:
                    logger.error("audio media setup failed: %s", exc)

        def _maybe_start_recording(self, call_audio, mic_audio) -> None:
            """Wire both directions to an AudioMediaRecorder. Called
            once per call from onCallMediaState after the audio path is
            up; subsequent media events are ignored."""
            if not self.record_to:
                logger.info("recording skipped: record_to is empty "
                            "(call_records disabled, ffmpeg missing, "
                            "or _build_record_path wasn't reached)")
                return
            if self._recorder_started:
                logger.debug("recording already running")
                return
            import os as _os
            _os.makedirs(_os.path.dirname(self.record_to), exist_ok=True)
            try:
                rec = pj.AudioMediaRecorder()
                rec.createRecorder(self.record_to)
                call_audio.startTransmit(rec)   # remote voice
                mic_audio.startTransmit(rec)    # our voice
                self._recorder = rec
                self._recorder_started = True
                logger.info("recording started: %s", self.record_to)
            except Exception:
                logger.exception("recorder start failed")
                self._recorder = None

        def stop_recording(self) -> Optional[str]:
            """Detach the recorder reference from this call and return
            the WAV path (or None if no recording was running). The
            caller MUST schedule the recorder's final ref-drop on the
            pjsua2 worker thread — SWIG's destructor invokes pjmedia
            cleanup, which asserts when it runs on an unregistered
            thread."""
            if self._recorder is None:
                return None
            return self.record_to

        def take_recorder(self):
            """Hand off the live recorder so the worker thread can
            drop the last reference (and trigger the SWIG destructor)
            on a pjsua2-registered thread."""
            rec = self._recorder
            self._recorder = None
            return rec

        def set_mic_muted(self, muted: bool) -> None:
            """Mute / unmute the local capture device for this call.

            Implementation: connect / disconnect the mic from the
            remote call audio media in pjsua2's conference bridge.
            When muted, the peer hears silence; when unmuted we
            re-bridge the mic. Local playback (we hear the peer) is
            unaffected.

            Idempotent — calling with the current state is a no-op."""
            aud = self._cached_call_audio
            mic = self._cached_mic_audio
            if aud is None or mic is None:
                logger.info("mute toggle ignored: audio not bridged yet "
                            "(call_audio=%s mic_audio=%s)",
                            aud is not None, mic is not None)
                return
            if bool(muted) == bool(self._mic_muted):
                return
            try:
                if muted:
                    mic.stopTransmit(aud)
                else:
                    mic.startTransmit(aud)
                self._mic_muted = bool(muted)
                logger.info("mic %s", "muted" if muted else "unmuted")
            except Exception:
                logger.exception("mic mute toggle failed")

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
            # Log the pjsua2-side state so we can tell, after the fact,
            # whether the call was really CONFIRMED (BYE), EARLY
            # (CANCEL), or already DISCONNECTED (no-op) when the user
            # tapped End.
            pre_state = "?"
            try:
                info = self.getInfo()
                pre_state = f"{info.stateText} ({int(info.state)})"
            except Exception:
                pass
            # Stack trace so any unexpected 486 (e.g. an auto-decline
            # firing from a WM close-request we didn't anticipate)
            # leaves a clear breadcrumb in the log.
            import traceback
            caller = " <- ".join(
                f"{f.name}:{f.lineno}"
                for f in traceback.extract_stack()[-6:-1]
            )
            logger.info("hangup() requested: pjsua2_state=%s status_code=%s "
                        "from %s",
                        pre_state, status_code or "default", caller)
            try:
                op = pj.CallOpParam()
                if status_code:
                    op.statusCode = status_code
                # statusCode=0 lets pjsua2 pick the right verb for the
                # state: BYE for CONFIRMED, CANCEL for EARLY, 487 for
                # incoming-not-yet-answered, etc.
                self.hangup(op)
                logger.info("hangup() submitted to pjsua2")
            except Exception as exc:
                logger.error("hangup failed: %s", exc, exc_info=True)

        def safe_answer(self, status_code: int = 200) -> None:
            try:
                op = pj.CallOpParam()
                op.statusCode = status_code
                self.answer(op)
            except Exception as exc:
                logger.error("answer failed: %s", exc)
