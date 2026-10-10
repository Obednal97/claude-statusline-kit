#!/usr/bin/env bash
#
# Installer for the Claude Code statusline kit.
# - Copies the widget scripts + ccstatusline config into ~/.config/ccstatusline/
# - Points Claude Code's status line at `ccstatusline`
# - Backs up anything it overwrites
#
# Safe to re-run. macOS and Linux (bash). Windows: run from Git Bash, MSYS2 or
# Cygwin (or run everything inside WSL).

set -euo pipefail

say()  { printf '\033[1;36m==>\033[0m %s\n' "$1"; }
warn() { printf '\033[1;33m[warn]\033[0m %s\n' "$1"; }
die()  { printf '\033[1;31m[error]\033[0m %s\n' "$1" >&2; exit 1; }

# Windows: a bash from Git for Windows, MSYS2 or Cygwin. Node, ccstatusline and
# Claude Code are native Windows programs there: their home is %USERPROFILE%
# (they ignore $HOME) and they need C:/ paths, not /c/ or /cygdrive/c/ ones.
# WSL reports linux-gnu here, which is right: Node and Claude Code are Linux there.
IS_WINDOWS=0
case "${OSTYPE:-}" in msys*|cygwin*) IS_WINDOWS=1 ;; esac
case "$(uname -s 2>/dev/null)" in MINGW*|MSYS*|CYGWIN*|*_NT-*) IS_WINDOWS=1 ;; esac

HOME_DIR="$HOME"
if [ "$IS_WINDOWS" = 1 ]; then
  command -v cygpath >/dev/null 2>&1 || die "cygpath not found — run install.sh from Git Bash, MSYS2 or Cygwin."
  [ -n "${USERPROFILE:-}" ] || die "USERPROFILE is not set — cannot find your Windows home folder."
  HOME_DIR="$(cygpath -u "$USERPROFILE")"
elif grep -qi microsoft /proc/sys/kernel/osrelease 2>/dev/null; then
  say "WSL detected: setting up Claude Code inside WSL. For Claude Code on Windows itself, run install.sh from Git Bash instead."
fi

# Paths handed to node must be native: C:/... on Windows, unchanged elsewhere.
native_path() { if [ "$IS_WINDOWS" = 1 ]; then cygpath -m "$1"; else printf '%s' "$1"; fi; }

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CFG_DIR="$HOME_DIR/.config/ccstatusline"
CLAUDE_SETTINGS="$HOME_DIR/.claude/settings.json"
STAMP="$(date +%Y%m%d-%H%M%S)"

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
say "Installed $(ls "$REPO_DIR/statusline/"*.sh | wc -l | tr -d ' ') widget scripts + config into $CFG_DIR"

# Windows: ccstatusline runs custom commands through cmd.exe, which can't run
# a .sh or expand ~, so launch each widget with bash.exe and an absolute path.
if [ "$IS_WINDOWS" = 1 ]; then
  # Prefer Git for Windows' bin/bash.exe wrapper; otherwise (MSYS2, Cygwin) the
  # bash running this script. Widgets put /usr/bin on PATH themselves.
  BASH_EXE="$(cygpath -m /)bin/bash.exe"
  [ -f "$BASH_EXE" ] || BASH_EXE="$(cygpath -m "$BASH")"
  case "$BASH_EXE" in *.exe) ;; *) BASH_EXE="$BASH_EXE.exe" ;; esac
  BASH_EXE="$BASH_EXE" CFG_WIN="$(native_path "$CFG_DIR")" CFG_JSON="$(native_path "$CFG_DIR/settings.json")" node -e '
  const fs = require("fs");
  const p = process.env.CFG_JSON;
  const cfg = JSON.parse(fs.readFileSync(p, "utf8"));
  let n = 0;
  for (const line of cfg.lines || []) for (const w of line) {
    const m = typeof w.commandPath === "string" && w.commandPath.match(/^~\/\.config\/ccstatusline\/([\w.-]+\.sh)$/);
    if (m) { w.commandPath = "\"" + process.env.BASH_EXE + "\" \"" + process.env.CFG_WIN + "/" + m[1] + "\""; n++; }
  }
  fs.writeFileSync(p, JSON.stringify(cfg, null, 2) + "\n");
  if (!n) process.exit(3);
  ' || die "Could not point the widgets at bash.exe in $CFG_DIR/settings.json."
  say "Windows: widgets run via $BASH_EXE"
fi

# 3. Point Claude Code at ccstatusline --------------------------------------
if [ -f "$CLAUDE_SETTINGS" ]; then
  cp "$CLAUDE_SETTINGS" "$CLAUDE_SETTINGS.bak-$STAMP"
  say "Backed up Claude settings → $(basename "$CLAUDE_SETTINGS").bak-$STAMP"
fi

# An unreadable settings file stops the install instead of being replaced, so
# a typo (or a BOM from a Windows editor, which is stripped) never wipes it.
CLAUDE_SETTINGS="$(native_path "$CLAUDE_SETTINGS")" node -e '
const fs = require("fs"), path = require("path");
const p = process.env.CLAUDE_SETTINGS;
let cfg = {};
if (fs.existsSync(p)) {
  const text = fs.readFileSync(p, "utf8").replace(/^\uFEFF/, "");
  if (text.trim()) {
    try { cfg = JSON.parse(text); } catch (e) { process.exit(3); }
  }
}
cfg.statusLine = { type: "command", command: "ccstatusline" };
fs.mkdirSync(path.dirname(p), { recursive: true });
fs.writeFileSync(p, JSON.stringify(cfg, null, 2) + "\n");
' || die "$CLAUDE_SETTINGS is not valid JSON — fix it (or move it aside) and re-run. It was not changed."
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
