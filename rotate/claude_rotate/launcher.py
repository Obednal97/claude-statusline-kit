"""Run claude on the best tier, and relaunch it on another tier when a hook requests a switch."""
from __future__ import annotations

import json
import os
import shlex
import signal
import subprocess
import sys
import time
from typing import Callable, Dict, List, Mapping, Optional, Sequence, Set, TextIO, Tuple

from . import tokens
from .config import Config, Tier
from .limits import best_available, pick
from .profiles import login_labels, login_mismatch
from .state import StateStore
from .status import format_status

# Option arities and subcommands as listed by `claude --help` in Claude Code 2.1.299.
SUBCOMMANDS = frozenset({
    "agents", "attach", "auth", "auto-mode", "doctor", "gateway", "import", "install", "kill", "logs",
    "mcp", "plugin", "plugins", "purge", "respawn", "rm", "setup-token", "stop",
    "ultrareview", "update", "upgrade",
})
PASSTHROUGH_FLAGS = frozenset({"-p", "--print", "--version", "-v", "--help", "-h", "--cloud"})
REQUIRED_VALUE = frozenset({
    "--agent", "--agents", "--append-system-prompt", "--append-system-prompt-file", "--autocompact",
    "--debug-file", "--effort", "--environment", "--fallback-model", "--input-format", "--json-schema",
    "--max-budget-usd", "--model", "-n", "--name", "--output-format", "--permission-mode", "--permission-prompts",
    "--plugin-dir", "--plugin-url", "--remote-control-session-name-prefix", "--session-id", "--setting-sources",
    "--settings", "--system-prompt", "--system-prompt-file", "--system-prompt-snapshot",
})
OPTIONAL_VALUE = frozenset({
    "--cloud", "-d", "--debug", "--from-pr", "--prompt-suggestions", "--remote-control", "-r", "--resume",
    "--teleport", "-w", "--worktree",
})
VARIADIC = frozenset({
    "--add-dir", "--allowedTools", "--allowed-tools", "--betas", "--disallowedTools", "--disallowed-tools",
    "--file", "--mcp-config", "--tools",
})
# Session selection is replaced on relaunch; --worktree would create yet another worktree.
DROP_ON_RELAUNCH = frozenset({
    "-c", "--continue", "--fork-session", "-r", "--resume", "--session-id", "--from-pr", "--teleport",
    "-w", "--worktree",
})
MANAGED_ENV = ("CLAUDE_CONFIG_DIR", "CLAUDE_CODE_OAUTH_TOKEN", "RCLAUDE_ACCOUNT", "RCLAUDE_PID",
               "RCLAUDE_INNER_STATUSLINE", "RCLAUDE_AUTH", "RCLAUDE_ACCOUNT_EMAIL")
LIMIT_PROMPT = ("You were interrupted by a usage limit and are now running on a different account. "
                "Continue where you left off.")


def handoff_prompt(transcript_path: str) -> str:
    return ("You're continuing a task from a session that ran under a different account. "
            "Its transcript is at %s (JSONL). Read the last part of it to recover the context, "
            "then continue the task." % transcript_path)


def parse_args(args: Sequence[str]) -> List[Tuple[str, List[str]]]:
    """Split claude args into ("opt", [option, values...]) and ("pos", [value]) items."""
    items: List[Tuple[str, List[str]]] = []
    i = 0
    while i < len(args):
        a = args[i]
        if a == "--":
            items.extend(("pos", [x]) for x in args[i + 1:])
            break
        if not a.startswith("-") or a == "-":
            items.append(("pos", [a]))
            i += 1
            continue
        name = a.split("=", 1)[0]
        j = i + 1
        if "=" not in a:
            if name in REQUIRED_VALUE:
                j = min(i + 2, len(args))
            elif name in OPTIONAL_VALUE:
                j += 1 if j < len(args) and not args[j].startswith("-") else 0
            elif name in VARIADIC:
                while j < len(args) and not args[j].startswith("-"):
                    j += 1
        items.append(("opt", list(args[i:j])))
        i = j
    return items


def first_positional(args: Sequence[str]) -> Optional[str]:
    return next((values[0] for kind, values in parse_args(args) if kind == "pos"), None)


def is_config_command(args: Sequence[str]) -> bool:
    return first_positional(args) in SUBCOMMANDS


def is_passthrough(args: Sequence[str]) -> bool:
    """Subcommands, print mode, help, version and cloud sessions run once, without rotation."""
    options = (values[0].split("=", 1)[0] for kind, values in parse_args(args) if kind == "opt")
    return any(o in PASSTHROUGH_FLAGS for o in options) or is_config_command(args)


def strip_session_args(args: Sequence[str]) -> List[str]:
    """The user's options minus session selection, and minus the prompt (already answered), for relaunches."""
    out: List[str] = []
    for kind, values in parse_args(args):
        if kind == "opt" and values[0].split("=", 1)[0] not in DROP_ON_RELAUNCH:
            out.extend(values)
    return out


def build_env(base: Mapping[str, str], tier: Tier, token: Optional[str], launcher_pid: Optional[int] = None,
              inner_statusline: Optional[str] = None) -> Dict[str, str]:
    env = {k: v for k, v in base.items() if k not in MANAGED_ENV}
    if tier.config_dir:
        env["CLAUDE_CONFIG_DIR"] = tier.config_dir
    if token:
        env["CLAUDE_CODE_OAUTH_TOKEN"] = token
    if launcher_pid is not None:
        env["RCLAUDE_ACCOUNT"] = tier.name
        env["RCLAUDE_PID"] = str(launcher_pid)
        env["RCLAUDE_INNER_STATUSLINE"] = inner_statusline or ""
        env["RCLAUDE_AUTH"] = tier.auth  # status-line widgets: token sessions don't use the profile's login
        env["RCLAUDE_ACCOUNT_EMAIL"] = tier.email or ""
    return env


def inner_statusline_for(tier: Tier) -> Optional[str]:
    """The profile's own status line command, which rclaude's status line wraps."""
    directory = tier.config_dir or os.path.expanduser("~/.claude")
    try:
        with open(os.path.join(directory, "settings.json")) as f:
            settings = json.load(f)
    except (OSError, ValueError):
        return None
    line = settings.get("statusLine") if isinstance(settings, dict) else None
    if not isinstance(line, dict) or line.get("type") != "command" or not isinstance(line.get("command"), str):
        return None
    if "rclaude" in line["command"] and "_statusline" in line["command"]:
        return None  # never wrap ourselves
    return line["command"]


def generated_settings(entry_path: str) -> dict:
    cmd = shlex.quote(entry_path)

    def hook(command: str) -> list:
        return [{"hooks": [{"type": "command", "command": command}]}]

    return {
        "statusLine": {"type": "command", "command": cmd + " _statusline"},
        "hooks": {"Stop": hook(cmd + " _hook stop"), "StopFailure": hook(cmd + " _hook stop-failure"),
                  "SessionStart": hook(cmd + " _hook session-start")},
    }


def write_generated_settings(store: StateStore, entry_path: str) -> str:
    tmp = "%s.tmp-%d" % (store.settings_path, os.getpid())
    with open(tmp, "w") as f:
        json.dump(generated_settings(entry_path), f, indent=2)
    os.replace(tmp, store.settings_path)
    return store.settings_path


def plan_relaunch(request: dict, from_tier: Tier, to_tier: Tier,
                  return_session: Optional[dict]) -> Tuple[List[str], Optional[dict]]:
    """Session args for the relaunch, and the return_session to remember afterwards."""
    session_id = request.get("session_id")
    transcript = request.get("transcript_path")
    if from_tier.config_dir == to_tier.config_dir:
        if not session_id:
            return [], return_session
        args = ["--resume", session_id]
        # Only when it's really another account: if the target had no usable token, the launcher falls back to
        # the limited account itself, and the prompt would be false and hit the limit again.
        if request.get("reason") == "limit" and from_tier.name != to_tier.name:
            args.append(LIMIT_PROMPT)
        return args, return_session
    handoff = [handoff_prompt(transcript)] if transcript else []
    if (return_session and return_session.get("config_dir") == to_tier.config_dir
            and return_session.get("session_id")):
        return ["--resume", return_session["session_id"]] + handoff, None
    remembered = {"session_id": session_id, "config_dir": from_tier.config_dir} if session_id else None
    return handoff, remembered


def run_child(cmd: List[str], env: Mapping[str, str]) -> int:
    proc = subprocess.Popen(cmd, env=dict(env))
    # Only after Popen: an ignored signal is inherited across exec, and claude must still get Ctrl+C.
    previous = {s: signal.signal(s, signal.SIG_IGN) for s in (signal.SIGINT, signal.SIGQUIT)}

    def forward(signum, _frame):
        try:
            proc.send_signal(signum)
        except ProcessLookupError:
            pass

    for s in (signal.SIGTERM, signal.SIGHUP):
        previous[s] = signal.signal(s, forward)
    try:
        code = proc.wait()
    finally:
        for s, handler in previous.items():
            signal.signal(s, handler)
    return code if code >= 0 else 128 - code


def resolve_tier(config: Config, accounts: Mapping, now: float, unusable: Set[str],
                 get_token: Callable[[str], Optional[str]],
                 warn: Callable[[str], None]) -> Tuple[Optional[Tier], Optional[str]]:
    """pick(), skipping token tiers whose token is missing or unreadable (they're added to `unusable`)."""
    while True:
        tier = pick(config.tiers, accounts, now, unusable)
        if tier is None:
            return None, None
        problem = login_mismatch(tier)
        if problem:
            warn("rclaude: skipping %s (%s)" % (tier.name, problem))
            unusable.add(tier.name)
            continue
        if tier.auth != "token":
            return tier, None
        try:
            token = get_token(tier.name)
            problem = "no token stored"
        except tokens.TokenStoreError as e:
            token, problem = None, "token store error: %s" % e
        if token:
            return tier, token
        warn("rclaude: skipping %s (%s). Fix: rclaude add-token %s" % (tier.name, problem, tier.name))
        unusable.add(tier.name)


def run(argv: Sequence[str], config: Config, store: StateStore, entry_path: str, claude_bin: str = "claude",
        get_token: Callable[[str], Optional[str]] = tokens.get_token, now: Callable[[], float] = time.time,
        environ: Optional[Mapping[str, str]] = None, err: Optional[TextIO] = None) -> int:
    environ = os.environ if environ is None else environ
    err = sys.stderr if err is None else err

    def warn(message: str) -> None:
        print(message, file=err)

    unusable: Set[str] = set()
    if is_config_command(argv):
        # `mcp add`, `plugin install`, `auth …` configure a profile: always the first tier's, never the
        # work profile just because the personal accounts happen to be limited.
        tier = config.tiers[0]
        token = None
        if tier.auth == "token":
            try:
                token = get_token(tier.name)
            except tokens.TokenStoreError:
                token = None
        return run_child([claude_bin] + list(argv), build_env(environ, tier, token))
    if is_passthrough(argv):
        tier, token = resolve_tier(config, store.read()["accounts"], now(), unusable, get_token, warn)
        if tier is None:
            warn("rclaude: no usable account.")
            return 1
        return run_child([claude_bin] + list(argv), build_env(environ, tier, token))

    pid = os.getpid()
    key = str(pid)
    store.clear_pending(pid)
    base_args = strip_session_args(argv)
    settings_path = write_generated_settings(store, entry_path)
    current: Optional[Tier] = None
    request: Optional[dict] = None
    return_session: Optional[dict] = None
    while True:
        t = now()
        accounts = store.read()["accounts"]
        tier, token = resolve_tier(config, accounts, t, unusable, get_token, warn)
        if tier is None:
            warn(format_status(config, store.read(), t, login_labels(config)))
            warn("rclaude: no usable account. Fix the problems above and try again.")
            return 1
        if best_available(config.tiers, accounts, t, unusable) is None:
            warn(format_status(config, store.read(), t, login_labels(config)))
            warn("rclaude: every account is limited; starting on %s, which resets soonest." % tier.name)
        if current is None or request is None:
            args = list(argv)
        else:
            session_args, return_session = plan_relaunch(request, current, tier, return_session)
            args = session_args + base_args  # session args and prompt first: no user option can swallow them
            store.log("switching %s -> %s (%s)" % (current.name, tier.name, request.get("reason")))
        with store.locked() as state:
            entry = state["launchers"].setdefault(key, {"started_at": t, "last_switch_at": 0})
            entry.update({"account": tier.name, "cwd": os.getcwd(), "unusable": sorted(unusable),
                          "return_session": return_session, "launched_at": t})
            if current is not None:
                entry["last_switch_at"] = t
        env = build_env(environ, tier, token, launcher_pid=pid, inner_statusline=inner_statusline_for(tier))
        store.log("launch on %s" % tier.name)
        code = run_child([claude_bin, "--settings", settings_path] + args, env)
        request = store.take_switch(pid)
        if request is not None and not store.take_killed(pid):
            store.log("ignoring %s switch: claude exited on its own (code %s)" % (request.get("reason"), code))
            request = None
        if request is None:
            with store.locked() as state:
                state["launchers"].pop(key, None)
            return code
        current = tier
