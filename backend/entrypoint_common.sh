# Shared bind-mount ownership repair, sourced by backend/entrypoint.sh and
# backend/worker_entrypoint.sh while they are still root, immediately before they
# drop to appuser via gosu. Sourced rather than duplicated: the api and worker
# entrypoints previously carried their own copy of this logic and had already
# started to drift.
#
# Why this exists at all: Docker creates a missing bind-mount source on the host
# as root:root, which the unprivileged runtime user (uid 1000) cannot then write
# into. The containers therefore enter as root purely to correct that, and run
# everything else as appuser.

# The data root. NOJOIN_DATA_DIR is the same override the application honours
# (backend/utils/path_manager.py::_get_container_data_directory), so a deployment
# that relocates its data directory still gets it repaired.
nojoin_data_root() {
    if [ -n "${NOJOIN_DATA_DIR:-}" ]; then
        case "$NOJOIN_DATA_DIR" in
            /*) printf '%s\n' "$NOJOIN_DATA_DIR" ;;
            *) printf '/app/%s\n' "$NOJOIN_DATA_DIR" ;;
        esac
        return
    fi
    printf '/app/data\n'
}

# Every directory under the data root that a container must be able to write to.
# Each is checked and repaired independently: a parent-only check cannot see a
# child that was recreated as root under an already-correct parent, which is the
# failure in issue #153. Keep this list in step with the paths the application
# creates at runtime.
NOJOIN_WRITE_CRITICAL_DIRS="recordings
recordings/temp
recordings/failed
logs
documents
backups
temp_uploads
temp_restores
restore_staging
cli-oauth"

nojoin_owner_uid() {
    stat -c %u "$1" 2>/dev/null || printf 'unknown\n'
}

# chown is deliberately non-fatal. Both entrypoints run under `set -e`, and some
# supported bind-mount backends (notably a Windows-drive mount under Docker
# Desktop) either reject chown or silently ignore it. A container that refuses to
# start there would be a worse outcome than one that starts and reports the
# problem through the admin health surface, so failures warn and continue.
nojoin_chown_tree() {
    if chown -R appuser:appuser "$1" 2>/dev/null; then
        return 0
    fi
    echo "nojoin: WARNING: could not take ownership of $1 (uid $(nojoin_owner_uid "$1"))." >&2
    echo "nojoin: the runtime user is uid 1000; writes below this path will fail until it is chowned on the host." >&2
    return 1
}

nojoin_repair_data_ownership() {
    root="$(nojoin_data_root)"

    mkdir -p "$root" 2>/dev/null || true
    if [ ! -d "$root" ]; then
        echo "nojoin: WARNING: data directory $root does not exist and could not be created." >&2
        return 0
    fi

    # Create every write-critical directory before deciding what to repair, so
    # one ownership pass covers them all.
    echo "$NOJOIN_WRITE_CRITICAL_DIRS" | while IFS= read -r relative; do
        [ -n "$relative" ] || continue
        mkdir -p "$root/$relative" 2>/dev/null || true
    done

    # Whole-tree repair only when the root itself is mis-owned. That is the first
    # boot on a fresh ./data, when the tree is empty and the recursive walk is
    # cheap; on later restarts a large recordings library is never walked.
    if [ "$(nojoin_owner_uid "$root")" != "1000" ]; then
        nojoin_chown_tree "$root" || true
        return 0
    fi

    # Root is already correct, so repair only the specific children that are not.
    # This is the case a parent-only check misses.
    echo "$NOJOIN_WRITE_CRITICAL_DIRS" | while IFS= read -r relative; do
        [ -n "$relative" ] || continue
        directory="$root/$relative"

        [ -d "$directory" ] || continue
        [ "$(nojoin_owner_uid "$directory")" = "1000" ] || {
            echo "nojoin: repairing ownership of $directory" >&2
            nojoin_chown_tree "$directory" || true
        }
    done
}
