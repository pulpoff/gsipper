"""Settings persisted as JSON at ~/.config/gsipper/settings.json."""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field, fields
from typing import List


def default_codecs() -> List[dict]:
    """Default codec list, in priority order (highest first).

    Each entry has an `id` (pjsua2 codec prefix), a human `name`, and an
    `enabled` flag. Add new families here and they'll show up in the
    Advanced > Codecs list automatically. Codecs that aren't actually
    available in the running pjsua2 build (e.g. G.729 without libbcg729
    at build time) are silently skipped and listed in the Advanced
    'unavailable' tooltip.
    """
    return [
        {"id": "opus/48000",   "name": "Opus",                "enabled": True},
        {"id": "G722/16000",   "name": "G.722",               "enabled": True},
        {"id": "G7221/16000",  "name": "G.722.1",             "enabled": True},
        {"id": "PCMA/8000",    "name": "G.711 a-law (PCMA)",  "enabled": True},
        {"id": "PCMU/8000",    "name": "G.711 µ-law (PCMU)",  "enabled": True},
        {"id": "GSM/8000",     "name": "GSM Full-Rate",       "enabled": True},
    ]


_CONFIG_DIR = os.path.join(
    os.environ.get("XDG_CONFIG_HOME", os.path.expanduser("~/.config")),
    "gsipper",
)
_CONFIG_PATH = os.path.join(_CONFIG_DIR, "settings.json")


@dataclass
class AccountSettings:
    name: str = ""
    server: str = ""
    domain: str = ""           # optional; defaults to server
    username: str = ""
    password: str = ""
    auth_id: str = ""          # optional; defaults to username
    display_name: str = ""
    transport: str = "UDP"     # UDP, TCP, TLS
    stun_server: str = ""      # e.g. stun.l.google.com:19302
    enabled: bool = False
    codecs: List[dict] = field(default_factory=default_codecs)


@dataclass
class GeneralSettings:
    """App-level preferences edited from Window > Settings."""
    start_minimized: bool = False   # do_activate skips window.present()
    run_on_start: bool = False      # write ~/.config/autostart/*.desktop
    call_records: bool = False      # record every call to wav, convert to mp3
    enable_messages: bool = False   # show the Messages tab + SIP IM features
    window_width: int = 0           # last size on hide; 0 = use default 360
    window_height: int = 0          # last size on hide; 0 = use default 560


@dataclass
class Settings:
    account: AccountSettings = field(default_factory=AccountSettings)
    general: GeneralSettings = field(default_factory=GeneralSettings)


def _dataclass_from_dict(cls, data: dict):
    valid = {f.name for f in fields(cls)}
    return cls(**{k: v for k, v in data.items() if k in valid})


# Codecs that used to appear in default_codecs() but have been
# retired. _sanitize_codecs strips them out of any settings.json the
# user upgraded from. Keep this list narrow — anything still in
# default_codecs() must NOT be listed here.
_RETIRED_CODEC_IDS = frozenset({
    "G729/8000",     # bcg729 dropped — too much external-lib churn
    "G726-32/8000",
    "G726-24/8000",
    "G726-16/8000",
    "G726-40/8000",
    "G723/8000",     # patent-restricted, never bundled
})


def _sanitize_codecs(raw) -> List[dict]:
    defaults = default_codecs()
    if not isinstance(raw, list):
        return defaults
    cleaned: List[dict] = []
    seen_ids: set = set()
    for item in raw:
        if not isinstance(item, dict):
            continue
        cid = item.get("id")
        if not cid or str(cid) in _RETIRED_CODEC_IDS:
            continue
        name = item.get("name") or cid
        cleaned.append({
            "id": str(cid),
            "name": str(name),
            "enabled": bool(item.get("enabled", True)),
        })
        seen_ids.add(str(cid))
    if not cleaned:
        return defaults
    # Append any codecs introduced in newer gsipper versions that
    # aren't in the user's saved list. Preserves the user's existing
    # ordering and enabled/disabled state for the codecs they already
    # configured; just adds the new entries at the bottom so they
    # surface in Account > Advanced > Codecs without the user having
    # to wipe settings.json.
    for d in defaults:
        if d["id"] not in seen_ids:
            cleaned.append({
                "id": d["id"],
                "name": d["name"],
                "enabled": bool(d.get("enabled", True)),
            })
    return cleaned


def load_settings() -> Settings:
    try:
        with open(_CONFIG_PATH, "r") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return Settings()
    account_data = dict(data.get("account", {}))
    raw_codecs = account_data.pop("codecs", None)
    account = _dataclass_from_dict(AccountSettings, account_data)
    if raw_codecs is not None:
        account.codecs = _sanitize_codecs(raw_codecs)
    general = _dataclass_from_dict(GeneralSettings, dict(data.get("general", {})))
    return Settings(account=account, general=general)


def save_settings(settings: Settings) -> None:
    os.makedirs(_CONFIG_DIR, exist_ok=True)
    tmp = _CONFIG_PATH + ".tmp"
    with open(tmp, "w") as f:
        json.dump({
            "account": asdict(settings.account),
            "general": asdict(settings.general),
        }, f, indent=2)
    os.replace(tmp, _CONFIG_PATH)
