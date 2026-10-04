#!/usr/bin/env bash
# Week-to-date cost (weeks start Sunday, ccusage default) with 15-minute cache,
# non-blocking background refresh. Mirrors daily-cost.sh.
#
# `ccusage` takes ~10s, longer than ccstatusline's widget timeout, so the
# refresh ALWAYS runs detached and the render only ever reads the cache.
# Until the first refresh of the week lands, this prints "Wk: …".
# No --offline: the bundled price table lags new models, which showed $0.00.
PREFIX="Wk"
# Each config dir ($CLAUDE_CONFIG_DIR) is its own account with its own transcripts,
# and ccusage only reads the active one, so key the cache per config dir.
ACCT=$(printf %s "${CLAUDE_CONFIG_DIR:-default}" | cksum | cut -d" " -f1)
CACHE_FILE="/tmp/ccusage-weekly-cost-$(id -u)-$ACCT-$(date +%Y-%U).cache"
LOCK_DIR="/tmp/ccusage-weekly-cost-$(id -u)-$ACCT.lockd"
CACHE_AGE=900   # refresh after 15 minutes
LOCK_TTL=120    # a lock older than this is from a killed refresh; ignore it
NODE="$(command -v node)"

refresh_cache() {
  if ! mkdir "$LOCK_DIR" 2>/dev/null; then
    LT=$(stat -c %Y "$LOCK_DIR" 2>/dev/null || stat -f %m "$LOCK_DIR" 2>/dev/null)
    [ $(( $(date +%s) - ${LT:-0} )) -lt "$LOCK_TTL" ] && return
    rm -rf "$LOCK_DIR"; mkdir "$LOCK_DIR" 2>/dev/null || return
  fi
  trap 'rm -rf "$LOCK_DIR"' EXIT

  [ -n "$NODE" ] || return
  DOW=$(date +%w)   # days since Sunday
  WEEK_START=$(date -v-"${DOW}"d +%Y%m%d 2>/dev/null || date -d "-${DOW} days" +%Y%m%d)
  # --since limits output to this period, so the last bucket is the current one
  # (0 if no usage yet). Prints nothing if ccusage failed,
  # so a failed run never overwrites a good cached value.
  COST=$(ccusage weekly --since "$WEEK_START" --json 2>/dev/null | "$NODE" -e 'const fs=require("fs");let o;try{o=JSON.parse(fs.readFileSync(0,"utf8"))}catch(e){process.exit(0)}const a=(o&&o.weekly)||[];const l=a[a.length-1];const c=l&&l.totalCost;process.stdout.write(String(c!=null?c:0))')
  if [ -n "$COST" ]; then
    printf "%s: \$%.2f" "$PREFIX" "$COST" > "$CACHE_FILE.tmp" && mv "$CACHE_FILE.tmp" "$CACHE_FILE"
  fi
}

STALE=1
if [ -f "$CACHE_FILE" ]; then
  cat "$CACHE_FILE"
  CT=$(stat -c %Y "$CACHE_FILE" 2>/dev/null || stat -f %m "$CACHE_FILE" 2>/dev/null)
  [ $(( $(date +%s) - ${CT:-0} )) -lt "$CACHE_AGE" ] && STALE=0
else
  printf "%s: …" "$PREFIX"
fi

# detach fds so the background refresh can never stall or be killed with the render
[ "$STALE" = "1" ] && ( refresh_cache </dev/null >/dev/null 2>&1 & )
exit 0
