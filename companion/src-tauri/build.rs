fn main() {
    // Swift runtime linker flags are only valid for the Unix linker (macOS/Linux).
    // They are invalid for the MSVC linker on Windows, so gate them out there.
    if cfg!(not(windows)) {
        println!("cargo:rustc-link-search=/usr/lib/swift");
        println!("cargo:rustc-link-arg=-Wl,-rpath,/usr/lib/swift");
    }
    tauri_build::build()
}
