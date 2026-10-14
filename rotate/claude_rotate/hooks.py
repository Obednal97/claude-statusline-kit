"""`rclaude _hook stop|stop-failure`, and the delayed SIGTERM that makes the launcher relaunch claude."""
from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from typing import Callable, Mapping, Optional, Tuple

from .config import Config
from .limits import best_available, classify_failure, compute_limited_until, stop_decision
from .profiles import profile_account
from .state import StateStore
from .transcript import login_since, trailing_limit

AUTH_BENCH_SECONDS = 3600
Killer = Callable[..., None]  # killer(launcher_pid) for a limit; killer(launcher_pid, transcript_path) at idle


def _context(env: Mapping[str, str]) -> Optional[Tuple[int, str]]:
    pid, account = env.get("RCLAUDE_PID"), env.get("RCLAUDE_ACCOUNT")
    if not pid or not pid.isdigit() or not account:
        return None
    return int(pid), account


def _request(reason: str, payload: dict, account: str, target: str, now: float) -> dict:
    return {
        "reason": reason,
        "session_id": payload.get("session_id"),
        "transcript_path": payload.get("transcript_path"),
        "cwd": payload.get("cwd"),
        "from_account": account,
        "target": target,
        "at": now,
    }


def launched_since(launcher: dict) -> float:
    """When the current claude process of this launcher started (older launchers don't record launched_at)."""
    return max(float(launcher.get(k, 0) or 0) for k in ("launched_at", "last_switch_at", "started_at"))


def logged_in_tier(config: Config, account: str) -> Optional[str]:
    """After a /login inside a session: the tier whose email matches the profile's login now, if any."""
    current = config.tier(account)
    acct = profile_account(current) if current else None
    email = (acct.email or "").lower() if acct else ""
    for tier in config.tiers:
        if email and tier.email and tier.email.lower() == email:
            return tier.name
    return None


def _record_session(state: dict, payload: dict, account: str, now: float) -> None:
    session_id = payload.get("session_id")
    if session_id:
        state["sessions"][session_id] = {"account": account, "at": now}


def handle_stop_failure(payload: dict, env: Mapping[str, str], config: Config, store: StateStore,
                        now: float, killer: Killer) -> Optional[dict]:
    ctx = _context(env)
    if ctx is None:
        return None
    pid, account = ctx
    error = str(payload.get("error") or "")
    message = str(payload.get("last_assistant_message") or payload.get("error_details") or "")
    kind = classify_failure(error, message)
    if kind == "other":
        store.log("stop-failure on %s ignored (error=%s): %s" % (account, error, message[:200]))
        return None
    launcher = store.read()["launchers"].get(str(pid), {})
    relogged = login_since(payload.get("transcript_path"), launched_since(launcher))
    blame = logged_in_tier(config, account) if relogged else account
    if relogged:
        store.log("a /login happened inside this %s session; blaming %s" % (account, blame or "an account not in the config"))
    with store.locked() as state:
        until = None
        if blame:
            acct = state["accounts"].setdefault(blame, {})
            if kind == "limit":
                acct["limited_until"] = compute_limited_until(message, acct, now)
            else:
                acct["limited_until"] = now + AUTH_BENCH_SECONDS
            acct["last_error"] = message[:500]
            acct["last_error_at"] = now
            until = acct["limited_until"]
        if blame:  # after a /login into an account no tier knows, record nothing rather than a wrong tier
            _record_session(state, payload, blame, now)
        unusable = state["launchers"].get(str(pid), {}).get("unusable", [])
        target = best_available(config.tiers, state["accounts"], now, unusable)
    if until:
        store.log("%s on %s (%s); limited until %s" % (
            kind, blame, message[:120], time.strftime("%a %H:%M", time.localtime(until))))
    # After a /login, relaunching even on the same tier helps: it restores that tier's own credentials.
    if target is None or (target.name == account and not relogged):
        store.log("no other account available; staying on %s (Claude Code's own wait applies)" % account)
        return None
    if store.has_switch(pid):
        return None
    request = _request("limit", payload, account, target.name, now)
    store.write_switch(pid, request)
    killer(pid)
    store.log("switch requested: %s -> %s (limit)" % (account, target.name))
    return request


def handle_stop(payload: dict, env: Mapping[str, str], config: Config, store: StateStore,
                now: float, killer: Killer) -> Optional[dict]:
    ctx = _context(env)
    if ctx is None:
        return None
    pid, account = ctx
    if store.has_switch(pid):
        return None
    if payload.get("stop_hook_active") or payload.get("background_tasks") or payload.get("session_crons"):
        return None  # not really idle: a Stop hook kept claude going, or background work would be killed
    launcher = store.read()["launchers"].get(str(pid), {})
    seen_as = account
    if login_since(payload.get("transcript_path"), launched_since(launcher)):
        seen_as = logged_in_tier(config, account)  # the session now runs as whoever it /login-ed into
    with store.locked() as state:
        if seen_as:
            _record_session(state, payload, seen_as, now)
        launcher = state["launchers"].get(str(pid), {})
        target = stop_decision(
            config.tiers, state["accounts"], account, now,
            float(launcher.get("last_switch_at") or 0), config.min_upswitch_interval_seconds,
            launcher.get("unusable", []),
        )
    if target is None:
        return None
    request = _request("rebalance", payload, account, target.name, now)
    store.write_switch(pid, request)
    killer(pid, payload.get("transcript_path"))
    store.log("switch requested: %s -> %s (rebalance at idle)" % (account, target.name))
    return request


def _limit_is_ours(found: dict, session_id: Optional[str], account: str, config: Config,
                   store: StateStore) -> bool:
    """Was the conversation's trailing limit hit by the account this session is now running on?"""
    record = store.read()["sessions"].get(session_id or "")
    if record and found.get("at") is not None and float(record.get("at", 0) or 0) >= found["at"]:
        return record.get("account") == account  # rclaude saw which account hit it
    tier = config.tier(account)
    if tier is None or tier.auth != "login" or not found.get("org"):
        return False  # can't tell; Claude Code only blocks when it is the same account
    acct = profile_account(tier)
    return bool(acct and acct.org == found["org"])


def handle_session_start(payload: dict, env: Mapping[str, str], config: Config, store: StateStore,
                         now: float, killer: Killer) -> Optional[dict]:
    """Resuming a conversation that ended on this account's limit makes Claude Code open its limit menu
    before anything is sent (no StopFailure), so switch here, before the menu can catch any typing."""
    ctx = _context(env)
    if ctx is None or payload.get("source") != "resume":
        return None
    pid, account = ctx
    if store.has_switch(pid):
        return None
    transcript = payload.get("transcript_path")
    found = trailing_limit(transcript, now) if transcript else None
    if found is None or not _limit_is_ours(found, payload.get("session_id"), account, config, store):
        return None
    with store.locked() as state:
        acct = state["accounts"].setdefault(account, {})
        acct["limited_until"] = max(float(acct.get("limited_until", 0) or 0), found["resets_at"])
        acct["last_error"] = "resumed a conversation that ended on this account's usage limit"
        acct["last_error_at"] = now
        unusable = state["launchers"].get(str(pid), {}).get("unusable", [])
        target = best_available(config.tiers, state["accounts"], now, unusable)
    store.log("limit on %s found on resume; limited until %s" % (
        account, time.strftime("%a %H:%M", time.localtime(found["resets_at"]))))
    if target is None or target.name == account:
        store.log("no other account available; staying on %s (Claude Code's own wait applies)" % account)
        return None
    request = _request("limit-on-resume", payload, account, target.name, now)
    store.write_switch(pid, request)
    killer(pid)
    store.log("switch requested: %s -> %s (resumed into its own limit)" % (account, target.name))
    return request


def parent_pid(pid: int) -> Optional[int]:
    proc = subprocess.run(["ps", "-o", "ppid=", "-p", str(pid)], capture_output=True, text=True)
    out = proc.stdout.strip()
    return int(out) if proc.returncode == 0 and out.isdigit() else None


def find_launcher_child(launcher_pid: int, start_pid: int, max_depth: int = 10) -> Optional[int]:
    """Walk up from start_pid to the process whose parent is the launcher: that's claude."""
    pid = start_pid
    for _ in range(max_depth):
        ppid = parent_pid(pid)
        if ppid is None or ppid <= 1:
            return None
        if ppid == launcher_pid:
            return pid
        pid = ppid
    return None


def _size(path: Optional[str]) -> Optional[int]:
    try:
        return os.path.getsize(path) if path else None
    except OSError:
        return None


def schedule_kill(launcher_pid: int, entry_path: str, store: StateStore,
                  transcript_path: Optional[str] = None) -> None:
    """Find claude now (while the hook's parents exist), then SIGTERM it from a detached helper after a delay.

    With transcript_path (a switch at idle), the helper backs off if the transcript grows in the meantime.
    """
    child = find_launcher_child(launcher_pid, os.getpid())
    if child is None:
        store.take_switch(launcher_pid)  # without a kill the request would only block later switches
        store.log("could not find claude under launcher %d; switch dropped" % launcher_pid)
        return
    cmd = [sys.executable, entry_path, "_kill", str(launcher_pid), str(child)]
    size = _size(transcript_path)
    if transcript_path and size is not None:
        cmd += [transcript_path, str(size)]
    subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     start_new_session=True)


def _busy_since(transcript_path: str, size: int) -> bool:
    """Did a new prompt or reply land after `size`? Claude Code always appends bookkeeping rows
    (stop_hook_summary, turn_duration) after the Stop hooks; those don't count."""
    try:
        with open(transcript_path, "rb") as f:
            f.seek(size)
            appended = f.read()
    except OSError:
        return False
    for raw in appended.split(b"\n"):
        try:
            row = json.loads(raw)
        except ValueError:
            continue
        if isinstance(row, dict) and row.get("type") in ("user", "assistant"):
            return True
    return False


def delayed_kill(launcher_pid: int, child_pid: int, delay: float, transcript_path: Optional[str] = None,
                 transcript_size: Optional[int] = None, store: Optional[StateStore] = None) -> bool:
    time.sleep(delay)
    if transcript_path is not None and transcript_size is not None and _busy_since(transcript_path, transcript_size):
        return False  # the session got busy again (a blocking Stop hook, or the user typed)
    if parent_pid(child_pid) != launcher_pid:  # gone, or the PID was reused
        return False
    if store is not None:
        store.mark_killed(launcher_pid)  # before the signal, so the launcher sees it when claude exits
    try:
        os.kill(child_pid, signal.SIGTERM)
    except ProcessLookupError:
        if store is not None:
            store.take_killed(launcher_pid)
        return False
    return True
