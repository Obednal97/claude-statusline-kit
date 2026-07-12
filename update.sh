#!/usr/bin/env bash
#
# Updates the kit: pulls the latest code (if this is a git checkout) and
# re-runs the installer. install.sh is idempotent and backs up what it touches.
#
# Note: pricing and model context windows update themselves at runtime from
# live sources (ccusage / LiteLLM) — you only need this for kit code changes.

set -euo pipefail
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$REPO_DIR"

if [ -d .git ] && command -v git >/dev/null 2>&1; then
  echo "==> Pulling latest…"
  git pull --ff-only || {
    echo "[warn] git pull failed (local changes or non-fast-forward). Resolve, then re-run."
    exit 1
  }
else
  echo "[warn] Not a git checkout — re-download the repo to get the latest code."
fi

echo "==> Re-running installer…"
./install.sh
