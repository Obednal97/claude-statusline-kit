"""`rclaude status`: one row per tier with usage, limits and live sessions."""
from __future__ import annotations

import time
from typing import Mapping, Optional

from .config import Config
from .limits import WINDOWS, available, blocked_until


def _when(ts: float, now: float) -> str:
    if not ts or ts <= now:
        return "-"
    hours, rem = divmod(int(ts - now), 3600)
    if hours >= 24:
        rel = "%dd%dh" % divmod(hours, 24)
    elif hours:
        rel = "%dh%02dm" % (hours, rem // 60)
    else:
        rel = "%dm" % (rem // 60)
    return "%s (in %s)" % (time.strftime("%a %H:%M", time.localtime(ts)), rel)


def _age(ts: float, now: float) -> str:
    if not ts:
        return "never"
    secs = int(now - ts)
    if secs < 60:
        return "%ds ago" % secs
    if secs < 3600:
        return "%dm ago" % (secs // 60)
    if secs < 86400:
        return "%dh ago" % (secs // 3600)
    return "%dd ago" % (secs // 86400)


def format_status(config: Config, state: Mapping, now: float, logins: Optional[Mapping[str, str]] = None) -> str:
    accounts = state.get("accounts", {})
    sessions: dict = {}
    for entry in state.get("launchers", {}).values():
        sessions[entry.get("account")] = sessions.get(entry.get("account"), 0) + 1
    rows = [("#", "ACCOUNT", "STATUS", "5H", "WEEK", "BLOCKED UNTIL", "USAGE SEEN", "SESSIONS", "LOGIN")]
    for i, tier in enumerate(config.tiers, 1):
        acct = accounts.get(tier.name, {})
        windows = [acct.get(w) if isinstance(acct.get(w), dict) else {} for w in WINDOWS]
        pct = ["%d%%" % round(w["pct"]) if "pct" in w else "-" for w in windows]
        seen = max(float(w.get("seen_at", 0) or 0) for w in windows)
        ok = available(acct, now)
        rows.append((
            str(i), tier.name, "available" if ok else "limited", pct[0], pct[1],
            "-" if ok else _when(blocked_until(acct, now), now), _age(seen, now), str(sessions.get(tier.name, 0)),
            (logins or {}).get(tier.name, "-"),
        ))
    widths = [max(len(r[c]) for r in rows) for c in range(len(rows[0]))]
    return "\n".join("  ".join(cell.ljust(widths[c]) for c, cell in enumerate(r)).rstrip() for r in rows)
