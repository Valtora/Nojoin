# CUDA-specific hooks consumed by Dockerfile.worker.

worker_install_backend_packages() {
    pip uninstall -y onnxruntime onnxruntime-gpu && \
        pip install --no-cache-dir onnxruntime-gpu==1.20.2 && \
        pip install --no-cache-dir triton
}

worker_patch_system_python() {
    /usr/bin/python3 -m pip install --no-cache-dir --break-system-packages \
        --root-user-action=ignore --upgrade pillow==12.3.0 urllib3==2.7.0
}

worker_configure_runtime_libraries() {
    /usr/bin/python3 -c \
        "import nvidia, os, glob; \
         print('\\n'.join(sorted(d for p in nvidia.__path__ \
               for d in glob.glob(os.path.join(p, '*', 'lib')))))" \
        > /etc/ld.so.conf.d/nvidia-cuda-wheels.conf && \
        ldconfig && \
        ldconfig -p | grep -q libcudnn_adv.so.9 && \
        ldconfig -p | grep -q libcublasLt.so.12
}
