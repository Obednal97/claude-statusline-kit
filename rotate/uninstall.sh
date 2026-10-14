#!/usr/bin/env bash
#
# Removes the ~/.local/bin/rclaude link that rotate/install.sh created (only if it
# points at this checkout). Keeps accounts.json, state, logs and tokens, and prints
# the commands to remove them.

set -euo pipefail

say() { printf '\033[1;36m==>\033[0m %s\n' "$1"; }

ROTATE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
LINK="$HOME/.local/bin/rclaude"
CONFIG="$HOME/.config/claude-rotate/accounts.json"

if [ -L "$LINK" ] && [ "$(readlink "$LINK")" = "$ROTATE_DIR/rclaude" ]; then
  rm -f "$LINK"
  say "Removed $LINK"
elif [ -L "$LINK" ] || [ -e "$LINK" ]; then
  say "Left $LINK alone: it doesn't point at this checkout ($(readlink "$LINK" 2>/dev/null || echo 'not a link'))."
else
  say "No rclaude link to remove."
fi

echo
say "Kept your accounts, state and tokens. To remove them as well:"
echo "   rm -r ~/.config/claude-rotate ~/.local/state/claude-rotate"
if [ "$(uname -s)" = "Darwin" ] && [ -f "$CONFIG" ]; then
  NAMES="$(python3 -c '
import json, sys
try:
    tiers = json.load(open(sys.argv[1])).get("tiers", [])
except Exception:
    tiers = []
print(" ".join(t["name"] for t in tiers if isinstance(t, dict) and t.get("auth") == "token" and isinstance(t.get("name"), str)))
' "$CONFIG" 2>/dev/null || true)"
  for n in $NAMES; do
    echo "   security delete-generic-password -s claude-rotate:$n -a \"\$USER\""
  done
else
  echo "   (on Linux the tokens are in ~/.config/claude-rotate/tokens.json, which that rm removes)"
fi
echo "   Also remove any rclaude aliases from your shell profile."
