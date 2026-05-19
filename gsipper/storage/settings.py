"""Settings persisted as JSON at ~/.config/gsipper/settings.json."""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field, fields


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
    enabled: bool = False


@dataclass
class Settings:
    account: AccountSettings = field(default_factory=AccountSettings)


def _dataclass_from_dict(cls, data: dict):
    valid = {f.name for f in fields(cls)}
    return cls(**{k: v for k, v in data.items() if k in valid})


def load_settings() -> Settings:
    try:
        with open(_CONFIG_PATH, "r") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return Settings()
    return Settings(
        account=_dataclass_from_dict(AccountSettings, data.get("account", {})),
    )


def save_settings(settings: Settings) -> None:
    os.makedirs(_CONFIG_DIR, exist_ok=True)
    tmp = _CONFIG_PATH + ".tmp"
    with open(tmp, "w") as f:
        json.dump({"account": asdict(settings.account)}, f, indent=2)
    os.replace(tmp, _CONFIG_PATH)
