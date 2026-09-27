# CPU-specific hooks consumed by Dockerfile.worker.

worker_install_builder_os_packages() {
    apt-get install -y --no-install-recommends python3 python-is-python3
}

worker_install_torch() {
    test -n "${TORCH_VERSION}" && test -n "${TORCHAUDIO_VERSION}" && \
        pip install --no-cache-dir \
            --extra-index-url https://download.pytorch.org/whl/cpu \
            "torch==${TORCH_VERSION}+cpu" "torchaudio==${TORCHAUDIO_VERSION}+cpu"
}

worker_install_backend_packages() {
    python -c "import onnxruntime as ort; print('CPU worker ONNX providers:', ort.get_available_providers())" && \
        echo "CPU worker does not need Triton"
}

worker_cleanup_builder_os_packages() {
    apt-get purge --auto-remove -y python-is-python3
}

worker_patch_system_python() {
    echo "CPU base has no preinstalled PyTorch system Python packages to patch"
}

worker_configure_runtime_libraries() {
    echo "Using CPU-only PyTorch and ONNX Runtime"
}
