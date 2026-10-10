#!/usr/bin/env bash
# Repo name for the git row, e.g. "claude-statusline-kit".
#
# Uses the origin remote's repo name when there is one (stable across clones,
# worktrees and renamed folders), else the repo's top-level folder name.
# Prints nothing outside a git repo, so the widget hides itself.
#
# Input: Claude Code's status JSON on stdin (workspace.current_dir / cwd).

# Windows (Git Bash, MSYS2, Cygwin): bash.exe launched by cmd.exe may not have
# /usr/bin (stat, date, cksum) on PATH.
case "${OSTYPE:-}" in msys*|cygwin*) PATH="/usr/bin:$PATH" ;; esac

NODE="$(command -v node)"
DIR=""
if [ -n "$NODE" ]; then
  DIR=$("$NODE" -e 'const fs=require("fs");let o={};try{o=JSON.parse(fs.readFileSync(0,"utf8"))}catch(e){}process.stdout.write((o.workspace&&o.workspace.current_dir)||o.cwd||"")' 2>/dev/null)
fi
[ -d "$DIR" ] || DIR="$PWD"

git -C "$DIR" rev-parse --is-inside-work-tree >/dev/null 2>&1 || exit 0

URL=$(git -C "$DIR" remote get-url origin 2>/dev/null)
if [ -n "$URL" ]; then
  NAME=$(basename "${URL%/}" .git)
else
  NAME=$(basename "$(git -C "$DIR" rev-parse --show-toplevel 2>/dev/null)")
fi
printf '%s' "$NAME"
