"""Read the end of a Claude Code transcript (JSONL): did the conversation stop on a usage limit?"""
from __future__ import annotations

import calendar
import json
import os
import time
from typing import Iterator, Optional

from .limits import LIMIT_RE, parse_reset

TAIL_BYTES = 4_000_000  # transcripts reach 50 MB+; the end is what matters


def _entries(path: str, tail_bytes: Optional[int]) -> Iterator[dict]:
    with open(path, "rb") as f:
        f.seek(0, os.SEEK_END)
        start = max(0, f.tell() - tail_bytes) if tail_bytes else 0
        f.seek(start)
        lines = f.read().split(b"\n")
    if start > 0:
        lines = lines[1:]  # the first line of a tail is usually partial
    for raw in lines:
        if not raw.strip():
            continue
        try:
            e = json.loads(raw)
        except ValueError:
            continue
        if isinstance(e, dict):
            yield e


def _text(entry: dict) -> str:
    msg = entry.get("message")
    content = msg.get("content") if isinstance(msg, dict) else None
    if isinstance(content, str):
        return content
    for c in content if isinstance(content, list) else []:
        if isinstance(c, dict) and c.get("type") == "text":
            return str(c.get("text") or "")
    return ""


def _is_limit_error(entry: dict) -> bool:
    if not entry.get("isApiErrorMessage"):
        return False
    return entry.get("apiError") == "usage_limit_reached" or (
        entry.get("error") == "rate_limit" and bool(LIMIT_RE.search(_text(entry))))


def _epoch(ts: object) -> Optional[float]:
    try:
        return float(calendar.timegm(time.strptime(str(ts)[:19], "%Y-%m-%dT%H:%M:%S")))
    except ValueError:
        return None


def _is_prompt(entry: dict) -> bool:
    """A prompt (or local command) typed by the user, as opposed to a tool result fed back to the model."""
    if entry.get("type") != "user" or entry.get("isMeta"):
        return False
    msg = entry.get("message")
    content = msg.get("content") if isinstance(msg, dict) else None
    if isinstance(content, list):
        return not any(isinstance(c, dict) and c.get("type") == "tool_result" for c in content)
    return isinstance(content, str)


def _scan(path: str, now: float, tail_bytes: Optional[int]) -> Optional[dict]:
    org = None
    found = None
    for e in _entries(path, tail_bytes):
        att = e.get("attachment")
        if _is_prompt(e):
            org = None  # credential_org follows the prompt it applies to; token-auth prompts get none
        elif isinstance(att, dict) and att.get("type") == "credential_org":
            org = att.get("organizationUuid")
        elif e.get("type") == "assistant":
            if _is_limit_error(e):
                quota = e.get("quotaLimits") if isinstance(e.get("quotaLimits"), dict) else {}
                resets = quota.get("resetsAt")
                if not isinstance(resets, (int, float)):
                    resets = parse_reset(_text(e), now)
                found = {"resets_at": float(resets) if resets else None, "org": org, "at": _epoch(e.get("timestamp"))}
            elif not e.get("isApiErrorMessage"):
                found = None  # a real reply came after it: the conversation isn't stuck on a limit
    if found and found["resets_at"] and found["resets_at"] > now:
        return found
    return None


def trailing_limit(path: str, now: float, tail_bytes: int = TAIL_BYTES) -> Optional[dict]:
    """{"resets_at", "org", "at"} if the conversation ended on a usage limit that hasn't reset; else None.

    "org" is the organizationUuid Claude Code recorded (credential_org) for the prompt that failed. Login sessions
    record it when the account changes; token sessions never do. None means "can't tell", never a guess.
    """
    try:
        return _scan(path, now, tail_bytes)
    except OSError:
        return None


LOGIN_MARK = b"<local-command-stdout>Login successful"


def login_since(path: Optional[str], since: float, tail_bytes: int = 1_000_000) -> bool:
    """True if a /login succeeded inside this conversation at or after `since` (epoch seconds).

    A /login typed in a session switches that session to the new account and rewrites the profile's
    login, so the tier the session was launched as no longer says who it is.
    """
    if not path:
        return False
    try:
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            f.seek(max(0, f.tell() - tail_bytes))
            data = f.read()
    except OSError:
        return False
    if LOGIN_MARK not in data:  # cheap: the status line calls this on every refresh
        return False
    for raw in data.split(b"\n"):
        if LOGIN_MARK not in raw:
            continue
        try:
            e = json.loads(raw)
        except ValueError:
            continue
        if not isinstance(e, dict) or e.get("type") != "user":
            continue
        msg = e.get("message")
        content = msg.get("content") if isinstance(msg, dict) else None
        if not isinstance(content, str) or not content.lstrip().startswith(LOGIN_MARK.decode()):
            continue  # e.g. a tool result or reply that merely mentions the text
        at = _epoch(e.get("timestamp"))
        if at is not None and at >= since - 1:  # transcript timestamps have whole seconds
            return True
    return False
