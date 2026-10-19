#!/usr/bin/env bash
# Rebuild the public demo for the latest Nojoin release. See demo/README.md.
#
#   refresh.sh             act only when a release newer than the last one exists
#   refresh.sh --force     redo the latest release even if it was already done
#   refresh.sh --recapture recapture and rebuild at the current checkout, without
#                          upgrading the seed instance or moving the checkout
#
# Configuration comes from the file named by DEMO_REFRESH_ENV. Every step that
# can fail leaves the running demo as it was: fixtures are captured into a new
# directory and only swapped in once the capture is complete, and a failed
# check after the rebuild puts the previous fixtures back.

set -euo pipefail
# The fixture API reads the captures as another user, so they must be readable
# whatever umask the caller runs with.
umask 022

: "${DEMO_REFRESH_ENV:?Set DEMO_REFRESH_ENV to the refresh configuration file}"
# shellcheck disable=SC1090
source "$DEMO_REFRESH_ENV"

: "${DEMO_REPO:?}"            # clean clone of the Nojoin repository
: "${FIXTURES_DIR:?}"         # where the running demo reads its fixtures
: "${SEED_DIR:?}"             # directory holding the seed instance's compose file and .env
: "${SEED_URL:?}"             # the seed instance's address, e.g. https://localhost:16443
: "${SEED_PASSWORD:?}"        # the seeded account's password
: "${DEMO_URL:?}"             # the public demo, e.g. https://demo.nojoin.co.uk
STATE_FILE="${STATE_FILE:-$FIXTURES_DIR.release}"
LOCK_FILE="${LOCK_FILE:-$FIXTURES_DIR.lock}"
# The seed services a capture needs. Workers stay stopped, so a refresh never
# touches the GPU.
SEED_SERVICES="${SEED_SERVICES:-db redis socket-proxy api frontend nginx}"
IMAGE_REGISTRY="${IMAGE_REGISTRY:-ghcr.io/valtora}"
MIN_ENTRIES="${MIN_ENTRIES:-40}"
PLAYWRIGHT_VERSION="${PLAYWRIGHT_VERSION:-1.62.1}"

log() { printf '%s %s\n' "$(date -u +%FT%TZ)" "$*"; }

notify() {
    [ -n "${NOTIFY_URL:-}" ] || return 0
    curl -fsS -X POST -F "title=$1" -F "body=$2" ${NOTIFY_TAG:+-F "tag=$NOTIFY_TAG"} \
        "$NOTIFY_URL" >/dev/null || log "WARN: notification failed"
}

fail() {
    log "FAIL: $1"
    notify "Nojoin demo refresh failed" "$1"
    exit 1
}

seed() { docker compose --project-directory "$SEED_DIR" -f "$SEED_DIR/docker-compose.yml" "$@"; }
demo() { docker compose --project-directory "$DEMO_REPO/demo" -f "$DEMO_REPO/demo/compose.yaml" "$@"; }

set_seed_env() {
    local key=$1 value=$2 file="$SEED_DIR/.env"
    if grep -q "^$key=" "$file"; then
        sed -i "s|^$key=.*|$key=$value|" "$file"
    else
        printf '%s=%s\n' "$key" "$value" >>"$file"
    fi
}

wait_for() {
    local url=$1 tries=${2:-60}
    for _ in $(seq "$tries"); do
        if curl -fsSk -o /dev/null "$url"; then return 0; fi
        sleep 5
    done
    return 1
}

upgrade_seed() {
    local version=$1
    log "upgrading the seed instance to $version"
    for image in nojoin-api nojoin-frontend; do
        docker pull -q "$IMAGE_REGISTRY/$image:$version" >/dev/null || fail "could not pull $image:$version"
    done
    set_seed_env SEED_API_IMAGE "$IMAGE_REGISTRY/nojoin-api:$version"
    set_seed_env SEED_FRONTEND_IMAGE "$IMAGE_REGISTRY/nojoin-frontend:$version"
    git -C "$DEMO_REPO" fetch -q --tags origin
    git -C "$DEMO_REPO" checkout -q --detach "v$version" || fail "could not check out v$version"
}

capture() {
    local out=$1
    rm -rf "$out"
    mkdir -p "$out"
    docker run --rm --network host --user "$(id -u):$(id -g)" -e HOME=/tmp \
        -v "$DEMO_REPO/demo/capture:/capture:ro" -v "$out:/fixtures" \
        -e SEED_URL="$SEED_URL" -e SEED_PASSWORD="$SEED_PASSWORD" -e FIXTURES_DIR=/fixtures \
        "mcr.microsoft.com/playwright:v$PLAYWRIGHT_VERSION-noble" \
        sh -c "cd /tmp && npm i -s playwright@$PLAYWRIGHT_VERSION >/dev/null 2>&1 && cp /capture/capture.mjs . && node capture.mjs" \
        || return 1
    chmod -R a+rX "$out"
    local count
    count=$(jq '.entries | length' "$out/index.json" 2>/dev/null || echo 0)
    log "captured $count responses"
    [ "$count" -ge "$MIN_ENTRIES" ]
}

main() {
    local mode=${1:-release}
    exec 9>"$LOCK_FILE"
    flock -n 9 || { log "another refresh is running"; exit 0; }

    local release="" version=""
    if [ "$mode" != "--recapture" ]; then
        release=$(curl -fsS https://api.github.com/repos/Valtora/Nojoin/releases/latest | jq -r .tag_name) \
            || fail "could not read the latest release"
        if [ "$mode" != "--force" ] && [ "$release" = "$(cat "$STATE_FILE" 2>/dev/null || true)" ]; then
            exit 0
        fi
        version=${release#v}
        log "refreshing the demo for $release"
        upgrade_seed "$version"
    else
        log "recapturing at $(git -C "$DEMO_REPO" describe --tags --always)"
    fi

    # shellcheck disable=SC2086
    seed up -d $SEED_SERVICES >/dev/null 2>&1 || fail "the seed instance did not start"
    wait_for "$SEED_URL/login" || fail "the seed instance did not answer"

    if ! capture "$FIXTURES_DIR.next"; then
        seed stop >/dev/null 2>&1 || true
        fail "the capture failed or came back short; the demo is unchanged"
    fi
    seed stop >/dev/null 2>&1 || true

    rm -rf "$FIXTURES_DIR.prev"
    [ -d "$FIXTURES_DIR" ] && mv "$FIXTURES_DIR" "$FIXTURES_DIR.prev"
    mv "$FIXTURES_DIR.next" "$FIXTURES_DIR"

    # Recreated rather than restarted, so the API binds the new directory.
    demo up -d --build --force-recreate >/dev/null 2>&1 || fail "the demo did not rebuild"
    if ! wait_for "$DEMO_URL/login" 24 || ! wait_for "$DEMO_URL/api/v1/version" 12; then
        if [ -d "$FIXTURES_DIR.prev" ]; then
            rm -rf "$FIXTURES_DIR.failed"
            mv "$FIXTURES_DIR" "$FIXTURES_DIR.failed"
            mv "$FIXTURES_DIR.prev" "$FIXTURES_DIR"
            demo up -d --force-recreate >/dev/null 2>&1 || true
        fi
        fail "the rebuilt demo did not answer at $DEMO_URL; the previous fixtures are back"
    fi

    if [ -n "$release" ]; then
        printf '%s\n' "$release" >"$STATE_FILE"
        notify "Nojoin demo refreshed" "$DEMO_URL now runs $release."
    fi
    log "done"
}

# Everything runs from main, so a checkout that rewrites this file mid-run
# cannot change the commands bash has yet to read.
{ main "$@"; exit $?; }
