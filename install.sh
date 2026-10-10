#!/usr/bin/env bash
#
# Installer for the Claude Code statusline kit.
# - Copies the widget scripts + ccstatusline config into ~/.config/ccstatusline/
# - Points Claude Code's status line at `ccstatusline`
# - Backs up anything it overwrites
#
# Safe to re-run. macOS and Linux (bash). Windows: run from Git Bash (or use WSL).

set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CFG_DIR="$HOME/.config/ccstatusline"
CLAUDE_SETTINGS="$HOME/.claude/settings.json"
STAMP="$(date +%Y%m%d-%H%M%S)"

say()  { printf '\033[1;36m==>\033[0m %s\n' "$1"; }
warn() { printf '\033[1;33m[warn]\033[0m %s\n' "$1"; }
die()  { printf '\033[1;31m[error]\033[0m %s\n' "$1" >&2; exit 1; }

# 1. Dependencies -----------------------------------------------------------
command -v node >/dev/null 2>&1 || die "node is required (install Node.js, then re-run)."
command -v npm  >/dev/null 2>&1 || warn "npm not found — skipping automatic dependency install."

install_dep() {
  local bin="$1" pkg="$2"
  if command -v "$bin" >/dev/null 2>&1; then
    say "$bin already installed."
  elif command -v npm >/dev/null 2>&1; then
    say "Installing $pkg (npm install -g $pkg)…"
    npm install -g "$pkg" >/dev/null 2>&1 \
      && say "$pkg installed." \
      || warn "Could not install $pkg automatically. Run: npm install -g $pkg"
  else
    warn "$bin missing and npm unavailable. Install it manually: npm install -g $pkg"
  fi
}
install_dep ccstatusline ccstatusline   # renders the status line
install_dep ccusage      ccusage        # provides cost data for the $ widgets

# 2. Widget scripts + ccstatusline config -----------------------------------
mkdir -p "$CFG_DIR"

if [ -f "$CFG_DIR/settings.json" ]; then
  cp "$CFG_DIR/settings.json" "$CFG_DIR/settings.json.bak-$STAMP"
  say "Backed up existing ccstatusline config → settings.json.bak-$STAMP"
fi

cp "$REPO_DIR/statusline/"*.sh "$CFG_DIR/"
chmod +x "$CFG_DIR/"*.sh
cp "$REPO_DIR/settings.json" "$CFG_DIR/settings.json"
say "Installed 6 widget scripts + config into $CFG_DIR"

# Windows (Git Bash): ccstatusline runs custom commands through cmd.exe, which
# can't run a .sh or expand ~, so point each widget at Git Bash explicitly.
case "$(uname -s)" in
  MINGW*|MSYS*|CYGWIN*)
    command -v cygpath >/dev/null 2>&1 || die "cygpath not found — run install.sh from Git Bash."
    # Prefer Git's bin/bash.exe wrapper: it puts /usr/bin (stat, date, cksum) on PATH.
    BASH_EXE="$(cygpath -m /)bin/bash.exe"
    [ -f "$BASH_EXE" ] || BASH_EXE="$(cygpath -m "$(command -v bash)")"
    BASH_EXE="$BASH_EXE" CFG_WIN="$(cygpath -m "$CFG_DIR")" CFG_JSON="$CFG_DIR/settings.json" node -e '
    const fs = require("fs");
    const p = process.env.CFG_JSON;
    const cfg = JSON.parse(fs.readFileSync(p, "utf8"));
    for (const line of cfg.lines || []) for (const w of line) {
      const m = typeof w.commandPath === "string" && w.commandPath.match(/^~\/\.config\/ccstatusline\/(.+\.sh)$/);
      if (m) w.commandPath = "\"" + process.env.BASH_EXE + "\" \"" + process.env.CFG_WIN + "/" + m[1] + "\"";
    }
    fs.writeFileSync(p, JSON.stringify(cfg, null, 2) + "\n");
    '
    say "Windows: widgets run via $BASH_EXE"
    ;;
esac

# 3. Point Claude Code at ccstatusline --------------------------------------
if [ -f "$CLAUDE_SETTINGS" ]; then
  cp "$CLAUDE_SETTINGS" "$CLAUDE_SETTINGS.bak-$STAMP"
  say "Backed up Claude settings → $(basename "$CLAUDE_SETTINGS").bak-$STAMP"
fi

CLAUDE_SETTINGS="$CLAUDE_SETTINGS" node -e '
const fs = require("fs"), path = require("path");
const p = process.env.CLAUDE_SETTINGS;
let cfg = {};
try { cfg = JSON.parse(fs.readFileSync(p, "utf8")); } catch (e) {}
cfg.statusLine = { type: "command", command: "ccstatusline" };
fs.mkdirSync(path.dirname(p), { recursive: true });
fs.writeFileSync(p, JSON.stringify(cfg, null, 2) + "\n");
'
say "Set statusLine.command = \"ccstatusline\" in Claude Code settings."

# 4. Sanity check -----------------------------------------------------------
if ! command -v ccstatusline >/dev/null 2>&1; then
  warn "ccstatusline is not on your PATH. Ensure your npm global bin is on PATH,"
  warn "or set the status line command to: npx -y ccstatusline@latest"
fi

echo
say "Done. Open a new Claude Code session (or wait for the next render)."
echo "   • Tag your accounts: set WORK_DOMAIN in $CFG_DIR/account.sh"
echo "   • Tweak widgets/colours interactively: run  ccstatusline"
echo "   • Uninstall: ./uninstall.sh"
