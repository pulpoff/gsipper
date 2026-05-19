"""Contact list persistence at ~/.local/share/gsipper/contacts.json."""

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
_CONTACTS_PATH = os.path.join(_DATA_DIR, "contacts.json")


@dataclass
class Contact:
    id: str = ""
    name: str = ""
    # Each entry: {"label": "mobile" | "home" | "work" | ..., "number": "+1..."}
    phones: List[dict] = field(default_factory=list)
    sip_uri: str = ""
    organization: str = ""
    notes: str = ""

    def primary_target(self) -> str:
        """The string we hand to SipEndpoint.make_call when the row is clicked."""
        if self.sip_uri:
            return self.sip_uri
        for p in self.phones:
            num = p.get("number", "")
            if num:
                return num
        return ""

    def matches(self, query: str) -> bool:
        if not query:
            return True
        q = query.casefold()
        if q in self.name.casefold():
            return True
        if q in self.organization.casefold():
            return True
        if q in self.sip_uri.casefold():
            return True
        digits_q = "".join(c for c in q if c.isdigit())
        for p in self.phones:
            num = p.get("number", "")
            if q in num.casefold():
                return True
            if digits_q and digits_q in "".join(c for c in num if c.isdigit()):
                return True
        return False


def _from_dict(d: dict) -> Optional[Contact]:
    if not isinstance(d, dict):
        return None
    try:
        phones = d.get("phones", []) or []
        phones = [
            {"label": str(p.get("label", "")), "number": str(p.get("number", ""))}
            for p in phones if isinstance(p, dict) and p.get("number")
        ]
        return Contact(
            id=str(d.get("id") or uuid.uuid4()),
            name=str(d.get("name", "")),
            phones=phones,
            sip_uri=str(d.get("sip_uri", "")),
            organization=str(d.get("organization", "")),
            notes=str(d.get("notes", "")),
        )
    except Exception:
        return None


def load_contacts() -> List[Contact]:
    try:
        with open(_CONTACTS_PATH, "r") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(data, list):
        return []
    out: List[Contact] = []
    for d in data:
        c = _from_dict(d)
        if c is not None:
            out.append(c)
    return out


def save_contacts(contacts: Iterable[Contact]) -> None:
    os.makedirs(_DATA_DIR, exist_ok=True)
    tmp = _CONTACTS_PATH + ".tmp"
    with open(tmp, "w") as f:
        json.dump([asdict(c) for c in contacts], f, indent=2)
    os.replace(tmp, _CONTACTS_PATH)


def new_id() -> str:
    return str(uuid.uuid4())


def merge_imported(existing: List[Contact], imported: Iterable[Contact]) -> int:
    """Append imported contacts, skipping ones whose name+primary already exists.
    Returns the number of new entries actually added."""
    seen = {(c.name.casefold(), c.primary_target()) for c in existing}
    added = 0
    for c in imported:
        key = (c.name.casefold(), c.primary_target())
        if not c.name or not c.primary_target():
            # Without at least a name and one number we can't dial it.
            continue
        if key in seen:
            continue
        seen.add(key)
        existing.append(c)
        added += 1
    return added
