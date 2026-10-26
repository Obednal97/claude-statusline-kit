"""Command dispatch for the rclaude entry point."""
from __future__ import annotations

import getpass
import json
import os
import sys
import time
from typing import List, Optional

from . import hooks, launcher, limits, statusline, tokens
from .config import NAME_RE, ConfigError, load_config
from .profiles import login_labels
from .state import StateStore
from .status import format_status


def _hook(event: str, store: StateStore, entry_path: str) -> int:
    if not os.environ.get("RCLAUDE_PID"):
        return 0
    try:
        config = load_config()
        try:
            payload = json.loads(sys.stdin.read() or "{}")
        except ValueError:
            payload = {}
        if not isinstance(payload, dict):
            payload = {}

        def killer(pid: int, transcript_path: Optional[str] = None) -> None:
            hooks.schedule_kill(pid, entry_path, store, transcript_path)

        handler = {"stop": hooks.handle_stop, "stop-failure": hooks.handle_stop_failure,
                   "session-start": hooks.handle_session_start}[event]
        handler(payload, os.environ, config, store, time.time(), killer)
    except Exception as e:  # a hook must never break the session
        store.log("hook %s failed: %r" % (event, e))
    return 0


def _statusline(store: StateStore) -> int:
    try:
        try:
            config = load_config()
        except ConfigError:
            config = None
        out = statusline.render(sys.stdin.read(), os.environ, store, time.time(), config)
    except Exception as e:  # never break the status line
        store.log("statusline failed: %r" % e)
        out = ""
    if out:
        print(out)
    return 0


def _add_token(args: List[str], store: StateStore) -> int:
    if len(args) != 1 or not NAME_RE.fullmatch(args[0]):
        print("usage: rclaude add-token <name>   (a tier name from accounts.json)", file=sys.stderr)
        return 2
    name = args[0]
    try:
        tier = load_config().tier(name)
        if tier is None or tier.auth != "token":
            print('rclaude: note: "%s" is not a token tier in accounts.json (yet)' % name, file=sys.stderr)
    except ConfigError:
        pass
    token = getpass.getpass("Paste the `claude setup-token` token for %s (input hidden): " % name).strip()
    try:
        tokens.set_token(name, token)
    except tokens.TokenStoreError as e:
        print("rclaude: %s" % e, file=sys.stderr)
        return 1
    print("Saved to %s" % tokens.describe(name))
    store.log("token stored for %s" % name)
    if _clear_auth_block(store, name, time.time()):
        print("Lifted the block on %s from its last login error; it's available again" % name)
        store.log("auth block on %s lifted by add-token" % name)
    return 0


def _clear_auth_block(store: StateStore, name: str, now: float) -> bool:
    """Drop a block left by a login error (a bad token), so a new token is tried at once. A plan limit is kept:
    a new token for the same account doesn't reset it. state.json records no kind, so the message tells them apart."""
    with store.locked() as state:
        acct = state["accounts"].get(name)
        if not acct or float(acct.get("limited_until", 0) or 0) <= now:
            return False
        if limits.LIMIT_RE.search(str(acct.get("last_error") or "")):
            return False
        del acct["limited_until"]
        return True


def main(argv: List[str], entry_path: str) -> int:
    store = StateStore()
    if argv[:1] == ["_statusline"]:
        return _statusline(store)
    if argv[:1] == ["_hook"] and len(argv) == 2 and argv[1] in ("stop", "stop-failure", "session-start"):
        return _hook(argv[1], store, entry_path)
    if argv[:1] == ["_kill"]:
        if len(argv) in (3, 5) and argv[1].isdigit() and argv[2].isdigit():
            transcript, size = (argv[3], int(argv[4])) if len(argv) == 5 and argv[4].isdigit() else (None, None)
            killed = hooks.delayed_kill(int(argv[1]), int(argv[2]), float(os.environ.get("RCLAUDE_KILL_DELAY", "1.0")),
                                        transcript, size, store)
            if not killed:
                store.take_switch(int(argv[1]))
                store.log("switch for launcher %s dropped: claude was busy again or already gone" % argv[1])
        return 0
    if argv[:1] == ["add-token"]:
        return _add_token(argv[1:], store)
    try:
        config = load_config()
    except ConfigError as e:
        print("rclaude: config error: %s" % e, file=sys.stderr)
        return 2
    if argv[:1] == ["status"]:
        print(format_status(config, store.read(), time.time(), login_labels(config)))
        return 0
    return launcher.run(argv, config, store, entry_path,
                        claude_bin=os.environ.get("RCLAUDE_CLAUDE_BIN") or "claude")
