"""Per-account tokens in the macOS Keychain, or a JSON file when RCLAUDE_FAKE_KEYCHAIN is set (tests)."""
from __future__ import annotations

import getpass
import json
import os
import re
import subprocess
from typing import Optional

from .config import NAME_RE

SERVICE_PREFIX = "claude-rotate:"
TOKEN_RE = re.compile(r"[A-Za-z0-9._~+/=-]+")  # always use .fullmatch
NOT_FOUND_EXIT = 44  # `security find-generic-password` exit status when there is no such item


class KeychainError(Exception):
    pass


def service_name(name: str) -> str:
    return SERVICE_PREFIX + name


def _account() -> str:
    return os.environ.get("USER") or getpass.getuser()


def _fake_path() -> Optional[str]:
    return os.environ.get("RCLAUDE_FAKE_KEYCHAIN") or None


def get_token(name: str) -> Optional[str]:
    """The stored token; None if there isn't one; KeychainError if the Keychain can't be read."""
    fake = _fake_path()
    if fake:
        try:
            with open(fake) as f:
                value = json.load(f).get(name)
        except FileNotFoundError:
            return None
        return value if isinstance(value, str) and value else None
    proc = subprocess.run(
        ["security", "find-generic-password", "-s", service_name(name), "-a", _account(), "-w"],
        capture_output=True, text=True,
    )
    if proc.returncode == NOT_FOUND_EXIT:
        return None
    if proc.returncode != 0:
        detail = proc.stderr.strip().splitlines()
        raise KeychainError(detail[0] if detail else "security exited with %d" % proc.returncode)
    return proc.stdout.strip() or None


def set_token(name: str, token: str) -> None:
    if not NAME_RE.fullmatch(name or ""):
        raise KeychainError("invalid tier name %r" % name)
    if not TOKEN_RE.fullmatch(token or ""):
        raise KeychainError("that doesn't look like a token (empty or unexpected characters); nothing was saved")
    fake = _fake_path()
    if fake:
        data = {}
        if os.path.exists(fake):
            with open(fake) as f:
                data = json.load(f)
        data[name] = token
        with open(fake, "w") as f:
            json.dump(data, f)
        return
    # The token goes in on stdin through `security -i`, never on argv (other processes can read argv via ps).
    command = 'add-generic-password -U -s "%s" -a "%s" -w "%s"\n' % (service_name(name), _account(), token)
    subprocess.run(["security", "-i"], input=command, capture_output=True, text=True)
    if get_token(name) != token:
        raise KeychainError("the Keychain did not accept the token (is it locked?); nothing was saved")
