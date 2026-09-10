#!/usr/bin/env bash
# Start FinAlly (macOS / Linux).
#
#   ./scripts/start_mac.sh            build if needed, then run
#   ./scripts/start_mac.sh --build    force a rebuild first
#   ./scripts/start_mac.sh --open     open the browser once it is healthy
#
# Idempotent: running it again replaces the container and leaves the data volume
# — and therefore the portfolio — untouched.
set -euo pipefail

IMAGE_NAME="finally:latest"
CONTAINER_NAME="finally"
VOLUME_NAME="finally-data"
PORT="${FINALLY_PORT:-8000}"

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

FORCE_BUILD=false
OPEN_BROWSER=false
for arg in "$@"; do
    case "$arg" in
        --build) FORCE_BUILD=true ;;
        --open)  OPEN_BROWSER=true ;;
        -h|--help)
            sed -n '2,9p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
            exit 0
            ;;
        *) echo "Unknown option: $arg (try --help)" >&2; exit 2 ;;
    esac
done

if ! command -v docker >/dev/null 2>&1; then
    echo "Docker is not installed or not on PATH. See https://docs.docker.com/get-docker/" >&2
    exit 1
fi
if ! docker info >/dev/null 2>&1; then
    echo "Docker is installed but the daemon is not running. Start Docker Desktop and retry." >&2
    exit 1
fi

if [ ! -f .env ]; then
    echo "WARNING: no .env found. Copy .env.example to .env and add OPENROUTER_API_KEY"
    echo "         if you want the AI chat panel to work. Everything else runs without it."
fi

if $FORCE_BUILD || ! docker image inspect "$IMAGE_NAME" >/dev/null 2>&1; then
    echo "==> Building $IMAGE_NAME"
    docker build -t "$IMAGE_NAME" .
else
    echo "==> Using existing image $IMAGE_NAME (pass --build to rebuild)"
fi

# Replace any previous container. The volume is never touched, so data persists.
if docker container inspect "$CONTAINER_NAME" >/dev/null 2>&1; then
    echo "==> Removing previous container"
    docker rm -f "$CONTAINER_NAME" >/dev/null
fi

docker volume create "$VOLUME_NAME" >/dev/null

ENV_ARGS=()
[ -f .env ] && ENV_ARGS=(--env-file .env)

echo "==> Starting container"
if ! docker run -d \
    --name "$CONTAINER_NAME" \
    -p "${PORT}:8000" \
    -v "${VOLUME_NAME}:/app/db" \
    "${ENV_ARGS[@]+"${ENV_ARGS[@]}"}" \
    --restart unless-stopped \
    "$IMAGE_NAME" >/dev/null; then
    echo "    If port ${PORT} is already in use, set FINALLY_PORT to another port and retry." >&2
    # A container that fails at the networking stage is still created, stopped.
    # Clear it so the next run does not report "removing previous container".
    docker rm -f "$CONTAINER_NAME" >/dev/null 2>&1 || true
    exit 1
fi

# 127.0.0.1, not localhost, and this is the URL we print and open as well as
# the one we probe.
#
# Docker reports publishing on both 0.0.0.0:PORT and [::]:PORT, but nothing
# necessarily answers on IPv6 loopback. Where "localhost" resolves to ::1
# first, a browser sent to http://localhost:PORT gets a connection reset from
# a healthy, running container. Probing 127.0.0.1 while still advertising
# localhost is the worst of both: the script reports success and hands the
# user a dead link.
URL="http://127.0.0.1:${PORT}"
PROBE="${URL}/api/health"
echo -n "==> Waiting for $URL "
for _ in $(seq 1 60); do
    if curl -fsS "$PROBE" >/dev/null 2>&1; then
        echo
        echo "==> FinAlly is up:  $URL"
        $OPEN_BROWSER && command -v open >/dev/null 2>&1 && open "$URL"
        $OPEN_BROWSER && command -v xdg-open >/dev/null 2>&1 && xdg-open "$URL"
        exit 0
    fi
    if ! docker container inspect -f '{{.State.Running}}' "$CONTAINER_NAME" 2>/dev/null | grep -q true; then
        echo
        echo "==> Container exited during startup. Logs:" >&2
        docker logs "$CONTAINER_NAME" >&2 || true
        exit 1
    fi
    echo -n "."
    sleep 2
done

echo
echo "==> Timed out waiting for health. Recent logs:" >&2
docker logs --tail 50 "$CONTAINER_NAME" >&2 || true
exit 1
