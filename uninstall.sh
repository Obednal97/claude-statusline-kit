#!/usr/bin/env bash
#
# Removes the statusline kit's scripts + config and unsets the Claude Code
# status line. Leaves ccstatusline / ccusage installed (npm uninstall them
# yourself if you want them gone). Your timestamped backups are left in place.

set -euo pipefail

CFG_DIR="$HOME/.config/ccstatusline"
CLAUDE_SETTINGS="$HOME/.claude/settings.json"

say() { printf '\033[1;36m==>\033[0m %s\n' "$1"; }

for f in daily-cost.sh weekly-cost.sh monthly-cost.sh context-percentage.sh account.sh repo-name.sh model-tags.sh plan-usage.sh settings.json; do
  rm -f "$CFG_DIR/$f"
done
say "Removed kit scripts + config from $CFG_DIR"

if [ -f "$CLAUDE_SETTINGS" ]; then
  CLAUDE_SETTINGS="$CLAUDE_SETTINGS" node -e '
  const fs = require("fs");
  const p = process.env.CLAUDE_SETTINGS;
  let cfg = {};
  try { cfg = JSON.parse(fs.readFileSync(p, "utf8")); } catch (e) { process.exit(0); }
  if (cfg.statusLine && cfg.statusLine.command === "ccstatusline") {
    delete cfg.statusLine;
    fs.writeFileSync(p, JSON.stringify(cfg, null, 2) + "\n");
  }
  '
  say "Unset statusLine in Claude Code settings (restore a backup if you had a custom one)."
fi

say "Done."
