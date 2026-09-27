# Source common no-op hooks then the selected backend's implementation.
: "${WORKER_INFERENCE_BACKEND:?WORKER_INFERENCE_BACKEND is required}"

case "${WORKER_INFERENCE_BACKEND}" in
    cpu | cuda | rocm) ;;
    *)
        echo "Unsupported WORKER_INFERENCE_BACKEND=${WORKER_INFERENCE_BACKEND}" >&2
        return 1
        ;;
esac

. /opt/nojoin-worker-backends/common.sh
. "/opt/nojoin-worker-backends/${WORKER_INFERENCE_BACKEND}.sh"
