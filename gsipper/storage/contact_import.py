"""Import contacts.vcf (vCard 3.0/4.0) or contacts.csv (Google export).

Auto-detects the format by sniffing the first non-blank line. Both
formats are parsed with stdlib only — no python-vobject dependency.
"""

from __future__ import annotations

import csv
import logging
import re
from io import StringIO
from typing import List

from .contacts import Contact, new_id


logger = logging.getLogger(__name__)


def import_file(path: str) -> List[Contact]:
    with open(path, "r", encoding="utf-8-sig", errors="replace") as f:
        text = f.read()
    return import_text(text)


def import_text(text: str) -> List[Contact]:
    stripped = text.lstrip()
    head = stripped[:32].upper()
    if head.startswith("BEGIN:VCARD"):
        return parse_vcard(text)
    return parse_google_csv(text)


# ----------------------------------------------------------------------
# vCard
# ----------------------------------------------------------------------

def _unfold(text: str) -> List[str]:
    """RFC 6350 line unfolding: a line starting with SPACE or TAB is a
    continuation of the previous line."""
    out: List[str] = []
    for raw in text.splitlines():
        if raw.startswith((" ", "\t")) and out:
            out[-1] += raw[1:]
        else:
            out.append(raw)
    return out


def _unescape(s: str) -> str:
    return (s.replace("\\n", "\n")
             .replace("\\N", "\n")
             .replace("\\,", ",")
             .replace("\\;", ";")
             .replace("\\\\", "\\"))


def _clean_number(s: str) -> str:
    cleaned = re.sub(r"[^\d+*#]", "", s)
    return cleaned or s.strip()


def _phone_label(params: List[str]) -> str:
    for p in params:
        if p.upper().startswith("TYPE="):
            value = p.split("=", 1)[1]
            # Multiple types are comma-separated, e.g. TYPE=CELL,VOICE
            # Prefer the first one that isn't generic "VOICE"/"INTERNET".
            for token in value.replace('"', '').split(","):
                t = token.strip().lower()
                if t and t not in ("voice", "internet", "pref"):
                    return t
            return value.split(",")[0].strip().lower()
    return "phone"


def parse_vcard(text: str) -> List[Contact]:
    contacts: List[Contact] = []
    current: Contact | None = None

    for line in _unfold(text):
        line = line.rstrip("\r")
        if not line.strip():
            continue
        upper = line.strip().upper()
        if upper == "BEGIN:VCARD":
            current = Contact(id=new_id())
            continue
        if upper == "END:VCARD":
            if current is not None and current.name and current.primary_target():
                contacts.append(current)
            current = None
            continue
        if current is None or ":" not in line:
            continue

        header, value = line.split(":", 1)
        parts = header.split(";")
        name = parts[0].upper()
        params = parts[1:]
        value = _unescape(value.strip())

        if name == "FN":
            if value:
                current.name = value
        elif name == "N" and not current.name:
            # Family;Given;Additional;Prefix;Suffix
            pieces = value.split(";")
            family = pieces[0] if len(pieces) > 0 else ""
            given = pieces[1] if len(pieces) > 1 else ""
            current.name = (given + " " + family).strip() or value
        elif name == "TEL":
            num = _clean_number(value)
            if num:
                current.phones.append({"label": _phone_label(params), "number": num})
        elif name == "ORG" and not current.organization:
            current.organization = value.rstrip(";").replace(";", " · ")
        elif name == "NOTE" and not current.notes:
            current.notes = value
        elif name == "X-SIP" or (name == "IMPP" and value.lower().startswith("sip:")):
            if value.lower().startswith("sip:") and not current.sip_uri:
                current.sip_uri = value

    logger.info("vCard import: parsed %d contacts", len(contacts))
    return contacts


# ----------------------------------------------------------------------
# Google CSV
# ----------------------------------------------------------------------

# Google's "Google CSV" export uses " ::: " to join multiple values in
# a single cell (e.g. multiple emails in "E-mail 1 - Value").
_GOOGLE_MULTI = " ::: "


def parse_google_csv(text: str) -> List[Contact]:
    contacts: List[Contact] = []
    reader = csv.DictReader(StringIO(text))
    for row in reader:
        name = (row.get("Name") or row.get("File As") or "").strip()
        if not name:
            given = (row.get("Given Name") or "").strip()
            family = (row.get("Family Name") or "").strip()
            name = (given + " " + family).strip()
        if not name:
            continue

        phones: List[dict] = []
        for idx in range(1, 16):
            values = row.get(f"Phone {idx} - Value") or ""
            label = (row.get(f"Phone {idx} - Type") or "phone").strip().lower() or "phone"
            if not values.strip():
                continue
            for raw in values.split(_GOOGLE_MULTI):
                num = _clean_number(raw)
                if num:
                    phones.append({"label": label, "number": num})

        if not phones:
            # Google CSV rows without a phone are useless for a SIP client.
            continue

        org = (row.get("Organization 1 - Name") or row.get("Organization") or "").strip()
        notes = (row.get("Notes") or "").strip()

        contacts.append(Contact(
            id=new_id(),
            name=name,
            phones=phones,
            organization=org,
            notes=notes,
        ))
    logger.info("Google CSV import: parsed %d contacts", len(contacts))
    return contacts
