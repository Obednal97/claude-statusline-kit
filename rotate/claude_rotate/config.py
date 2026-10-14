"""Load and validate ~/.config/claude-rotate/accounts.json."""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from typing import Optional, Tuple

DEFAULT_CONFIG_PATH = "~/.config/claude-rotate/accounts.json"
NAME_RE = re.compile(r"[A-Za-z0-9._@-]+")  # always use .fullmatch

EXAMPLE = """{
  "tiers": [
    { "name": "main",  "config_dir": "~/.claude-personal", "auth": "login", "email": "you@example.com" },
    { "name": "spare", "config_dir": "~/.claude-personal", "auth": "token", "email": "you.too@example.com" }
  ],
  "min_upswitch_interval_seconds": 120
}"""


class ConfigError(Exception):
    """accounts.json is missing or invalid; the message says how to fix it."""


@dataclass(frozen=True)
class Tier:
    name: str
    config_dir: Optional[str]  # absolute path; None = default profile (CLAUDE_CONFIG_DIR unset)
    auth: str  # "login" or "token"
    email: Optional[str] = None  # expected account; login tiers are checked against their profile at launch


@dataclass(frozen=True)
class Config:
    tiers: Tuple[Tier, ...]
    min_upswitch_interval_seconds: int

    def tier(self, name: str) -> Optional[Tier]:
        for t in self.tiers:
            if t.name == name:
                return t
        return None


def config_path() -> str:
    return os.path.expanduser(os.environ.get("RCLAUDE_CONFIG") or DEFAULT_CONFIG_PATH)


def _parse_tier(raw: object, where: str, seen: set) -> Tier:
    if not isinstance(raw, dict):
        raise ConfigError("%s must be an object" % where)
    name = raw.get("name")
    if not isinstance(name, str) or not NAME_RE.fullmatch(name):
        raise ConfigError('%s.name must be letters, digits, ".", "_", "@" or "-"' % where)
    if name in seen:
        raise ConfigError('%s.name "%s" is duplicated' % (where, name))
    seen.add(name)
    auth = raw.get("auth")
    if auth not in ("login", "token"):
        raise ConfigError('%s.auth must be "login" or "token"' % where)
    config_dir = raw.get("config_dir")
    if config_dir is not None:
        if not isinstance(config_dir, str) or not config_dir.strip():
            raise ConfigError("%s.config_dir must be a path or null" % where)
        config_dir = os.path.abspath(os.path.expanduser(config_dir))
        if config_dir == os.path.abspath(os.path.expanduser("~/.claude")):
            raise ConfigError(
                "%s.config_dir: use null for the default ~/.claude profile "
                "(setting it explicitly makes Claude Code read a different, empty Keychain entry)" % where
            )
    email = raw.get("email")
    if email is not None and (not isinstance(email, str) or "@" not in email or not email.strip()):
        raise ConfigError("%s.email must be an email address" % where)
    return Tier(name=name, config_dir=config_dir, auth=auth, email=email.strip() if email else None)


def parse_config(data: object) -> Config:
    if not isinstance(data, dict):
        raise ConfigError("the top level must be a JSON object")
    raw_tiers = data.get("tiers")
    if not isinstance(raw_tiers, list) or not raw_tiers:
        raise ConfigError('"tiers" must be a non-empty list')
    seen: set = set()
    tiers = tuple(_parse_tier(raw, "tiers[%d]" % i, seen) for i, raw in enumerate(raw_tiers))
    interval = data.get("min_upswitch_interval_seconds", 120)
    if isinstance(interval, bool) or not isinstance(interval, int) or interval < 0:
        raise ConfigError('"min_upswitch_interval_seconds" must be a whole number >= 0')
    return Config(tiers=tiers, min_upswitch_interval_seconds=interval)


def load_config(path: Optional[str] = None) -> Config:
    path = path or config_path()
    try:
        with open(path) as f:
            data = json.load(f)
    except FileNotFoundError:
        raise ConfigError("no config at %s. Create it, for example:\n%s" % (path, EXAMPLE)) from None
    except ValueError as e:
        raise ConfigError("%s is not valid JSON: %s" % (path, e)) from None
    return parse_config(data)
