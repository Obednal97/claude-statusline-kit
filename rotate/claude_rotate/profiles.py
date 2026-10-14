"""Which account a Claude Code profile is logged into, read from its .claude.json."""
from __future__ import annotations

import json
import os
from typing import NamedTuple, Optional

from .config import Tier


class ProfileAccount(NamedTuple):
    email: Optional[str]
    org: Optional[str]


def profile_json_path(tier: Tier) -> str:
    if tier.config_dir:
        return os.path.join(tier.config_dir, ".claude.json")
    return os.path.expanduser("~/.claude.json")  # the default profile keeps it in $HOME, not ~/.claude


def profile_account(tier: Tier) -> Optional[ProfileAccount]:
    try:
        with open(profile_json_path(tier)) as f:
            data = json.load(f)
    except (OSError, ValueError):
        return None
    acct = data.get("oauthAccount") if isinstance(data, dict) else None
    if not isinstance(acct, dict):
        return None
    return ProfileAccount(acct.get("emailAddress"), acct.get("organizationUuid"))


def login_mismatch(tier: Tier) -> Optional[str]:
    """A message when a login tier's profile is logged into another account than the configured email."""
    if tier.auth != "login" or not tier.email:
        return None
    acct = profile_account(tier)
    actual = acct.email if acct else None
    if actual and actual.lower() == tier.email.lower():
        return None
    run = "CLAUDE_CONFIG_DIR=%s claude" % tier.config_dir if tier.config_dir else "claude"
    return "%s is logged in as %s, expected %s. Fix: run `%s`, then /login as %s" % (
        tier.config_dir or "~/.claude", actual or "nobody", tier.email, run, tier.email)


def login_labels(config) -> dict:
    """tier name -> who it is really logged in as, for status tables."""
    labels = {}
    for tier in config.tiers:
        if tier.auth == "token":
            labels[tier.name] = "token"
            continue
        acct = profile_account(tier)
        label = (acct.email if acct else None) or "nobody"
        if login_mismatch(tier):
            label += "(expected:%s)" % tier.email
        labels[tier.name] = label
    return labels
