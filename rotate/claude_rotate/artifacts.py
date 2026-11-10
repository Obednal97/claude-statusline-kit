"""The artifact ledger (`<state dir>/artifacts.jsonl`) and `rclaude artifacts`.

An artifact published with Claude Code's Artifact tool is private to the account that published it, and sharing
it with the other accounts is a manual step in its Share menu on claude.ai. The PostToolUse hook records every
publish; `rclaude artifacts` lists the ones still to share and marks them done.

Ledger lines (append-only, 0600; readers fold by url, the latest publish wins, unknown fields and events are ignored):
  {"t", "event": "publish", "url", "id", "title", "account", "main", "session", "cwd"}  plus "share": false
      when the account is kept out of sharing (share_artifacts: false)
  {"t", "event": "shared", "url"}
"""
from __future__ import annotations

import json
import os
import re
import time
from typing import List, Mapping, Optional, Sequence

from .config import Config, Tier
from .status import _age

URL_RE = re.compile(r"https://claude\.ai/(?:code/)?artifact/[A-Za-z0-9_-]+")
USAGE = """usage: rclaude artifacts [--all]
       rclaude artifacts shared <number|url|all> ...

Artifacts are private to the account that published them. Lists those published on another account and not yet
shared (--all: every one). To share one, open its link signed in as that account, choose Share, and add the
accounts listed; then mark it with `rclaude artifacts shared <number>`."""


def ledger_path(state_dir: str) -> str:
    return os.path.join(state_dir, "artifacts.jsonl")


def append(state_dir: str, entry: dict) -> None:
    fd = os.open(ledger_path(state_dir), os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
    try:
        os.write(fd, (json.dumps(entry, ensure_ascii=False) + "\n").encode("utf-8"))
    finally:
        os.close(fd)


def _text(response: object) -> str:
    """The text of a tool result given as a string or as content blocks."""
    if isinstance(response, str):
        return response
    if isinstance(response, dict):
        response = response.get("content")
    if isinstance(response, list):
        return "\n".join(b.get("text", "") for b in response if isinstance(b, dict) and isinstance(b.get("text"), str))
    return ""


def published(payload: Mapping) -> Optional[dict]:
    """{url, id, title} for a successful page publish; None for other actions, asset uploads and failures."""
    tool_input = payload.get("tool_input")
    tool_input = tool_input if isinstance(tool_input, dict) else {}
    if tool_input.get("action", "publish") != "publish" or tool_input.get("asset"):
        return None
    response = payload.get("tool_response")
    result = response if isinstance(response, dict) else {}
    if result.get("is_error") or result.get("error") or result.get("owned") is False:
        return None  # owned: false is a publish to someone else's artifact: its owner has it already
    url = result.get("url") if isinstance(result.get("url"), str) else None
    if url is None:
        text = _text(response)
        if text.lstrip().lower().startswith(("error", "<tool_use_error>")):
            return None
        url = text  # the first artifact link in "Published <file> at <url> (Version 1 ...)"
    found = URL_RE.search(url)
    if not found:
        return None
    title = result.get("title") or tool_input.get("title")
    artifact_id = result.get("artifact_id")
    return {"url": found.group(0), "id": artifact_id if isinstance(artifact_id, str) else None,
            "title": title if isinstance(title, str) else None}


def share_targets(config: Config, owner: str) -> List[Tier]:
    """The accounts an artifact published on `owner` should be shared with."""
    tier = config.tier(owner)
    if tier is not None and not tier.share_artifacts:
        return []
    email = (tier.email or "").lower() if tier else ""
    return [t for t in config.tiers
            if t.name != owner and t.share_artifacts and not (email and (t.email or "").lower() == email)]


def handle_publish(payload: Mapping, env: Mapping[str, str], config: Config, state_dir: str,
                   now: float) -> Optional[str]:
    """Record a publish; returns a note for Claude when the artifact needs sharing."""
    account = env.get("RCLAUDE_ACCOUNT")
    found = published(payload) if account else None
    if found is None:
        return None
    tier = config.tier(account)
    entry = dict(t=now, event="publish", url=found["url"], id=found["id"], title=found["title"], account=account,
                 main=config.tiers[0].name == account, session=payload.get("session_id"), cwd=payload.get("cwd"))
    if tier is not None and not tier.share_artifacts:
        entry["share"] = False
    append(state_dir, entry)
    if not needs_share(entry, config):
        return None
    return ("rclaude: this artifact was published under the account %s, so the user's other accounts can't open "
            "it yet. Tell the user that in one line, and that `rclaude artifacts` lists what to share." % account)


def needs_share(record: Mapping, config: Config) -> bool:
    return (not record.get("main") and record.get("share", True) is not False and not record.get("shared")
            and bool(share_targets(config, str(record.get("account")))))


def fold(state_dir: str) -> List[dict]:
    """One record per url, in order of first publish, with "shared" set once a shared event follows."""
    records: dict = {}
    try:
        with open(ledger_path(state_dir), encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
    except OSError:
        return []
    for line in lines:
        try:
            row = json.loads(line)
        except ValueError:
            continue
        url = row.get("url") if isinstance(row, dict) else None
        if not isinstance(url, str):
            continue
        if row.get("event") == "publish":
            rec = records.setdefault(url, {"url": url, "shared": False})
            rec.update({k: v for k, v in row.items() if k not in ("event", "shared", "url")})
            rec["share"] = row.get("share") is not False
        elif row.get("event") == "shared" and url in records:
            records[url]["shared"] = True
    return list(records.values())


def _status(record: Mapping, config: Config) -> str:
    if record.get("shared"):
        return "shared"
    if record.get("main"):
        return "main account"
    return "to share" if needs_share(record, config) else "not shared"


def _target_list(config: Config, owner: str) -> str:
    return ", ".join("%s (%s)" % (t.name, t.email) if t.email else t.name for t in share_targets(config, owner))


def format_list(records: Sequence[dict], config: Config, now: float, show_all: bool = False) -> str:
    numbered = [(i, r) for i, r in enumerate(records, 1) if show_all or needs_share(r, config)]
    if not numbered:
        return "No artifacts recorded yet." if not records else "Nothing to share."
    groups: dict = {}
    for i, rec in numbered:
        groups.setdefault(str(rec.get("account")), []).append((i, rec))
    order = [t.name for t in config.tiers]
    out: List[str] = []
    for account in sorted(groups, key=lambda a: order.index(a) if a in order else len(order)):
        targets = _target_list(config, account)
        out += ([""] if out else []) + [account if show_all or not targets else "%s: share with %s" % (account, targets)]
        for i, rec in groups[account]:
            project = os.path.basename(str(rec.get("cwd") or "").rstrip("/")) or "-"
            cells = ["%3d" % i, rec.get("title") or "(untitled)", rec["url"], _age(float(rec.get("t") or 0), now),
                     project] + ([_status(rec, config)] if show_all else [])
            out.append("  ".join(cells))
    if not show_all:
        out += ["", "Open each link signed in as its account, choose Share and add the accounts listed.",
                "Then: rclaude artifacts shared <number|url|all>"]
    return "\n".join(out)


def mark_shared(args: Sequence[str], records: Sequence[dict], config: Config, state_dir: str,
                now: float) -> List[str]:
    """Append a shared event for each artifact named; returns the urls marked. Raises ValueError on a bad name."""
    urls: List[str] = []
    for arg in args:
        if arg == "all":
            urls += [r["url"] for r in records if needs_share(r, config)]
        elif arg.isdigit() and 1 <= int(arg) <= len(records):
            urls.append(records[int(arg) - 1]["url"])
        else:
            found = URL_RE.search(arg)
            if not found or found.group(0) not in {r["url"] for r in records}:
                raise ValueError("no recorded artifact %s" % arg)
            urls.append(found.group(0))
    for url in dict.fromkeys(urls):
        append(state_dir, {"t": now, "event": "shared", "url": url})
    return list(dict.fromkeys(urls))


def command(args: Sequence[str], config: Config, state_dir: str) -> int:
    now = time.time()
    records = fold(state_dir)
    if not args or args == ["--all"]:
        print(format_list(records, config, now, show_all=bool(args)))
        return 0
    if args[0] == "shared" and len(args) > 1:
        try:
            urls = mark_shared(args[1:], records, config, state_dir, now)
        except ValueError as e:
            print("rclaude: %s (see rclaude artifacts --all)" % e)
            return 1
        print("Marked %d as shared." % len(urls) if urls else "Nothing to mark.")
        return 0
    print(USAGE)
    return 0 if args[0] in ("-h", "--help") else 2
