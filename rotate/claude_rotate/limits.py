"""Pure decision logic: classify failures, parse reset times, choose tiers. No I/O."""
from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Collection, Mapping, Optional, Sequence, Tuple

from .config import Tier

WINDOWS = ("five_hour", "seven_day")
FRESH_WINDOW_SECONDS = 900
FALLBACK_SECONDS = 3600
# 2.1.296 wordings: "You've hit your <session|weekly|Opus|Sonnet|Fable|fast|usage> limit", the generic "You've hit
# your limit", "You've reached your Fable limit", "You've hit your team's shared budget", "You're out of extra
# usage" and "You're out of usage credits". Throttles ("Server is temporarily limiting requests") match none.
LIMIT_RE = re.compile(
    r"(?:hit|reached) your\b(?P<kind>[^·\n]{0,40}?)\b(?:limit|budget)\b|(?P<out>out of (?:extra usage|usage credits))",
    re.IGNORECASE)
AUTH_ERRORS = frozenset({"authentication_failed", "account_on_hold", "oauth_org_not_allowed", "billing_error"})

_MONTHS = ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"]
_DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
_TIME = r"(\d{1,2})(?::(\d{2}))?\s*(am|pm)"
_RE_TIME = re.compile(r"^" + _TIME + r"$")
_RE_DAY_TIME = re.compile(r"^(mon|tue|wed|thu|fri|sat|sun)[a-z]*,?\s+" + _TIME + r"$")
_RE_DATE_TIME = re.compile(
    r"^(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\s+(\d{1,2})(?:,\s*|\s+at\s+)" + _TIME + r"$")


def classify_failure(error: str, message: str) -> str:
    """'limit' for a plan usage limit, 'auth' for an account problem, otherwise 'other'."""
    if error == "rate_limit" and LIMIT_RE.search(message or ""):
        return "limit"
    if error in AUTH_ERRORS:
        return "auth"
    return "other"


def limit_kind(message: str) -> Optional[str]:
    """'session', 'weekly', a model name, 'usage', or None for the generic "You've hit your limit"."""
    m = LIMIT_RE.search(message or "")
    if not m:
        return None
    if m.group("out"):
        return "usage"
    return m.group("kind").strip().lower() or None


def _hour_minute(hour: str, minute: Optional[str], ampm: str) -> Optional[Tuple[int, int]]:
    h, m = int(hour), int(minute) if minute else 0
    if not (1 <= h <= 12 and 0 <= m <= 59):
        return None
    return h % 12 + (12 if ampm == "pm" else 0), m


def _reset_text(message: str) -> Optional[str]:
    lowered = (message or "").lower()
    idx = lowered.rfind("resets ")
    if idx < 0:
        return None
    text = lowered[idx + len("resets "):].split("\n")[0]
    text = re.split(r"\s+·|\s+\(|\s+-\s", text)[0]  # drop " · more text" and " (Europe/London)"
    return text.strip().rstrip(".")


def parse_reset(message: str, now: float) -> Optional[float]:
    """Epoch seconds of the next reset named in a limit message ("resets 3:45pm" etc.), in local time."""
    text = _reset_text(message)
    if not text:
        return None
    base = datetime.fromtimestamp(now)
    m = _RE_TIME.match(text)
    if m:
        hm = _hour_minute(*m.groups())
        if hm is None:
            return None
        cand = base.replace(hour=hm[0], minute=hm[1], second=0, microsecond=0)
        if cand <= base:
            cand += timedelta(days=1)
        return cand.timestamp()
    m = _RE_DAY_TIME.match(text)
    if m:
        hm = _hour_minute(m.group(2), m.group(3), m.group(4))
        if hm is None:
            return None
        ahead = (_DAYS.index(m.group(1)) - base.weekday()) % 7
        cand = (base + timedelta(days=ahead)).replace(hour=hm[0], minute=hm[1], second=0, microsecond=0)
        if cand <= base:
            cand += timedelta(days=7)
        return cand.timestamp()
    m = _RE_DATE_TIME.match(text)
    if m:
        hm = _hour_minute(m.group(3), m.group(4), m.group(5))
        if hm is None:
            return None
        month, day = _MONTHS.index(m.group(1)) + 1, int(m.group(2))
        for year in (base.year, base.year + 1):
            try:
                cand = datetime(year, month, day, hm[0], hm[1])
            except ValueError:
                return None
            if cand > base:
                return cand.timestamp()
    return None


def _window_blocks(win: object, now: float) -> bool:
    return (isinstance(win, dict) and float(win.get("pct", 0) or 0) >= 100
            and float(win.get("resets_at", 0) or 0) > now)


def available(acct: Mapping, now: float) -> bool:
    if now < float(acct.get("limited_until", 0) or 0):
        return False
    return not any(_window_blocks(acct.get(w), now) for w in WINDOWS)


def blocked_until(acct: Mapping, now: float) -> float:
    times = [float(acct.get("limited_until", 0) or 0)]
    times += [float(acct[w]["resets_at"]) for w in WINDOWS if _window_blocks(acct.get(w), now)]
    return max(times)


def best_available(tiers: Sequence[Tier], accounts: Mapping, now: float,
                   unusable: Collection[str] = ()) -> Optional[Tier]:
    for t in tiers:
        if t.name not in unusable and available(accounts.get(t.name, {}), now):
            return t
    return None


def pick(tiers: Sequence[Tier], accounts: Mapping, now: float,
         unusable: Collection[str] = ()) -> Optional[Tier]:
    """Best available tier; if all are limited, the usable one that resets soonest; None if none usable."""
    best = best_available(tiers, accounts, now, unusable)
    if best is not None:
        return best
    usable = [t for t in tiers if t.name not in unusable]
    if not usable:
        return None
    return min(usable, key=lambda t: blocked_until(accounts.get(t.name, {}), now))  # min() keeps priority on ties


def _fresh_reset(acct: Mapping, window: str, now: float, full_only: bool) -> Optional[float]:
    w = acct.get(window)
    if not isinstance(w, dict):
        return None
    resets_at = float(w.get("resets_at", 0) or 0)
    if resets_at <= now or now - float(w.get("seen_at", 0) or 0) > FRESH_WINDOW_SECONDS:
        return None
    if full_only and float(w.get("pct", 0) or 0) < 100:
        return None
    return resets_at


def compute_limited_until(message: str, acct: Mapping, now: float) -> float:
    kind = limit_kind(message)
    window = {"session": "five_hour", "weekly": "seven_day"}.get(kind or "")
    if window:
        resets_at = _fresh_reset(acct, window, now, full_only=False)
        if resets_at is not None:
            return resets_at
    elif kind in (None, "usage"):  # generic message: trust whichever fresh window is full
        full = [r for r in (_fresh_reset(acct, w, now, full_only=True) for w in WINDOWS) if r is not None]
        if full:
            return max(full)
    parsed = parse_reset(message, now)
    if parsed is not None and parsed > now:
        return parsed
    return now + FALLBACK_SECONDS


def stop_decision(tiers: Sequence[Tier], accounts: Mapping, current: str, now: float,
                  last_switch_at: float, min_interval: int, unusable: Collection[str] = ()) -> Optional[Tier]:
    """At idle: the tier to switch to, or None to stay."""
    target = best_available(tiers, accounts, now, unusable)
    if target is None or target.name == current:
        return None
    if current in unusable or not available(accounts.get(current, {}), now):
        return target
    names = [t.name for t in tiers]
    current_index = names.index(current) if current in names else len(names)
    if names.index(target.name) < current_index and now - last_switch_at >= min_interval:
        return target
    return None
