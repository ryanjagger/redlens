#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

OPENEMR_COMPOSE_FILE="${OPENEMR_COMPOSE_FILE:-/Users/ryan/gauntlet/openemr/docker/development-easy/docker-compose.yml}"
OPENEMR_SERVICE="${OPENEMR_SERVICE:-openemr}"

docker compose \
  -f "$OPENEMR_COMPOSE_FILE" \
  exec -T "$OPENEMR_SERVICE" \
  php /dev/stdin "$@" < "$SCRIPT_DIR/mint_openemr_token.php"

