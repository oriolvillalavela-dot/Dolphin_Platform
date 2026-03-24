#!/usr/bin/env bash
set -euo pipefail

PORT="${PORT:-8011}"                
BASE_URL_PATH="${BASE_URL_PATH:-}"  

ARGS=( "app/main.py"
  "--server.headless=true"
  "--server.address=0.0.0.0"
  "--server.port=${PORT}"
  "--browser.gatherUsageStats=false"
  # friendlier behind proxies:
  "--server.enableXsrfProtection=false"
  "--server.enableCORS=false"
)

# If your platform serves under a sub-path (e.g., /apps/abc123)
if [[ -n "$BASE_URL_PATH" ]]; then
  BASE_URL_PATH="${BASE_URL_PATH#/}"    # strip leading slash if present
  ARGS+=( "--server.baseUrlPath=${BASE_URL_PATH}" )
fi

echo "Starting Streamlit on 0.0.0.0:${PORT} ${BASE_URL_PATH:+(base path: /$BASE_URL_PATH)}"
exec streamlit run "${ARGS[@]}"

