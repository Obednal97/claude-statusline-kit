# Claude Code Statusline Kit

A [ccstatusline](https://github.com/sirmalloc/ccstatusline) preset (`settings.json`) plus custom-command widget scripts (`statusline/*.sh`). `install.sh` copies every `statusline/*.sh` and `settings.json` into `~/.config/ccstatusline/`; add new scripts to the file list in `uninstall.sh`.

## Writing widget scripts

- **Prefer Claude Code's stdin JSON over deriving data yourself.** It now carries `context_window` (real window size + current usage), `rate_limits`, `effort`, `fast_mode`, `pr`, `workspace.repo`, `worktree`, `session_name`, etc. Schema: https://code.claude.com/docs/en/statusline. Fall back to transcripts/caches only for older Claude Code versions.
- **No apostrophes inside `node -e '...'` blocks**, including in comments — a `'` ends the shell string and the widget silently prints nothing. Run `grep -n "'" statusline/<file>.sh` after editing; only the opening and closing quotes should match.
- Scripts must never block a render: no network calls in the render path (background-refresh into a cache instead), and print nothing to hide the widget.
- Account-specific files follow `CLAUDE_CONFIG_DIR` (`$CLAUDE_CONFIG_DIR/.claude.json`, else `~/.claude.json`). Read only identity/plan fields from `oauthAccount`; never touch credentials.

## ccstatusline quirks

- Custom-command output is trimmed, and colour/OSC 8 link escapes are stripped unless the widget has `"preserveColors": true` (then the script sets its own colours).
- `"merge": true` goes on the widget that joins the *next* one. If the next widget prints nothing, the merge swallows the separator after it, so a merged script should print a bare `\x1b[0m` rather than nothing.
- On Windows, custom commands run through `cmd.exe`, so `install.sh` rewrites each `~/.config/ccstatusline/*.sh` `commandPath` to `"<Git>/bin/bash.exe" "<abs path>"`. Keep new widget `commandPath`s in the `~/.config/ccstatusline/<name>.sh` form so that rewrite matches them, and keep scripts to tools Git Bash ships (it has `stat -c`, `cksum`, `date -d`, `/tmp`).
- `hide` metadata is a comma-separated string, e.g. `"hide": "no-git,no-data"`.
- Git PR/branch-link widgets are built in (`git-pr`, `git-branch` with `"linkToRepo": "true"`); the PR widget fetches via `gh`/`glab` in the background, so it appears one render later.

## Testing

Render against a sandbox instead of the live config: copy `statusline/*.sh` and `settings.json` into `$TMP/home/.config/ccstatusline/`, then `HOME=$TMP/home CLAUDE_CONFIG_DIR=<dir with a test .claude.json> ccstatusline < sample.json`. Under a fake `HOME`, `gh` loses its login — pass `GH_TOKEN=$(gh auth token)` for PR widgets.
