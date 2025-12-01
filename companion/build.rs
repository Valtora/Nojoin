fn main() {
    #[cfg(target_os = "windows")]
    {
        let mut res = winres::WindowsResource::new();
        res.set_icon("installer/icon.ico");
        res.set("ProductName", "Nojoin Companion");
        res.set("FileDescription", "Nojoin Companion - Meeting Intelligence");
        res.set("LegalCopyright", "Copyright (c) 2024 Valtora");
        
        if let Err(e) = res.compile() {
            eprintln!("Failed to compile Windows resources: {}", e);
        }
    }
}

