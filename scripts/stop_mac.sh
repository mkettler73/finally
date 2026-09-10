#!/usr/bin/env bash
# Stop FinAlly (macOS / Linux).
#
#   ./scripts/stop_mac.sh             stop and remove the container; keep data
#   ./scripts/stop_mac.sh --purge     also delete the data volume (irreversible)
#
# Idempotent: stopping something that is not running is a no-op, not an error.
set -euo pipefail

CONTAINER_NAME="finally"
VOLUME_NAME="finally-data"

PURGE=false
for arg in "$@"; do
    case "$arg" in
        --purge) PURGE=true ;;
        -h|--help)
            sed -n '2,7p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
            exit 0
            ;;
        *) echo "Unknown option: $arg (try --help)" >&2; exit 2 ;;
    esac
done

if ! docker info >/dev/null 2>&1; then
    echo "Docker daemon is not running — nothing to stop."
    exit 0
fi

if docker container inspect "$CONTAINER_NAME" >/dev/null 2>&1; then
    echo "==> Stopping and removing container $CONTAINER_NAME"
    docker rm -f "$CONTAINER_NAME" >/dev/null
else
    echo "==> No container named $CONTAINER_NAME — nothing to stop."
fi

if $PURGE; then
    echo "==> Removing data volume $VOLUME_NAME (portfolio and trade history will be lost)"
    docker volume rm "$VOLUME_NAME" >/dev/null 2>&1 || echo "    (volume did not exist)"
else
    echo "==> Data volume $VOLUME_NAME kept. Pass --purge to delete it."
fi
