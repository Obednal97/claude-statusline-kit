#!/usr/bin/env bash
# Today's cost with 15-minute cache, non-blocking background refresh.
#
# `ccusage` takes ~10s, longer than ccstatusline's widget timeout, so the
# refresh ALWAYS runs detached and the render only ever reads the cache.
# Until the first refresh of the day lands, this prints "Day: …".
# No --offline: the bundled price table lags new models, which showed $0.00.
PREFIX="Day"
CACHE_FILE="/tmp/ccusage-daily-cost-$(id -u)-$(date +%Y%m%d).cache"
LOCK_DIR="/tmp/ccusage-daily-cost-$(id -u).lockd"
CACHE_AGE=900   # refresh after 15 minutes
LOCK_TTL=120    # a lock older than this is from a killed refresh; ignore it
NODE="$(command -v node)"

refresh_cache() {
  if ! mkdir "$LOCK_DIR" 2>/dev/null; then
    LT=$(stat -f %m "$LOCK_DIR" 2>/dev/null || stat -c %Y "$LOCK_DIR" 2>/dev/null)
    [ $(( $(date +%s) - ${LT:-0} )) -lt "$LOCK_TTL" ] && return
    rm -rf "$LOCK_DIR"; mkdir "$LOCK_DIR" 2>/dev/null || return
  fi
  trap 'rm -rf "$LOCK_DIR"' EXIT

  [ -n "$NODE" ] || return
  # Prints the cost (0 if no usage today); prints nothing if ccusage failed,
  # so a failed run never overwrites a good cached value.
  COST=$(ccusage daily --since "$(date +%Y%m%d)" --json 2>/dev/null | "$NODE" -e 'const fs=require("fs");let o;try{o=JSON.parse(fs.readFileSync(0,"utf8"))}catch(e){process.exit(0)}const c=o&&o.totals&&o.totals.totalCost;process.stdout.write(String(c!=null?c:0))')
  if [ -n "$COST" ]; then
    printf "%s: \$%.2f" "$PREFIX" "$COST" > "$CACHE_FILE.tmp" && mv "$CACHE_FILE.tmp" "$CACHE_FILE"
  fi
}

STALE=1
if [ -f "$CACHE_FILE" ]; then
  cat "$CACHE_FILE"
  CT=$(stat -f %m "$CACHE_FILE" 2>/dev/null || stat -c %Y "$CACHE_FILE" 2>/dev/null)
  [ $(( $(date +%s) - ${CT:-0} )) -lt "$CACHE_AGE" ] && STALE=0
else
  printf "%s: …" "$PREFIX"
fi

# detach fds so the background refresh can never stall or be killed with the render
[ "$STALE" = "1" ] && ( refresh_cache </dev/null >/dev/null 2>&1 & )
exit 0
