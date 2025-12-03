use serde::{Deserialize, Serialize};
use std::fs;
use std::path::PathBuf;
use log::info;
use directories::ProjectDirs;

const DEFAULT_API_PORT: u16 = 14443;
const DEFAULT_LOCAL_PORT: u16 = 12345;

#[derive(Serialize, Deserialize, Clone, Debug)]
pub struct Config {
    #[serde(default = "default_api_port")]
    pub api_port: u16,
    #[serde(default = "default_api_host")]
    pub api_host: String,
    #[serde(default)]
    pub api_token: String,
    #[serde(default = "default_local_port")]
    pub local_port: u16,
    #[serde(default)]
    pub input_device_name: Option<String>,
    #[serde(default)]
    pub output_device_name: Option<String>,
    #[serde(default)]
    pub last_version: Option<String>,
}

fn default_api_port() -> u16 {
    DEFAULT_API_PORT
}

fn default_api_host() -> String {
    "localhost".to_string()
}

fn default_local_port() -> u16 {
    DEFAULT_LOCAL_PORT
}

/// Legacy config format for migration from older versions.
/// Fields may not all be used directly, but are needed for deserialization.
#[derive(Deserialize)]
#[allow(dead_code)]
struct LegacyConfig {
    api_url: Option<String>,
    api_token: Option<String>,
    web_app_url: Option<String>,
    input_device_name: Option<String>,
    output_device_name: Option<String>,
}

impl Config {
    pub fn get_api_url(&self) -> String {
        format!("https://{}:{}/api/v1", self.api_host, self.api_port)
    }

    pub fn get_web_url(&self) -> String {
        format!("https://{}:{}", self.api_host, self.api_port)
    }

    fn get_config_path() -> PathBuf {
        let config_name = "config.json";
        
        // First check current directory (development override)
        let cwd_path = PathBuf::from(config_name);
        if cwd_path.exists() {
            return cwd_path;
        }
        
        // Check standard system locations using directories crate
        if let Some(proj_dirs) = ProjectDirs::from("com", "Valtora", "Nojoin") {
            let config_dir = proj_dirs.config_dir();
            let app_config = config_dir.join(config_name);
            
            // If it exists, use it
            if app_config.exists() {
                return app_config;
            }
            
            // If we are on Linux or MacOS, we prefer this location for new configs
            #[cfg(any(target_os = "linux", target_os = "macos"))]
            {
                // Ensure directory exists
                if let Err(e) = fs::create_dir_all(config_dir) {
                    log::warn!("Failed to create config directory: {}", e);
                } else {
                    return app_config;
                }
            }
        }

        // Check executable directory (Legacy/Portable fallback)
        if let Ok(exe_path) = std::env::current_exe() {
            if let Some(exe_dir) = exe_path.parent() {
                let exe_config = exe_dir.join(config_name);
                if exe_config.exists() {
                    return exe_config;
                }
            }
        }
        
        // Check %LOCALAPPDATA%/Nojoin on Windows (Legacy manual check, directories crate handles this too but keeping for compat)
        #[cfg(windows)]
        if let Some(local_app_data) = std::env::var_os("LOCALAPPDATA") {
            let app_config = PathBuf::from(local_app_data).join("Nojoin").join(config_name);
            if app_config.exists() {
                return app_config;
            }
            // For Windows, we also prefer this for new configs if directories crate failed or we want explicit path
            return app_config;
        }
        
        // Fallback to current directory
        cwd_path
    }

    fn migrate_from_legacy(content: &str) -> Option<Config> {
        let legacy: LegacyConfig = serde_json::from_str(content).ok()?;
        
        // Extract port from legacy api_url if present
        let api_port = legacy.api_url.as_ref().and_then(|url| {
            // Parse URL like "https://localhost:14443/api/v1"
            if let Some(port_start) = url.find("localhost:") {
                let after_colon = &url[port_start + 10..];
                let port_str: String = after_colon.chars().take_while(|c| c.is_ascii_digit()).collect();
                port_str.parse().ok()
            } else {
                None
            }
        }).unwrap_or(DEFAULT_API_PORT);
        
        Some(Config {
            api_port,
            api_host: default_api_host(),
            api_token: legacy.api_token.unwrap_or_default(),
            local_port: DEFAULT_LOCAL_PORT,
            input_device_name: legacy.input_device_name,
            output_device_name: legacy.output_device_name,
        })
    }

    pub fn load() -> Self {
        let config_path = Self::get_config_path();
        info!("Loading config from: {:?}", config_path);

        if config_path.exists() {
            let content = match fs::read_to_string(&config_path) {
                Ok(c) => c,
                Err(e) => {
                    eprintln!("Failed to read config.json: {}. Using defaults.", e);
                    return Self::default();
                }
            };
            
            // Try parsing as new format first
            if let Ok(config) = serde_json::from_str::<Config>(&content) {
                return config;
            }
            
            // Try migrating from legacy format
            if let Some(migrated) = Self::migrate_from_legacy(&content) {
                info!("Migrated config from legacy format");
                // Save the migrated config
                if let Err(e) = migrated.save_to(&config_path) {
                    eprintln!("Failed to save migrated config: {}", e);
                }
                return migrated;
            }
            
            eprintln!("Failed to parse config.json. Using defaults.");
            Self::default()
        } else {
            info!("config.json not found. Creating default config at {:?}", config_path);
            let default_config = Self::default();
            
            // Ensure parent directory exists
            if let Some(parent) = config_path.parent() {
                let _ = fs::create_dir_all(parent);
            }
            
            if let Err(e) = default_config.save_to(&config_path) {
                eprintln!("Failed to write default config.json: {}", e);
            }
            
            default_config
        }
    }

    fn save_to(&self, path: &PathBuf) -> Result<(), std::io::Error> {
        let json = serde_json::to_string_pretty(self)?;
        fs::write(path, json)?;
        Ok(())
    }

    pub fn save(&self) -> Result<(), std::io::Error> {
        let config_path = Self::get_config_path();
        self.save_to(&config_path)
    }
}

impl Default for Config {
    fn default() -> Self {
        Config {
            api_port: DEFAULT_API_PORT,
            api_host: default_api_host(),
            api_token: String::new(),
            local_port: DEFAULT_LOCAL_PORT,
            input_device_name: None,
            output_device_name: None,
        }
    }
}
