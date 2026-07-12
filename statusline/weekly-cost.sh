#!/usr/bin/env bash
# Week-to-date cost with 15-minute cache, non-blocking background refresh.
# Mirrors daily-cost.sh. Uses `ccusage weekly` and takes the LAST bucket
# (the in-progress current week). No --offline: bundled pricing lags new models.
CACHE_FILE="/tmp/ccusage-weekly-cost-$(date +%Y%m%d).cache"
LOCK_FILE="/tmp/ccusage-weekly-cost.lock"
CACHE_AGE=900  # 15 minutes
NODE="$(command -v node)"

refresh_cache() {
  if [ -f "$LOCK_FILE" ]; then
    return
  fi
  touch "$LOCK_FILE"

  COST=""
  if [ -n "$NODE" ]; then
    COST=$(ccusage weekly --json 2>/dev/null | "$NODE" -e 'const fs=require("fs");let s="";try{s=fs.readFileSync(0,"utf8")}catch(e){}let o={};try{o=JSON.parse(s)}catch(e){}const a=(o.weekly||[]);const last=a[a.length-1];process.stdout.write(last&&last.totalCost!=null?String(last.totalCost):"")')
  fi

  if [ -n "$COST" ] && [ "$COST" != "0" ]; then
    printf "Wk: \$%.2f" "$COST" > "$CACHE_FILE"
  else
    echo "Wk: \$0.00" > "$CACHE_FILE"
  fi

  rm -f "$LOCK_FILE"
}

if [ -f "$CACHE_FILE" ]; then
  cat "$CACHE_FILE"
  CACHE_TIME=$(stat -f %m "$CACHE_FILE" 2>/dev/null || stat -c %Y "$CACHE_FILE" 2>/dev/null)
  NOW=$(date +%s)
  AGE=$((NOW - CACHE_TIME))
  if [ "$AGE" -ge "$CACHE_AGE" ]; then
    refresh_cache &
  fi
  exit 0
fi

refresh_cache
cat "$CACHE_FILE" 2>/dev/null || echo "Wk: \$0.00"
