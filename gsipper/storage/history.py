"""Call-history persistence at ~/.local/share/gsipper/history.json.

One JSON array of CallRecord objects, newest first. Capped at
_MAX_ENTRIES so the file never grows without bound.

Status meanings:
    "completed" — audio path was up at some point
    "missed"    — incoming, never answered
    "declined"  — incoming, rejected by us
    "failed"    — outgoing, never connected (no answer, busy, error)
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from typing import List


_DATA_DIR = os.path.join(
    os.environ.get("XDG_DATA_HOME", os.path.expanduser("~/.local/share")),
    "gsipper",
)
_HISTORY_PATH = os.path.join(_DATA_DIR, "history.json")
_MAX_ENTRIES = 1000


@dataclass
class CallRecord:
    direction: str = "outgoing"   # "outgoing" | "incoming"
    peer: str = ""                # short display (digits or user part)
    peer_uri: str = ""            # full SIP URI when available
    started_at: str = ""          # ISO 8601 local time
    duration_seconds: int = 0
    status: str = "completed"     # see module docstring
    status_code: int = 0
    status_reason: str = ""
    recording_path: str = ""      # absolute path to the converted .mp3


def load_history() -> List[CallRecord]:
    try:
        with open(_HISTORY_PATH, "r") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(data, list):
        return []
    out: List[CallRecord] = []
    for d in data:
        if not isinstance(d, dict):
            continue
        try:
            out.append(CallRecord(
                direction=str(d.get("direction", "outgoing")),
                peer=str(d.get("peer", "")),
                peer_uri=str(d.get("peer_uri", "")),
                started_at=str(d.get("started_at", "")),
                duration_seconds=int(d.get("duration_seconds", 0)),
                status=str(d.get("status", "completed")),
                status_code=int(d.get("status_code", 0)),
                status_reason=str(d.get("status_reason", "")),
                recording_path=str(d.get("recording_path", "")),
            ))
        except Exception:
            continue
    return out


def save_history(records: List[CallRecord]) -> None:
    os.makedirs(_DATA_DIR, exist_ok=True)
    trimmed = records[:_MAX_ENTRIES]
    tmp = _HISTORY_PATH + ".tmp"
    with open(tmp, "w") as f:
        json.dump([asdict(r) for r in trimmed], f, indent=2)
    os.replace(tmp, _HISTORY_PATH)


def append_call(record: CallRecord) -> List[CallRecord]:
    records = load_history()
    records.insert(0, record)
    save_history(records)
    return records


def clear_recording_path(path: str) -> bool:
    """Drop the recording_path on every history row that points at
    `path`. Used by the playback dialog's delete button so the row
    in Recent loses its ▶ as soon as the file is gone. Returns True
    if any row was updated."""
    if not path:
        return False
    records = load_history()
    changed = False
    for r in records:
        if r.recording_path == path:
            r.recording_path = ""
            changed = True
    if changed:
        save_history(records)
    return changed
