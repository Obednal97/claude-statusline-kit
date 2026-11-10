"""`rclaude models`: which Claude models each account can use, from Anthropic's model list read with that account's own
login. Plans differ (one organisation may have a model another doesn't), and Claude Code's /model menu only shows a few.
Results are kept for MAX_AGE in models.json in the state folder, and kept longer when a login can't be read (a login
account's access token expires until Claude Code next uses that account), so a list is there whenever one has been read."""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import time
import urllib.request
from typing import Callable, Dict, List, Optional, Sequence

from . import tokens
from .config import Config, Tier

URL = "https://api.anthropic.com/v1/models?limit=100"
MAX_AGE = 6 * 3600
USAGE = "usage: rclaude models [--json] [--refresh]"


def login_token(tier: Tier, now: float) -> Optional[str]:
    """Claude Code's own access token for a login account's config folder, while it's still valid."""
    if sys.platform == "darwin":  # Claude Code names the Keychain item after the folder, unless it's the default one
        svc = "Claude Code-credentials"
        if tier.config_dir:
            svc += "-" + hashlib.sha256(tier.config_dir.encode()).hexdigest()[:8]
        raw = subprocess.run(["security", "find-generic-password", "-s", svc, "-a", tokens._account(), "-w"],
                             capture_output=True, text=True).stdout
    else:
        try:
            with open(os.path.join(tier.config_dir or os.path.expanduser("~/.claude"), ".credentials.json")) as f:
                raw = f.read()
        except OSError:
            return None
    try:
        o = json.loads(raw)["claudeAiOauth"]
    except (ValueError, KeyError, TypeError):
        return None
    return o.get("accessToken") if o.get("expiresAt", 0) / 1000 > now + 60 else None


def fetch(token: str) -> List[str]:
    req = urllib.request.Request(URL, headers={"Authorization": "Bearer " + token, "anthropic-version": "2023-06-01",
                                               "anthropic-beta": "oauth-2025-04-20", "User-Agent": "rclaude"})
    with urllib.request.urlopen(req, timeout=15) as r:
        return [m["id"] for m in json.load(r).get("data", []) if isinstance(m, dict) and m.get("id")]


def token_for(tier: Tier, now: float) -> Optional[str]:
    try:
        return tokens.get_token(tier.name) if tier.auth == "token" else login_token(tier, now)
    except tokens.TokenStoreError:
        return None


def read(config: Config, state_dir: str, now: float, refresh: bool = False,
         token: Callable[[Tier, float], Optional[str]] = token_for,
         get: Callable[[str], List[str]] = fetch) -> Dict[str, dict]:
    """account -> {"models": [ids, newest first], "t": when read}; an account never read successfully is left out."""
    path = os.path.join(state_dir, "models.json")
    try:
        with open(path) as f:
            cache = json.load(f)
        if not isinstance(cache, dict):
            cache = {}
    except (OSError, ValueError):
        cache = {}
    changed = False
    for tier in config.tiers:
        old = cache.get(tier.name)
        if not refresh and isinstance(old, dict) and now - old.get("t", 0) < MAX_AGE:
            continue
        t = token(tier, now)
        if not t:
            continue
        try:
            cache[tier.name] = {"models": get(t), "t": now}
            changed = True
        except Exception:  # offline, or a login that no longer works: keep what was read before
            continue
    out = {k: v for k, v in cache.items() if k in {t.name for t in config.tiers}}
    if changed:
        os.makedirs(state_dir, exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(out, f)
        os.replace(tmp, path)
    return out


def command(args: Sequence[str], config: Config, state_dir: str) -> int:
    if any(a not in ("--json", "--refresh") for a in args):
        print(USAGE)
        return 0 if args[0] in ("-h", "--help") else 2
    now = time.time()
    got = read(config, state_dir, now, refresh="--refresh" in args)
    if "--json" in args:
        print(json.dumps({k: v["models"] for k, v in got.items()}))
        return 0
    for tier in config.tiers:
        v = got.get(tier.name)
        print("%s: %s" % (tier.name, ", ".join(v["models"]) if v else "unknown (no valid login to read it with yet)"))
    return 0
