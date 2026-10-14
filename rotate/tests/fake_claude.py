#!/usr/bin/env python3
"""Stand-in for `claude` in the integration tests.

FAKE_CLAUDE_SCRIPT is a JSON list with one entry per invocation; each entry is a list of steps:
  {"statusline": {...}}                             run the --settings statusLine command with this JSON
  {"hook": "Stop" | "StopFailure", "payload": {...}} run that hook's commands with this JSON on stdin
  {"clear_limits": ["name", ...]}                    set limited_until = 0 (simulates a reset)
  {"append": {"path": ..., "rows": [...]}}          append JSON rows to a file (e.g. the transcript)
  {"sleep": seconds}                                 pause
  {"snapshot_launchers": true}                      record rclaude's launcher entries as seen now
  {"touch": "/path"}                                 create a file (lets a test know we're running)
  {"wait_for_term": true}                            block until SIGTERM (exit 143), or exit 99 after 10 s
  {"exit": N}                                        exit with N
Each invocation appends one JSON line to FAKE_CLAUDE_LOG: argv, the env vars rclaude manages, outputs, exit code.
"""
import json
import os
import signal
import subprocess
import sys
import time

ENV_KEYS = ("CLAUDE_CONFIG_DIR", "CLAUDE_CODE_OAUTH_TOKEN", "RCLAUDE_ACCOUNT", "RCLAUDE_PID",
            "RCLAUDE_INNER_STATUSLINE", "RCLAUDE_AUTH", "RCLAUDE_ACCOUNT_EMAIL")


def main():
    log_path = os.environ["FAKE_CLAUDE_LOG"]
    with open(os.environ["FAKE_CLAUDE_SCRIPT"]) as f:
        script = json.load(f)
    try:
        with open(log_path) as f:
            index = sum(1 for _ in f)
    except FileNotFoundError:
        index = 0
    argv = sys.argv[1:]
    record = {"argv": argv, "env": {k: os.environ.get(k) for k in ENV_KEYS}, "outputs": []}
    settings = {}
    if "--settings" in argv:
        with open(argv[argv.index("--settings") + 1]) as f:
            settings = json.load(f)
    got_term = []
    signal.signal(signal.SIGTERM, lambda signum, frame: got_term.append(signum))
    code = 0
    for step in (script[index] if index < len(script) else [{"exit": 0}]):
        if "statusline" in step:
            p = subprocess.run(settings["statusLine"]["command"], shell=True,
                               input=json.dumps(step["statusline"]), capture_output=True, text=True)
            record["outputs"].append(p.stdout)
        elif "hook" in step:
            for group in settings.get("hooks", {}).get(step["hook"], []):
                for h in group["hooks"]:
                    subprocess.run(h["command"], shell=True, input=json.dumps(step["payload"]), text=True)
        elif "clear_limits" in step:
            sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.realpath(__file__))))
            from claude_rotate.state import StateStore
            with StateStore().locked() as state:
                for name in step["clear_limits"]:
                    state["accounts"].setdefault(name, {})["limited_until"] = 0
        elif "append" in step:
            with open(step["append"]["path"], "a") as f:
                for row in step["append"]["rows"]:
                    f.write(json.dumps(row) + "\n")
        elif "sleep" in step:
            time.sleep(step["sleep"])
        elif "snapshot_launchers" in step:
            sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.realpath(__file__))))
            from claude_rotate.state import StateStore
            record["launchers"] = StateStore().read()["launchers"]
        elif "touch" in step:
            open(step["touch"], "w").close()
        elif "wait_for_term" in step:
            deadline = time.time() + 10
            while not got_term and time.time() < deadline:
                time.sleep(0.05)
            code = 143 if got_term else 99
            break
        elif "exit" in step:
            code = step["exit"]
            break
    record["code"] = code
    with open(log_path, "a") as f:
        f.write(json.dumps(record) + "\n")
    sys.exit(code)


if __name__ == "__main__":
    main()
