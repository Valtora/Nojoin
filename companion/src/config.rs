use serde::Deserialize;
use std::fs;
use std::path::Path;

#[derive(Deserialize, Clone, Debug)]
pub struct Config {
    pub api_url: String,
    pub api_token: String,
}

impl Config {
    pub fn load() -> Self {
        let config_path = "config.json";
        if Path::new(config_path).exists() {
            let content = fs::read_to_string(config_path).expect("Failed to read config.json");
            serde_json::from_str(&content).expect("Failed to parse config.json")
        } else {
            println!("Warning: config.json not found, using defaults");
            Config {
                api_url: "http://localhost:8000/api/v1".to_string(),
                api_token: "".to_string(),
            }
        }
    }
}
