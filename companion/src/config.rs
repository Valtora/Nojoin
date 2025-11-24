use serde::{Deserialize, Serialize};
use std::fs;

#[derive(Serialize, Deserialize, Clone, Debug)]
pub struct Config {
    pub api_url: String,
    pub api_token: String,
}

impl Config {
    pub fn load() -> Self {
        let config_path = "config.json";
        let mut path_to_check = std::path::PathBuf::from(config_path);
        
        // Check current directory
        if !path_to_check.exists() {
            // Check executable directory
            if let Ok(exe_path) = std::env::current_exe() {
                if let Some(exe_dir) = exe_path.parent() {
                    let alt_path = exe_dir.join("config.json");
                    if alt_path.exists() {
                        path_to_check = alt_path;
                    }
                }
            }
        }

        if path_to_check.exists() {
            let content = fs::read_to_string(path_to_check).expect("Failed to read config.json");
            serde_json::from_str(&content).expect("Failed to parse config.json")
        } else {
            println!("Info: config.json not found. Creating default config.");
            let default_config = Config {
                api_url: "http://localhost:8000/api/v1".to_string(),
                api_token: "".to_string(),
            };
            
            // Try to write to current directory
            if let Ok(json) = serde_json::to_string_pretty(&default_config) {
                if let Err(e) = fs::write(config_path, json) {
                    eprintln!("Failed to write default config.json: {}", e);
                } else {
                    println!("Created default config.json");
                }
            }
            
            default_config
        }
    }

    pub fn save(&self) -> Result<(), std::io::Error> {
        let config_path = "config.json";
        // We always save to the current working directory for simplicity in this context,
        // or we could try to find where it was loaded from. 
        // For now, let's save to "config.json" in CWD.
        let json = serde_json::to_string_pretty(self)?;
        fs::write(config_path, json)?;
        Ok(())
    }
}
