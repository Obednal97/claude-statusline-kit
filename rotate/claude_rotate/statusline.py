"""`rclaude _statusline`: record the active account's usage, then show the profile's own status line."""
from __future__ import annotations

import json
import os
import signal
import subprocess
from typing import Mapping

from .profiles import login_mismatch
from .state import StateStore
from .transcript import login_since

INNER_TIMEOUT_SECONDS = 5.0


def record_rate_limits(payload: dict, account: str, store: StateStore, now: float) -> None:
    limits = payload.get("rate_limits")
    if not isinstance(limits, dict):
        return
    updates = {}
    for window in ("five_hour", "seven_day"):
        w = limits.get(window)
        if isinstance(w, dict) and w.get("used_percentage") is not None and w.get("resets_at") is not None:
            updates[window] = {"pct": float(w["used_percentage"]), "resets_at": float(w["resets_at"]), "seen_at": now}
    if updates:
        with store.locked() as state:
            state["accounts"].setdefault(account, {}).update(updates)


def _fallback(account: str, payload: dict) -> str:
    limits = payload.get("rate_limits")
    parts = []
    if isinstance(limits, dict):
        for label, window in (("5h", "five_hour"), ("wk", "seven_day")):
            w = limits.get(window)
            if isinstance(w, dict) and isinstance(w.get("used_percentage"), (int, float)):
                parts.append("%s %d%%" % (label, round(w["used_percentage"])))
    return "[%s] %s" % (account, " · ".join(parts)) if parts else "[%s]" % account


def _run_inner(command: str, stdin_text: str, env: Mapping[str, str]) -> str:
    proc = subprocess.Popen(
        command, shell=True, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        text=True, env=dict(env), start_new_session=True,
    )
    try:
        out, _ = proc.communicate(stdin_text, timeout=INNER_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:
        os.killpg(proc.pid, signal.SIGKILL)  # whole group, so no grandchild keeps the pipe open
        proc.communicate()
        return ""
    return out.rstrip("\n") if proc.returncode == 0 else ""


def render(stdin_text: str, env: Mapping[str, str], store: StateStore, now: float, config=None) -> str:
    account = env.get("RCLAUDE_ACCOUNT")
    if not env.get("RCLAUDE_PID") or not account:
        return ""
    tier = config.tier(account) if config is not None else None
    mismatch = bool(tier and login_mismatch(tier))  # e.g. someone ran /login as another account mid-session
    try:
        payload = json.loads(stdin_text) if stdin_text.strip() else {}
    except ValueError:
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    if not mismatch and payload.get("transcript_path"):
        from .hooks import launched_since  # local import: hooks imports a lot, the status line rarely needs it
        launcher = store.read()["launchers"].get(env.get("RCLAUDE_PID", ""), {})
        mismatch = login_since(payload["transcript_path"], launched_since(launcher))
    try:
        if not mismatch:
            record_rate_limits(payload, account, store, now)
    except Exception as e:  # never break the status line
        store.log("statusline: could not record usage: %r" % e)
    if mismatch:
        account += "?"  # the label no longer matches who is logged in
    inner = env.get("RCLAUDE_INNER_STATUSLINE") or ""
    if inner:
        try:
            out = _run_inner(inner, stdin_text, env)
        except OSError:
            out = ""
        if out.strip():
            lines = out.split("\n")
            lines[0] = "[%s] %s" % (account, lines[0])
            return "\n".join(lines)
    return _fallback(account, payload)
