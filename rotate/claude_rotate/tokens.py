"""Per-account tokens: the macOS Keychain, or a 0600 JSON file (Linux, or wherever RCLAUDE_TOKEN_FILE points)."""
from __future__ import annotations

import getpass
import json
import os
import re
import subprocess
import sys
import tempfile
from typing import Optional

from .config import NAME_RE

SERVICE_PREFIX = "claude-rotate:"
TOKEN_RE = re.compile(r"[A-Za-z0-9._~+/=-]+")  # always use .fullmatch
NOT_FOUND_EXIT = 44  # `security find-generic-password` exit status when there is no such item
DEFAULT_TOKEN_FILE = "~/.config/claude-rotate/tokens.json"


class TokenStoreError(Exception):
    pass


def service_name(name: str) -> str:
    return SERVICE_PREFIX + name


def _account() -> str:
    return os.environ.get("USER") or getpass.getuser()


def token_file() -> Optional[str]:
    """The token file in use, or None when tokens live in the macOS Keychain."""
    override = os.environ.get("RCLAUDE_TOKEN_FILE")
    if override:
        return override
    if sys.platform == "darwin":
        return None
    return os.path.expanduser(DEFAULT_TOKEN_FILE)  # like Claude Code's own Linux login file


def describe(name: str) -> str:
    """Where `name`'s token is stored, for messages."""
    path = token_file()
    if path is None:
        return "the Keychain as %s" % service_name(name)
    return "%s (as %s)" % (path, name)


def _read_file(path: str) -> dict:
    try:
        with open(path) as f:
            data = json.load(f)
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as e:
        raise TokenStoreError("can't read %s: %s" % (path, e)) from None
    if not isinstance(data, dict):
        raise TokenStoreError("%s must hold a JSON object" % path)
    return data


def _write_file(path: str, data: dict) -> None:
    """Replace the file in one step, so it's never half-written; the result is 0600."""
    directory = os.path.dirname(os.path.abspath(path))
    os.makedirs(directory, mode=0o700, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".tokens-", dir=directory)  # created 0600
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(data, f)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def get_token(name: str) -> Optional[str]:
    """The stored token; None if there isn't one; TokenStoreError if the store can't be read."""
    path = token_file()
    if path is not None:
        value = _read_file(path).get(name)
        return value if isinstance(value, str) and value else None
    proc = subprocess.run(
        ["security", "find-generic-password", "-s", service_name(name), "-a", _account(), "-w"],
        capture_output=True, text=True,
    )
    if proc.returncode == NOT_FOUND_EXIT:
        return None
    if proc.returncode != 0:
        detail = proc.stderr.strip().splitlines()
        raise TokenStoreError(detail[0] if detail else "security exited with %d" % proc.returncode)
    return proc.stdout.strip() or None


def set_token(name: str, token: str) -> None:
    if not NAME_RE.fullmatch(name or ""):
        raise TokenStoreError("invalid tier name %r" % name)
    if not TOKEN_RE.fullmatch(token or ""):
        raise TokenStoreError("that doesn't look like a token (empty or unexpected characters); nothing was saved")
    path = token_file()
    if path is not None:
        data = _read_file(path)
        data[name] = token
        _write_file(path, data)
        return
    # The token goes in on stdin through `security -i`, never on argv (other processes can read argv via ps).
    command = 'add-generic-password -U -s "%s" -a "%s" -w "%s"\n' % (service_name(name), _account(), token)
    subprocess.run(["security", "-i"], input=command, capture_output=True, text=True)
    if get_token(name) != token:
        raise TokenStoreError("the Keychain did not accept the token (is it locked?); nothing was saved")
