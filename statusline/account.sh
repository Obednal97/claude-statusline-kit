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

# Windows (Git Bash, MSYS2, Cygwin): bash.exe launched by cmd.exe may not have
# /usr/bin (stat, date, cksum) on PATH.
case "${OSTYPE:-}" in msys*|cygwin*) PATH="/usr/bin:$PATH" ;; esac

export WORK_DOMAIN=""   # e.g. "acme.com" to label that domain as "Work"
export CONFIG="${CLAUDE_CONFIG_DIR:+$CLAUDE_CONFIG_DIR/.claude.json}"
# Claude Code on Windows keeps .claude.json in %USERPROFILE%, whatever $HOME says.
HOME_DIR="$HOME"
case "${OSTYPE:-}" in msys*|cygwin*) HOME_DIR="${USERPROFILE:-$HOME}" ;; esac
CONFIG="${CONFIG:-$HOME_DIR/.claude.json}"
NODE="$(command -v node)"

if [ -z "$NODE" ] || [ ! -f "$CONFIG" ]; then
  printf '👤 (unknown)'
  exit 0
fi

"$NODE" -e '
const fs = require("fs");
let o = {};
try { o = JSON.parse(fs.readFileSync(process.env.CONFIG, "utf8")); } catch (e) {}
const acct = o.oauthAccount || {};
const email = acct.emailAddress || acct.email || "";
if (!email) { process.stdout.write("👤 (not logged in)"); process.exit(0); }
const workDomain = (process.env.WORK_DOMAIN || "").toLowerCase();
// With WORK_DOMAIN set, tag the account Work (matching domain) or Personal.
// With WORK_DOMAIN empty, show just the email — no tag.
const tag = workDomain ? (email.toLowerCase().endsWith("@" + workDomain) ? "Work" : "Personal") : "";
process.stdout.write("👤 " + (tag ? tag + " · " : "") + email);
'
