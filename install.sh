#!/usr/bin/env bash
#
# Installer for the Claude Code statusline kit.
# - Copies the widget scripts + ccstatusline config into ~/.config/ccstatusline/
# - Points Claude Code's status line at `ccstatusline`
# - Backs up anything it overwrites
# - Lets you choose which rows to show (asks in a terminal; --rows 1,2,5 to skip
#   the question). The choice is saved and offered again on the next run.
#
# Safe to re-run. macOS and Linux (bash). Windows: run from Git Bash, MSYS2 or
# Cygwin (or run everything inside WSL).

set -euo pipefail

say()  { printf '\033[1;36m==>\033[0m %s\n' "$1"; }
warn() { printf '\033[1;33m[warn]\033[0m %s\n' "$1"; }
die()  { printf '\033[1;31m[error]\033[0m %s\n' "$1" >&2; exit 1; }

# Rows of the preset, in settings.json order.
ROW_NAMES=(Money Context Git Account Device)
ROW_INFO=(
  "session, day, week and month cost; plan usage"
  "model, context window used, session time"
  "repo, branch, pull request, uncommitted changes"
  "the Claude account you are logged in as"
  "CPU, memory and disk bars"
)
ALL_ROWS="1,2,3,4,5"

usage() {
  cat <<EOF
Usage: ./install.sh [--rows LIST]

  --rows LIST   rows to show, e.g. --rows 1,2,3,5 (no question asked)
EOF
  local i
  for i in 0 1 2 3 4; do printf '                %d %-8s %s\n' $((i + 1)) "${ROW_NAMES[$i]}" "${ROW_INFO[$i]}"; done
  cat <<EOF

Without --rows it asks in a terminal, starting from your last choice. With no
terminal to ask in, it keeps your last choice, or shows every row.
EOF
}

ROWS_ARG=""
while [ $# -gt 0 ]; do
  case "$1" in
    --rows)   [ $# -ge 2 ] || die "--rows needs a list, e.g. --rows 1,2,3,5"; ROWS_ARG="$2"; shift 2 ;;
    --rows=*) ROWS_ARG="${1#--rows=}"; shift ;;
    -h|--help) usage; exit 0 ;;
    *) die "Unknown option: $1 (see ./install.sh --help)" ;;
  esac
done

# "5, 1 2" -> "1,2,5". Fails on anything that isn't a row number, or on no rows.
normalise_rows() {
  local list out="" n
  case "$1" in *[!0-9,\ ]*) return 1 ;; esac
  list="$(printf '%s' "$1" | tr ', ' '\n\n')"
  for n in $list; do
    case "$n" in [1-5]) ;; *) return 1 ;; esac
  done
  for n in 1 2 3 4 5; do
    case ",$(printf '%s' "$list" | tr '\n' ','),," in *",$n,"*) out="${out:+$out,}$n" ;; esac
  done
  [ -n "$out" ] || return 1
  printf '%s' "$out"
}

if [ -n "$ROWS_ARG" ]; then
  ROWS_ARG="$(normalise_rows "$ROWS_ARG")" || die "--rows takes row numbers from 1 to 5, e.g. --rows 1,2,3,5 (see --help)."
fi

# Ask which rows to show; prints the chosen list. Numbers switch rows on or off.
ask_rows() {
  local rows="$1" reply i mark toggled
  while :; do
    printf '\nWhich rows should the status line show?\n' >&2
    for i in 1 2 3 4 5; do
      case ",$rows," in *",$i,"*) mark="x" ;; *) mark=" " ;; esac
      printf '  [%s] %d %-8s %s\n' "$mark" "$i" "${ROW_NAMES[$((i - 1))]}" "${ROW_INFO[$((i - 1))]}" >&2
    done
    printf 'Press Enter to keep these, or type row numbers to switch them on or off: ' >&2
    IFS= read -r reply || reply=""
    [ -z "$(printf '%s' "$reply" | tr -d ', ')" ] && { printf '%s' "$rows"; return; }
    toggled="$(normalise_rows "$reply")" || { warn "Type numbers from 1 to 5, e.g. 4 or 2,5." >&2; continue; }
    local next=""
    for i in 1 2 3 4 5; do
      local on=0 flip=0
      case ",$rows," in *",$i,"*) on=1 ;; esac
      case ",$toggled," in *",$i,"*) flip=1 ;; esac
      [ $((on ^ flip)) = 1 ] && next="${next:+$next,}$i"
    done
    if [ -z "$next" ]; then warn "Keep at least one row." >&2; continue; fi
    rows="$next"
  done
}

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
# Which rows: --rows, else ask in a terminal, else the last choice, else all.
ROWS_FILE="$CFG_DIR/kit-rows"
SAVED_ROWS=""
[ -f "$ROWS_FILE" ] && SAVED_ROWS="$(normalise_rows "$(head -n 1 "$ROWS_FILE" | tr -d '\r')")" || SAVED_ROWS=""
if [ -n "$ROWS_ARG" ]; then
  ROWS="$ROWS_ARG"
elif [ -t 0 ] && [ -t 1 ]; then
  ROWS="$(ask_rows "${SAVED_ROWS:-$ALL_ROWS}")"
else
  ROWS="${SAVED_ROWS:-$ALL_ROWS}"
fi

mkdir -p "$CFG_DIR"

if [ -f "$CFG_DIR/settings.json" ]; then
  cp "$CFG_DIR/settings.json" "$CFG_DIR/settings.json.bak-$STAMP"
  say "Backed up existing ccstatusline config → settings.json.bak-$STAMP"
fi

cp "$REPO_DIR/statusline/"*.sh "$CFG_DIR/"
chmod +x "$CFG_DIR/"*.sh
ROWS="$ROWS" SRC="$(native_path "$REPO_DIR/settings.json")" DEST="$(native_path "$CFG_DIR/settings.json")" node -e '
const fs = require("fs");
const cfg = JSON.parse(fs.readFileSync(process.env.SRC, "utf8"));
if (cfg.lines.length !== 5) process.exit(3);
const keep = process.env.ROWS.split(",").map(Number);
cfg.lines = cfg.lines.filter((line, i) => keep.includes(i + 1));
fs.writeFileSync(process.env.DEST, JSON.stringify(cfg, null, 2) + "\n");
' || die "Could not write $CFG_DIR/settings.json (the preset in this checkout should have 5 rows)."
printf '%s\n' "$ROWS" > "$ROWS_FILE"
ROW_LIST=""
for i in $(printf '%s' "$ROWS" | tr ',' ' '); do ROW_LIST="${ROW_LIST:+$ROW_LIST, }${ROW_NAMES[$((i - 1))]}"; done
say "Installed $(ls "$REPO_DIR/statusline/"*.sh | wc -l | tr -d ' ') widget scripts + config into $CFG_DIR"
say "Rows: $ROW_LIST (change them: re-run ./install.sh, or ./install.sh --rows 1,2,3,5)"

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
