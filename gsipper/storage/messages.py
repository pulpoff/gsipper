"""SIP-IM persistence at ~/.local/share/gsipper/messages.json.

One JSON file containing every conversation. Conversations are keyed
by the peer SIP URI; each holds an ordered list of Messages with
direction (incoming/outgoing), body, timestamp and per-direction
status (delivered for outgoing, read for incoming).
"""

from __future__ import annotations

import json
import os
import uuid
from dataclasses import asdict, dataclass, field
from typing import Iterable, List, Optional


_DATA_DIR = os.path.join(
    os.environ.get("XDG_DATA_HOME", os.path.expanduser("~/.local/share")),
    "gsipper",
)
_MESSAGES_PATH = os.path.join(_DATA_DIR, "messages.json")
_MAX_MESSAGES_PER_CONVO = 2000


@dataclass
class Message:
    id: str = ""
    direction: str = "outgoing"   # "incoming" | "outgoing"
    body: str = ""
    timestamp: str = ""           # ISO 8601 local
    delivered: bool = False       # outgoing: set on 200 OK to MESSAGE
    delivery_error: str = ""      # outgoing: set on failure
    read: bool = False            # incoming: set when user views convo


@dataclass
class Conversation:
    peer_uri: str = ""            # canonical SIP URI ("sip:bob@host")
    peer_display: str = ""        # short label shown in the list
    messages: List[Message] = field(default_factory=list)
    last_activity: str = ""       # ISO 8601 of most recent message

    @property
    def unread_count(self) -> int:
        return sum(1 for m in self.messages
                   if m.direction == "incoming" and not m.read)

    @property
    def last_body(self) -> str:
        if not self.messages:
            return ""
        return self.messages[-1].body


# ----------------------------------------------------------------------
# Persistence
# ----------------------------------------------------------------------

def _message_from_dict(d: dict) -> Optional[Message]:
    if not isinstance(d, dict):
        return None
    try:
        return Message(
            id=str(d.get("id") or new_id()),
            direction=str(d.get("direction", "outgoing")),
            body=str(d.get("body", "")),
            timestamp=str(d.get("timestamp", "")),
            delivered=bool(d.get("delivered", False)),
            delivery_error=str(d.get("delivery_error", "")),
            read=bool(d.get("read", False)),
        )
    except Exception:
        return None


def _convo_from_dict(d: dict) -> Optional[Conversation]:
    if not isinstance(d, dict):
        return None
    uri = d.get("peer_uri") or ""
    if not uri:
        return None
    msgs: List[Message] = []
    for m in d.get("messages", []) or []:
        msg = _message_from_dict(m)
        if msg is not None:
            msgs.append(msg)
    return Conversation(
        peer_uri=str(uri),
        peer_display=str(d.get("peer_display") or uri),
        messages=msgs,
        last_activity=str(d.get("last_activity", "")),
    )


def load_conversations() -> List[Conversation]:
    try:
        with open(_MESSAGES_PATH, "r") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(data, list):
        return []
    out: List[Conversation] = []
    for d in data:
        c = _convo_from_dict(d)
        if c is not None:
            out.append(c)
    # Newest activity first.
    out.sort(key=lambda c: c.last_activity, reverse=True)
    return out


def save_conversations(convos: Iterable[Conversation]) -> None:
    os.makedirs(_DATA_DIR, exist_ok=True)
    # Trim each convo to the cap so the file doesn't grow without bound.
    serialised = []
    for c in convos:
        d = asdict(c)
        msgs = d.get("messages", [])
        if len(msgs) > _MAX_MESSAGES_PER_CONVO:
            d["messages"] = msgs[-_MAX_MESSAGES_PER_CONVO:]
        serialised.append(d)
    tmp = _MESSAGES_PATH + ".tmp"
    with open(tmp, "w") as f:
        json.dump(serialised, f, indent=2)
    os.replace(tmp, _MESSAGES_PATH)


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------

def new_id() -> str:
    return str(uuid.uuid4())


def normalise_uri(uri: str) -> str:
    """Canonicalise a SIP URI for keying. Strips display name + params."""
    if not uri:
        return ""
    inner = uri.strip()
    if "<" in inner and ">" in inner:
        inner = inner[inner.find("<") + 1: inner.rfind(">")]
    if ";" in inner:
        inner = inner.split(";", 1)[0]
    return inner


def find_conversation(convos: List[Conversation], peer_uri: str) -> Optional[Conversation]:
    key = normalise_uri(peer_uri)
    for c in convos:
        if normalise_uri(c.peer_uri) == key:
            return c
    return None


def get_messages_path() -> str:
    return _MESSAGES_PATH
