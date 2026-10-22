#!/usr/bin/env bash
# Shows the currently-active Claude Code account, tagged Work vs Personal by
# email domain. The account updates whenever you /login, so this reflects the
# login in use right now.
#
# Reads ONLY identity metadata (oauthAccount.emailAddress) from the active
# config: $CLAUDE_CONFIG_DIR/.claude.json if set, else ~/.claude.json.
# It never reads or displays the credentials/token file.
#
# Output: "👤 you@example.com"
#   Set WORK_DOMAIN below to tag accounts, e.g. WORK_DOMAIN="acme.com" gives:
#     "👤 Work · you@acme.com"   (email at that domain)
#     "👤 Personal · you@personal.com"   (any other domain)
#   Leave it empty to just show the email with no tag.
#
# Under rclaude (docs/accounts-and-rotation.md), a session running on a
# `claude setup-token` token doesn't use the profile's saved login, so the
# widget shows the account rclaude says is running (RCLAUDE_ACCOUNT_EMAIL,
# else RCLAUDE_ACCOUNT) instead.
#
# In local mode (local-mode.sh, e.g. claude-local on Ollama) nothing goes to
# Anthropic, so it shows "🖥 Local model (Ollama) · not using your Claude
# account" and never an email.

# Windows (Git Bash, MSYS2, Cygwin): bash.exe launched by cmd.exe may not have
# /usr/bin (stat, date, cksum) on PATH.
case "${OSTYPE:-}" in msys*|cygwin*) PATH="/usr/bin:$PATH" ;; esac

. "${BASH_SOURCE[0]%/*}/local-mode.sh" 2>/dev/null || is_local_mode() { return 1; }
if is_local_mode; then
  if local_mode_ollama; then
    printf '🖥 Local model (Ollama) · not using your Claude account'
  else
    printf '🖥 Local model · not using your Claude account'
  fi
  exit 0
fi

export WORK_DOMAIN=""   # e.g. "acme.com" to label that domain as "Work"
export CONFIG="${CLAUDE_CONFIG_DIR:+$CLAUDE_CONFIG_DIR/.claude.json}"
# Claude Code on Windows keeps .claude.json in %USERPROFILE%, whatever $HOME says.
HOME_DIR="$HOME"
case "${OSTYPE:-}" in msys*|cygwin*) HOME_DIR="${USERPROFILE:-$HOME}" ;; esac
CONFIG="${CONFIG:-$HOME_DIR/.claude.json}"
NODE="$(command -v node)"
export TOKEN_ACCOUNT=""
if [ "${RCLAUDE_AUTH:-}" = "token" ]; then
  TOKEN_ACCOUNT="${RCLAUDE_ACCOUNT_EMAIL:-${RCLAUDE_ACCOUNT:-token}}"
fi

if [ -z "$NODE" ] || { [ -z "$TOKEN_ACCOUNT" ] && [ ! -f "$CONFIG" ]; }; then
  printf '👤 (unknown)'
  exit 0
fi

"$NODE" -e '
const fs = require("fs");
let o = {};
try { o = JSON.parse(fs.readFileSync(process.env.CONFIG, "utf8")); } catch (e) {}
const acct = o.oauthAccount || {};
const email = process.env.TOKEN_ACCOUNT || acct.emailAddress || acct.email || "";
if (!email) { process.stdout.write("👤 (not logged in)"); process.exit(0); }
const workDomain = (process.env.WORK_DOMAIN || "").toLowerCase();
// With WORK_DOMAIN set, tag the account Work (matching domain) or Personal.
// With WORK_DOMAIN empty, show just the email — no tag.
const tag = workDomain ? (email.toLowerCase().endsWith("@" + workDomain) ? "Work" : "Personal") : "";
process.stdout.write("👤 " + (tag ? tag + " · " : "") + email);
'
