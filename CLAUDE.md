# Claude Code Statusline Kit

A [ccstatusline](https://github.com/sirmalloc/ccstatusline) preset (`settings.json`) plus custom-command widget scripts (`statusline/*.sh`). `install.sh` copies every `statusline/*.sh` and `settings.json` into `~/.config/ccstatusline/`; add new scripts to the file list in `uninstall.sh`. `rotate/` holds `rclaude`, an optional account-rotation launcher (Python), with its own installer; see the Rotation section.

## Writing widget scripts

- **Prefer Claude Code's stdin JSON over deriving data yourself.** It now carries `context_window` (real window size + current usage), `rate_limits`, `effort`, `fast_mode`, `pr`, `workspace.repo`, `worktree`, `session_name`, etc. Schema: https://code.claude.com/docs/en/statusline. Fall back to transcripts/caches only for older Claude Code versions.
- **No apostrophes inside `node -e '...'` blocks**, including in comments — a `'` ends the shell string and the widget silently prints nothing. Run `grep -n "'" statusline/<file>.sh` after editing; only the opening and closing quotes should match.
- Scripts must never block a render: no network calls in the render path (background-refresh into a cache instead), and print nothing to hide the widget.
- Account-specific files follow `CLAUDE_CONFIG_DIR` (`$CLAUDE_CONFIG_DIR/.claude.json`, else `~/.claude.json`). Read only identity/plan fields from `oauthAccount`; never touch credentials.

## ccstatusline quirks

- Custom-command output is trimmed, and colour/OSC 8 link escapes are stripped unless the widget has `"preserveColors": true` (then the script sets its own colours).
- `"merge": true` goes on the widget that joins the *next* one. If the next widget prints nothing, the merge swallows the separator after it, so a merged script should print a bare `\x1b[0m` rather than nothing.
- On Windows, custom commands run through `cmd.exe`, so `install.sh` rewrites each `~/.config/ccstatusline/*.sh` `commandPath` to `"<bash.exe>" "<abs path>"`. Keep new widget `commandPath`s in the `~/.config/ccstatusline/<name>.sh` form so that rewrite matches them.

## Windows (Git Bash, MSYS2, Cygwin)

- Detect it with `$OSTYPE` (`msys*`/`cygwin*`), backed by `uname -s` (`MINGW*|MSYS*|CYGWIN*|*_NT-*`, which covers MSYS2's UCRT64/CLANG64). WSL reports `linux-gnu` and is treated as Linux, which is correct: Node and Claude Code are Linux there.
- Node, ccstatusline and Claude Code are native Windows programs: their home is `%USERPROFILE%` (they ignore `$HOME`), and they need `C:/` paths. Pass every path you hand to `node` through `cygpath -m` (`native_path` in the installers). Do not rely on MSYS auto-conversion, which `MSYS_NO_PATHCONV=1` turns off and Cygwin never does.
- Every widget starts with the `case "${OSTYPE:-}" in msys*|cygwin*) PATH="/usr/bin:$PATH"` preamble: a bash.exe started by cmd.exe (anything but Git's `bin/bash.exe` wrapper) has no coreutils on PATH. Copy it into new widgets. Git Bash ships `stat -c`, `cksum`, `date -d` and `/tmp`.
- Windows editors save JSON with a BOM. Strip `^\uFEFF` before `JSON.parse` on any file a user may have edited.
- ccstatusline renders spaces as U+00A0, so normalise them before grepping its output in tests.
- `hide` metadata is a comma-separated string, e.g. `"hide": "no-git,no-data"`.
- Git PR/branch-link widgets are built in (`git-pr`, `git-branch` with `"linkToRepo": "true"`); the PR widget fetches via `gh`/`glab` in the background, so it appears one render later.

## Testing

Render against a sandbox instead of the live config: copy `statusline/*.sh` and `settings.json` into `$TMP/home/.config/ccstatusline/`, then `HOME=$TMP/home CLAUDE_CONFIG_DIR=<dir with a test .claude.json> ccstatusline < sample.json`. Under a fake `HOME`, `gh` loses its login — pass `GH_TOKEN=$(gh auth token)` for PR widgets.

## Rotation (`rotate/`)

`rclaude` wraps `claude` and switches accounts on plan limits. User docs: `docs/accounts-and-rotation.md`.

- Python stdlib only, 3.9+; every module starts with `from __future__ import annotations`.
- Tests, from the repo root: `/usr/bin/python3 -m unittest discover -s rotate/tests -t rotate` (macOS) or `python3 …` (Linux). `rotate/tests/fake_claude.py` stands in for `claude` in the end-to-end tests. CI: `.github/workflows/rotate-tests.yml`.
- Tokens never go on argv, in logs or in output. The Keychain is written through `security -i` on stdin; the Linux file store (`tokens.py`) is 0600 and replaced in one step.
- Don't change the formats of `state.json`, switch files or the generated settings without a migration: sessions started from an older checkout share them.
- Widgets learn about rclaude only through env: `RCLAUDE_ACCOUNT`, `RCLAUDE_AUTH` (`login`/`token`), `RCLAUDE_ACCOUNT_EMAIL`.
- `rclaude` can be PID 1 in a container, so process-tree code must not treat PID 1 as "no launcher".
- What the hooks rely on (checked against Claude Code 2.1.299):
  - `StopFailure` fires for every submitted prompt on a limited account, with `error: "rate_limit"` and the limit text in `last_assistant_message`.
  - Resuming, with no prompt, a conversation whose last event is the same account's limit opens Claude Code's limit menu and fires only `SessionStart` (`source: "resume"`); `hooks.handle_session_start` covers it.
  - Login sessions write a `credential_org` attachment after a prompt only when the org changes; token sessions write none.
  - An in-session `/login` appears in the transcript as a user entry starting `<local-command-stdout>Login successful`.
  - The status-line JSON has `rate_limits` (after the first API response) but no account identity.
- `launcher.py`'s option tables mirror `claude --help`; re-check them when Claude Code adds flags or subcommands.
