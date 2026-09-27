# Backend-specific hooks consumed by Dockerfile.worker.
# ROCm reuses the matched torch/torchaudio stack from its pinned PyTorch base.

worker_install_builder_os_packages() {
    :
}

worker_install_torch() {
    :
}

worker_install_backend_packages() {
    pip uninstall -y onnxruntime-gpu || true
    python -c "import onnxruntime as ort; print('ROCm worker ONNX providers:', ort.get_available_providers())" && \
        python -c "import triton; print('ROCm Triton:', triton.__version__)"
}

worker_cleanup_builder_os_packages() {
    :
}

worker_install_runtime_os_packages() {
    # MIOpen JIT-compiles HIP kernels at runtime and HIPRTC needs these headers.
    apt-get install -y --no-install-recommends libstdc++-13-dev && \
        test -f /usr/include/c++/13/utility
}

worker_patch_system_python() {
    /usr/bin/python3 -m pip install --no-cache-dir --break-system-packages \
        --root-user-action=ignore --upgrade pillow==12.3.0 urllib3==2.7.0
}

worker_configure_runtime_libraries() {
    echo "Using ROCm runtime libraries from the base image"
}
