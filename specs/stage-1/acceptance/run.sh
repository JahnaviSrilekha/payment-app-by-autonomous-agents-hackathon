#!/bin/sh
# One-command acceptance run for Pocketful stage 1.
#
#   ./run.sh [BASE_URL]        run the suite against an already-running service
#                              (BASE_URL defaults to http://localhost:8080)
#   ./run.sh --docker [PORT]   build the stage-1 image, start a container on
#                              PORT (default 9090), run the suite, tear down.
#                              Also verifies PORT is honoured and defaults to 8080 (R9).
set -e
HERE=$(cd "$(dirname "$0")" && pwd)
REPO=$(cd "$HERE/../../.." && pwd)
PY=python3

wait_healthy() {  # url timeout_seconds
    $PY - "$1" "$2" <<'EOF'
import sys, time, urllib.request
url, budget = sys.argv[1], float(sys.argv[2])
deadline = time.time() + budget
while True:
    try:
        if urllib.request.urlopen(url + "/health", timeout=2).status == 200:
            sys.exit(0)
    except Exception:
        pass
    if time.time() > deadline:
        sys.exit(1)
    time.sleep(2)
EOF
}

if [ "${1:-}" = "--docker" ]; then
    PORT=${2:-9090}
    DF=""
    for c in "$REPO/stage-1/Dockerfile" "$REPO/Dockerfile"; do
        [ -f "$c" ] && DF=$c && break
    done
    if [ -z "$DF" ]; then
        echo "No Dockerfile found (looked for $REPO/stage-1/Dockerfile and $REPO/Dockerfile)" >&2
        exit 2
    fi
    IMG=pocketful-acceptance-s1
    docker build -f "$DF" -t "$IMG" "$(dirname "$DF")" >&2
    CID=$(docker run -d -e PORT=$PORT -p $PORT:$PORT "$IMG")
    CID2=""
    cleanup() {
        [ -n "$CID" ] && docker rm -f "$CID" >/dev/null 2>&1 || true
        [ -n "$CID2" ] && docker rm -f "$CID2" >/dev/null 2>&1 || true
    }
    trap cleanup EXIT
    if ! wait_healthy "http://localhost:$PORT" 60; then
        echo "FAIL (R7): container not healthy within 60s; logs:" >&2
        docker logs "$CID" >&2 || true
        exit 2
    fi
    echo "Container healthy (within 60s)" >&2
    RUN_OK=0
    $PY "$HERE/run.py" "http://localhost:$PORT" --wait 30 || RUN_OK=$?

    # R9: the container must also serve on the default port 8080 without -e PORT.
    CID2=$(docker run -d -p 18080:8080 "$IMG")
    if ! wait_healthy "http://localhost:18080" 30; then
        echo "FAIL (R9): container without -e PORT did not serve on default 8080" >&2
        docker logs "$CID2" >&2 || true
        exit 1
    fi
    echo "R9 ok: default port 8080 honoured without -e PORT" >&2
    exit $RUN_OK
fi

BASE=${1:-http://localhost:8080}
shift 2>/dev/null || true
exec $PY "$HERE/run.py" "$BASE" "$@"