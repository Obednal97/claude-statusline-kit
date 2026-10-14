#!/usr/bin/env bash
#
# Installs rclaude (account rotation for Claude Code) from this checkout:
# - links ~/.local/bin/rclaude to rotate/rclaude here
# - writes a starter ~/.config/claude-rotate/accounts.json if you don't have one
#
# Safe to re-run. Never edits shell profiles, Claude Code settings or tokens.
# macOS and Linux (WSL counts as Linux). Guide: docs/accounts-and-rotation.md

set -euo pipefail

say()  { printf '\033[1;36m==>\033[0m %s\n' "$1"; }
warn() { printf '\033[1;33m[warn]\033[0m %s\n' "$1"; }
die()  { printf '\033[1;31m[error]\033[0m %s\n' "$1" >&2; exit 1; }

WINDOWS_MSG="Windows isn't supported. Use WSL: clone the kit inside your WSL distro and run this there."
case "${OSTYPE:-}" in msys*|cygwin*) die "$WINDOWS_MSG" ;; esac
case "$(uname -s 2>/dev/null)" in
  MINGW*|MSYS*|CYGWIN*|*_NT-*) die "$WINDOWS_MSG" ;;
  Darwin|Linux) ;;
  *) die "Unsupported system ($(uname -s)). rclaude runs on macOS and Linux." ;;
esac

ROTATE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
ENTRY="$ROTATE_DIR/rclaude"
BIN_DIR="$HOME/.local/bin"
LINK="$BIN_DIR/rclaude"
CONFIG_DIR="$HOME/.config/claude-rotate"
CONFIG="$CONFIG_DIR/accounts.json"
STAMP="$(date +%Y%m%d-%H%M%S)"

# 1. Python 3.9+ (rclaude uses only the standard library) and Claude Code ----------
command -v python3 >/dev/null 2>&1 \
  || die "python3 not found. Install Python 3.9 or newer (macOS: xcode-select --install; Linux: your package manager)."
python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)' \
  || die "rclaude needs Python 3.9 or newer; $(command -v python3) is $(python3 -V 2>&1 || echo 'unknown')."
say "Using $(python3 -V 2>&1) ($(command -v python3))"
command -v claude >/dev/null 2>&1 || warn "claude is not on your PATH yet. Install Claude Code before using rclaude."

# 2. ~/.local/bin/rclaude -> this checkout ------------------------------------------
chmod +x "$ENTRY"
mkdir -p "$BIN_DIR"
if [ -L "$LINK" ]; then
  OLD_TARGET="$(readlink "$LINK")"
  [ "$OLD_TARGET" = "$ENTRY" ] || say "Replacing the old rclaude link (it pointed at $OLD_TARGET)."
  rm -f "$LINK"
elif [ -e "$LINK" ]; then
  mv "$LINK" "$LINK.bak-$STAMP"
  say "Backed up the existing $LINK as rclaude.bak-$STAMP"
fi
ln -s "$ENTRY" "$LINK"
say "Linked $LINK -> $ENTRY"
case ":$PATH:" in
  *":$BIN_DIR:"*) ;;
  *) warn "$BIN_DIR is not on your PATH. Add this line to your shell profile (~/.zshrc or ~/.bashrc):"
     printf '    export PATH="$HOME/.local/bin:$PATH"\n' ;;
esac

# 3. Starter accounts.json (never overwritten) ---------------------------------------
mkdir -p "$CONFIG_DIR"
if [ -e "$CONFIG" ]; then
  say "Keeping your existing $CONFIG"
else
  cp "$ROTATE_DIR/accounts.example.json" "$CONFIG"
  say "Wrote a starter $CONFIG. Edit the names, emails and order to match your accounts."
fi

echo
say "Next steps (full guide: docs/accounts-and-rotation.md):"
echo "   1. Log the personal profile in:  CLAUDE_CONFIG_DIR=~/.claude-personal claude   then /login, then /exit"
echo "   2. For each extra account: in a normal terminal run  claude setup-token  (sign in as that account),"
echo "      then  rclaude add-token <name>  and paste the token (input is hidden)"
echo "   3. Add an alias to your shell profile:  alias claude-personal='rclaude'"
echo "   4. Check everything:  rclaude status"
