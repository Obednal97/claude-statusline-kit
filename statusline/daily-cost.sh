#!/bin/bash
# Daily cost with 15-minute cache, non-blocking background refresh
CACHE_FILE="/tmp/ccusage-daily-cost-$(date +%Y%m%d).cache"
LOCK_FILE="/tmp/ccusage-daily-cost.lock"
CACHE_AGE=900  # 15 minutes

# Function to refresh cache
refresh_cache() {
  # Prevent concurrent refreshes
  if [ -f "$LOCK_FILE" ]; then
    return
  fi
  touch "$LOCK_FILE"

  TODAY=$(date +%Y%m%d)
  # No --offline: the bundled price table lags new models (Opus 4.8 / Fable 5),
  # which made this show $0.00. Live pricing prices them; ccusage caches it locally.
  COST=$(ccusage daily --since "$TODAY" --json 2>/dev/null | grep '"totalCost":' | tail -1 | sed 's/.*"totalCost": *\([0-9.]*\).*/\1/')
  if [ -n "$COST" ] && [ "$COST" != "0" ]; then
    printf "Day: \$%.2f" "$COST" > "$CACHE_FILE"
  else
    echo "Day: \$0.00" > "$CACHE_FILE"
  fi

  rm -f "$LOCK_FILE"
}

# If cache exists, always return it immediately
if [ -f "$CACHE_FILE" ]; then
  cat "$CACHE_FILE"

  # Check if cache is stale and needs background refresh
  CACHE_TIME=$(stat -f %m "$CACHE_FILE" 2>/dev/null || stat -c %Y "$CACHE_FILE" 2>/dev/null)
  NOW=$(date +%s)
  AGE=$((NOW - CACHE_TIME))
  if [ "$AGE" -ge "$CACHE_AGE" ]; then
    # Refresh in background (non-blocking)
    refresh_cache &
  fi
  exit 0
fi

# No cache exists - must refresh synchronously (first call of the day)
refresh_cache
cat "$CACHE_FILE" 2>/dev/null || echo "Day: \$0.00"
