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
# Pure bash, no node, so it adds nothing noticeable to a render.

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
