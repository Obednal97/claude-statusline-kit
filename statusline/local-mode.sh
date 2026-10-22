# Shared helper, sourced by the widgets (not a widget itself): is this Claude
# Code session running on a local or other non-Anthropic model?
#
# Wrappers such as claude-local point Claude Code at Ollama by setting
# ANTHROPIC_BASE_URL (e.g. http://localhost:11434). Any base URL whose host is
# not anthropic.com or a subdomain of it counts as local mode: localhost,
# 127.0.0.1, ::1, *.local, a LAN box or any other server. Unset or empty means
# Anthropic, as usual.
#
# Override: STATUSLINE_LOCAL_MODE=1 forces local mode on, 0 forces it off (for
# example behind a corporate proxy that forwards to Anthropic).
#
# After sourcing:
#   is_local_mode       exit status 0 in local mode
#   local_mode_ollama   exit status 0 when the endpoint looks like Ollama
#                       (port 11434, or ANTHROPIC_AUTH_TOKEN=ollama)
#   local_server_json   prints what Ollama has loaded for this model (see below)
#   local_native_path   a path node can open (C:/... on Windows)
# Detection is pure bash, so sourcing this adds nothing noticeable to a render.

is_local_mode() {
  case "${STATUSLINE_LOCAL_MODE:-}" in 1) return 0 ;; 0) return 1 ;; esac
  local host="${ANTHROPIC_BASE_URL:-}"
  [ -n "$host" ] || return 1
  case "$host" in *://*) host="${host#*://}" ;; esac
  host="${host%%[/?#]*}"   # drop path, query, fragment
  host="${host##*@}"       # drop user:password@
  case "$host" in
    \[*) host="${host#\[}"; host="${host%%\]*}" ;;   # [::1]:11434
    *:*:*) ;;                                         # bare IPv6, no port
    *) host="${host%%:*}" ;;                          # host:port
  esac
  host="$(printf '%s' "$host" | tr '[:upper:]' '[:lower:]')"
  host="${host%.}"
  [ -n "$host" ] || return 1
  case "$host" in anthropic.com|*.anthropic.com) return 1 ;; esac
  return 0
}

local_mode_ollama() {
  [ "${ANTHROPIC_AUTH_TOKEN:-}" = "ollama" ] && return 0
  case "${ANTHROPIC_BASE_URL:-}" in *:11434|*:11434/*) return 0 ;; esac
  return 1
}

local_native_path() {
  case "${OSTYPE:-}" in msys*|cygwin*) cygpath -m "$1" ;; *) printf '%s' "$1" ;; esac
}

# Ollama server stats. A detached refresh asks the server what it has loaded
# (GET /api/ps, at most every LOCAL_SERVER_MAX_AGE seconds) and caches a small
# summary for ANTHROPIC_MODEL (or the only loaded model when that is unset):
#   {"state":"loaded","size":<bytes>,"vram":<bytes>,"ctx":<tokens or 0>}
#   {"state":"idle"}     server up, model not loaded (the next prompt reloads it)
#   {"state":"down"}     no answer from the server
#   {"state":"unknown"}  answered, but not like Ollama
# local_server_json prints the cached summary, or nothing when there is none
# yet, and never waits on the network. Only for Ollama endpoints.
LOCAL_SERVER_MAX_AGE=10    # seconds between refreshes
LOCAL_SERVER_TRUST=300     # ignore a summary older than this (refresh stuck)

local_server_cache() {
  local key
  key=$(printf '%s|%s' "${ANTHROPIC_BASE_URL:-}" "${ANTHROPIC_MODEL:-}" | cksum | cut -d" " -f1)
  printf '/tmp/ccstatusline-local-server-%s-%s.json' "$(id -u)" "$key"
}

local_server_json() {
  local_mode_ollama || return 0
  local cache ct age=999999
  cache=$(local_server_cache)
  if [ -f "$cache" ]; then
    ct=$(stat -c %Y "$cache" 2>/dev/null || stat -f %m "$cache" 2>/dev/null)
    age=$(( $(date +%s) - ${ct:-0} ))
  fi
  [ "$age" -lt "$LOCAL_SERVER_TRUST" ] && cat "$cache" 2>/dev/null
  # detach fds so the background refresh can never stall the render
  [ "$age" -ge "$LOCAL_SERVER_MAX_AGE" ] && ( _local_server_refresh "$cache" </dev/null >/dev/null 2>&1 & )
  return 0
}

_local_server_refresh() {
  local cache="$1" lock="$1.lockd" node url origin ps lt
  if ! mkdir "$lock" 2>/dev/null; then
    lt=$(stat -c %Y "$lock" 2>/dev/null || stat -f %m "$lock" 2>/dev/null)
    [ $(( $(date +%s) - ${lt:-0} )) -lt 30 ] && return   # a refresh is running
    rm -rf "$lock"; mkdir "$lock" 2>/dev/null || return
  fi
  trap 'rm -rf "$lock"' EXIT
  node=$(command -v node) || return
  command -v curl >/dev/null 2>&1 || return
  url="${ANTHROPIC_BASE_URL:-}"
  case "$url" in *://*) ;; *) url="http://$url" ;; esac
  origin="${url%%://*}://$(printf '%s' "${url#*://}" | cut -d/ -f1)"
  ps=$(curl -fsS --max-time 3 "$origin/api/ps" 2>/dev/null) || ps=""
  printf '%s' "$ps" | MODEL="${ANTHROPIC_MODEL:-}" "$node" -e '
    const fs = require("fs");
    let raw = ""; try { raw = fs.readFileSync(0, "utf8"); } catch (e) {}
    let o = null; try { o = JSON.parse(raw); } catch (e) {}
    // Ollama names default to the latest tag: "mistral" is "mistral:latest"
    const norm = s => { s = String(s || "").toLowerCase().replace(/\[.*$/, "").trim(); return !s || s.includes(":") ? s : s + ":latest"; };
    let r;
    if (!raw) r = { state: "down" };
    else if (!o || !Array.isArray(o.models)) r = { state: "unknown" };
    else {
      const want = norm(process.env.MODEL);
      const m = want ? o.models.find(x => norm(x.name) === want || norm(x.model) === want)
                     : (o.models.length === 1 ? o.models[0] : null);
      r = m ? { state: "loaded", size: Number(m.size) || 0, vram: Number(m.size_vram) || 0, ctx: Number(m.context_length) || 0 }
            : { state: "idle" };
    }
    process.stdout.write(JSON.stringify(r));
  ' > "$cache.tmp" 2>/dev/null
  if [ -s "$cache.tmp" ]; then mv "$cache.tmp" "$cache"; else rm -f "$cache.tmp"; fi
}
