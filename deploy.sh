#!/usr/bin/env bash
# Test deploy to a Home Assistant host over SSH.
#
#   ./deploy.sh            copy the working tree into the running add-on
#                          container and restart it (seconds, not minutes)
#   ./deploy.sh --install  copy the add-on folder into /addons on the host and
#                          (re)build it as a local add-on; needed once, and
#                          again after a change to the Dockerfile or
#                          requirements.txt
#
# A deploy without --install lives in the container's writable layer only: the
# next rebuild or update of the add-on replaces it.
#
# Configure in .env (template: .env.example).
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [[ -f "$root/.env" ]]; then
  set -a
  # shellcheck disable=SC1091
  . "$root/.env"
  set +a
fi

HA_HOST="${HA_HOST:?HA_HOST is not set - see .env.example}"
SSH_USER="${SSH_USER:-root}"
SSH_PORT="${SSH_PORT:-22}"
SLUG="${SLUG:-local_matter_health}"
CONTAINER="${CONTAINER:-}"
APP_PATH=/opt/matter_health
addon="$root/matter_health"

remote() { ssh -p "$SSH_PORT" "$SSH_USER@$HA_HOST" "$@"; }

echo "[1/3] Building the page"
(cd "$addon/frontend" && npm run --silent build)

if [[ "${1:-}" == "--install" ]]; then
  echo "[2/3] Copying the add-on to /addons/matter_health"
  tar -C "$addon" -czf - \
    --exclude=node_modules --exclude=__pycache__ --exclude=static . |
    remote "rm -rf /addons/matter_health && mkdir -p /addons/matter_health && tar -xzf - -C /addons/matter_health"
  echo "[3/3] Building and starting the add-on (takes a few minutes)"
  remote "ha store reload >/dev/null
    if ha apps info $SLUG --raw-json 2>/dev/null | grep -q '\"version\":\"'; then
      ha apps rebuild $SLUG
    else
      ha apps install $SLUG
    fi
    curl -fsS -X POST -H \"Authorization: Bearer \$SUPERVISOR_TOKEN\" \\
      -H 'Content-Type: application/json' -d '{\"ingress_panel\": true}' \\
      http://supervisor/addons/$SLUG/options >/dev/null || true
    ha apps start $SLUG || true"
  echo "Done. Open it from the sidebar: Matter Health."
  exit 0
fi

# Home Assistant names the container app_<slug>; older versions addon_<slug>.
if [[ -z "$CONTAINER" ]]; then
  CONTAINER="$(remote "docker ps --format '{{.Names}}'" |
    grep -E "^(app|addon)_${SLUG}\$" || true)"
fi
if [[ -z "$CONTAINER" ]]; then
  echo "The add-on is not running - install it first: ./deploy.sh --install" >&2
  exit 1
fi
echo "[2/3] Copying code into $CONTAINER"
# docker cp, not a tar inside the container: the add-on's AppArmor profile
# keeps anything running in it from writing to its own code.
tar -C "$addon" -cf - --exclude=__pycache__ app translations |
  remote "docker cp - $CONTAINER:$APP_PATH"

echo "[3/3] Restarting"
remote "docker restart $CONTAINER >/dev/null"
echo "Done."
