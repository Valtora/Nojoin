# Default no-op backend hooks. Profile scripts override only the work they need.

worker_install_builder_os_packages() { :; }
worker_install_torch() { :; }
worker_install_backend_packages() { :; }
worker_cleanup_builder_os_packages() { :; }
worker_install_runtime_os_packages() { :; }
worker_patch_system_python() { :; }
worker_configure_runtime_libraries() { :; }
