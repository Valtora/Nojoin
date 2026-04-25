use directories::{BaseDirs, ProjectDirs};
use log::{info, warn};
use reqwest::Url;
use serde::{Deserialize, Serialize};
use serde_json::Value;
use std::fs;
use std::path::{Path, PathBuf};

const CURRENT_CONFIG_VERSION: u32 = 2;
const DEFAULT_API_PORT: u16 = 14443;
const DEFAULT_LOCAL_PORT: u16 = 12345;

fn default_config_version() -> u32 {
    CURRENT_CONFIG_VERSION
}

fn default_api_protocol() -> String {
    "https".to_string()
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

fn is_standard_port(protocol: &str, port: u16) -> bool {
    (protocol.eq_ignore_ascii_case("https") && port == 443)
        || (protocol.eq_ignore_ascii_case("http") && port == 80)
}

fn format_host_for_url(host: &str) -> String {
    if host.contains(':') && !host.starts_with('[') && !host.ends_with(']') {
        format!("[{}]", host)
    } else {
        host.to_string()
    }
}

fn build_origin(protocol: &str, host: &str, port: u16) -> String {
    let formatted_host = format_host_for_url(host);
    if is_standard_port(protocol, port) {
        format!("{}://{}", protocol, formatted_host)
    } else {
        format!("{}://{}:{}", protocol, formatted_host, port)
    }
}

fn build_api_url(protocol: &str, host: &str, port: u16) -> String {
    format!("{}/api/v1", build_origin(protocol, host, port))
}

fn normalize_protocol(protocol: &str) -> String {
    let trimmed = protocol.trim().to_ascii_lowercase();
    if trimmed.is_empty() {
        default_api_protocol()
    } else {
        trimmed
    }
}

fn normalize_host(host: &str) -> String {
    let trimmed = host.trim();
    if trimmed.is_empty() {
        default_api_host()
    } else {
        trimmed.to_string()
    }
}

fn normalize_optional_string(value: Option<String>) -> Option<String> {
    value.and_then(|value| {
        let trimmed = value.trim();
        if trimmed.is_empty() {
            None
        } else {
            Some(trimmed.to_string())
        }
    })
}

fn canonicalize_origin(value: &str) -> Option<String> {
    let url = Url::parse(value).ok()?;
    let protocol = normalize_protocol(url.scheme());
    let host = url.host_str()?.to_string();
    let port = url.port_or_known_default()? as u16;
    Some(build_origin(&protocol, &host, port))
}

fn optional_string_field(object: &serde_json::Map<String, Value>, key: &str) -> Option<String> {
    object
        .get(key)
        .and_then(Value::as_str)
        .map(|value| value.to_string())
}

fn optional_u16_field(object: &serde_json::Map<String, Value>, key: &str) -> Option<u16> {
    object
        .get(key)
        .and_then(Value::as_u64)
        .and_then(|value| value.try_into().ok())
}

fn optional_u32_field(object: &serde_json::Map<String, Value>, key: &str) -> Option<u32> {
    object
        .get(key)
        .and_then(Value::as_u64)
        .and_then(|value| value.try_into().ok())
}

fn optional_bool_field(object: &serde_json::Map<String, Value>, key: &str) -> Option<bool> {
    object.get(key).and_then(Value::as_bool)
}

#[derive(Serialize, Deserialize, Clone, Debug, PartialEq, Eq)]
pub struct Config {
    #[serde(default = "default_config_version")]
    pub version: u32,
    #[serde(default)]
    pub machine_local: MachineLocalSettings,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub backend: Option<BackendConnection>,
}

#[derive(Serialize, Deserialize, Clone, Debug, PartialEq, Eq)]
pub struct MachineLocalSettings {
    #[serde(default = "default_local_port")]
    pub local_port: u16,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub input_device_name: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub output_device_name: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub last_version: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub min_meeting_length: Option<u32>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub run_on_startup: Option<bool>,
}

#[derive(Serialize, Deserialize, Clone, Debug, PartialEq, Eq)]
pub struct BackendConnection {
    #[serde(default = "default_api_protocol")]
    pub api_protocol: String,
    #[serde(default = "default_api_host")]
    pub api_host: String,
    #[serde(default = "default_api_port")]
    pub api_port: u16,
    #[serde(default)]
    pub api_token: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub tls_fingerprint: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub paired_web_origin: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub local_control_secret: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub backend_pairing_id: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub local_control_secret_version: Option<u32>,
}

#[derive(Clone, Debug, Default)]
pub struct MachineLocalUpdate {
    pub local_port: Option<u16>,
    pub input_device_name: Option<Option<String>>,
    pub output_device_name: Option<Option<String>>,
    pub last_version: Option<Option<String>>,
    pub min_meeting_length: Option<Option<u32>>,
    pub run_on_startup: Option<Option<bool>>,
}

impl Default for MachineLocalSettings {
    fn default() -> Self {
        Self {
            local_port: DEFAULT_LOCAL_PORT,
            input_device_name: None,
            output_device_name: None,
            last_version: None,
            min_meeting_length: None,
            run_on_startup: None,
        }
    }
}

impl BackendConnection {
    pub fn normalized(mut self) -> Self {
        self.api_protocol = normalize_protocol(&self.api_protocol);
        self.api_host = normalize_host(&self.api_host);
        self.api_port = if self.api_port == 0 {
            DEFAULT_API_PORT
        } else {
            self.api_port
        };
        self.api_token = self.api_token.trim().to_string();
        self.tls_fingerprint = normalize_optional_string(self.tls_fingerprint);
        self.paired_web_origin = self
            .paired_web_origin
            .as_deref()
            .and_then(canonicalize_origin);
        self.local_control_secret = normalize_optional_string(self.local_control_secret);
        self.backend_pairing_id = normalize_optional_string(self.backend_pairing_id);
        self.local_control_secret_version = self
            .local_control_secret_version
            .filter(|value| *value > 0);
        self
    }

    pub fn api_url(&self) -> String {
        build_api_url(&self.api_protocol, &self.api_host, self.api_port)
    }

    pub fn derived_web_origin(&self) -> String {
        build_origin(&self.api_protocol, &self.api_host, self.api_port)
    }

    pub fn has_complete_pairing_state(&self) -> bool {
        !self.api_token.is_empty()
            && self
                .tls_fingerprint
                .as_deref()
                .map(str::trim)
                .filter(|value| !value.is_empty())
                .is_some()
            && self.paired_web_origin.is_some()
            && self
                .local_control_secret
                .as_deref()
                .map(str::trim)
                .filter(|value| !value.is_empty())
                .is_some()
            && self
                .backend_pairing_id
                .as_deref()
                .map(str::trim)
                .filter(|value| !value.is_empty())
                .is_some()
            && self.local_control_secret_version.unwrap_or_default() > 0
    }
}

impl Default for BackendConnection {
    fn default() -> Self {
        Self {
            api_protocol: default_api_protocol(),
            api_host: default_api_host(),
            api_port: DEFAULT_API_PORT,
            api_token: String::new(),
            tls_fingerprint: None,
            paired_web_origin: None,
            local_control_secret: None,
            backend_pairing_id: None,
            local_control_secret_version: None,
        }
    }
}

#[derive(Deserialize)]
struct VersionedConfigRoot {
    version: Option<u32>,
    #[serde(default)]
    machine_local: Option<MachineLocalSettings>,
    #[serde(default)]
    backend: Option<Value>,
}

impl Config {
    pub fn get_api_url(&self) -> String {
        self.backend_or_default().api_url()
    }

    pub fn api_protocol(&self) -> String {
        self.backend_or_default().api_protocol
    }

    pub fn api_host(&self) -> String {
        self.backend_or_default().api_host
    }

    pub fn api_port(&self) -> u16 {
        self.backend_or_default().api_port
    }

    pub fn api_token(&self) -> String {
        self.backend_or_default().api_token
    }

    pub fn tls_fingerprint(&self) -> Option<String> {
        self.backend
            .as_ref()
            .and_then(|backend| backend.tls_fingerprint.clone())
    }

    pub fn paired_web_origin(&self) -> Option<String> {
        self.backend
            .as_ref()
            .and_then(|backend| backend.paired_web_origin.clone())
    }

    pub fn get_web_url(&self) -> String {
        self.paired_web_origin()
            .unwrap_or_else(|| self.backend_or_default().derived_web_origin())
    }

    pub fn is_authenticated(&self) -> bool {
        self.backend
            .as_ref()
            .map(BackendConnection::has_complete_pairing_state)
            .unwrap_or(false)
    }

    #[allow(dead_code)]
    pub fn is_paired(&self) -> bool {
        self.is_authenticated() && self.paired_web_origin().is_some()
    }

    pub fn backend_or_default(&self) -> BackendConnection {
        self.backend.clone().unwrap_or_default()
    }

    pub fn backend_connection(&self) -> Option<BackendConnection> {
        self.backend.clone()
    }

    pub fn local_port(&self) -> u16 {
        self.machine_local.local_port
    }

    pub fn input_device_name(&self) -> Option<&str> {
        self.machine_local.input_device_name.as_deref()
    }

    pub fn output_device_name(&self) -> Option<&str> {
        self.machine_local.output_device_name.as_deref()
    }

    pub fn min_meeting_length(&self) -> Option<u32> {
        self.machine_local.min_meeting_length
    }

    pub fn run_on_startup(&self) -> Option<bool> {
        self.machine_local.run_on_startup
    }

    pub fn last_version(&self) -> Option<&str> {
        self.machine_local.last_version.as_deref()
    }

    pub fn replace_backend(&mut self, backend: BackendConnection) {
        self.version = CURRENT_CONFIG_VERSION;
        self.backend = Some(backend.normalized());
    }

    pub fn clear_backend(&mut self) {
        self.version = CURRENT_CONFIG_VERSION;
        self.backend = None;
    }

    pub fn replace_backend_and_save(
        &mut self,
        backend: BackendConnection,
    ) -> Result<(), std::io::Error> {
        let config_path = Self::get_config_path();
        self.replace_backend_and_save_to(backend, &config_path)
    }

    fn replace_backend_and_save_to(
        &mut self,
        backend: BackendConnection,
        path: &Path,
    ) -> Result<(), std::io::Error> {
        let mut updated = self.clone();
        updated.replace_backend(backend);
        self.save_updated_to(updated, path)
    }

    pub fn clear_backend_and_save(&mut self) -> Result<(), std::io::Error> {
        let config_path = Self::get_config_path();
        self.clear_backend_and_save_to(&config_path)
    }

    fn clear_backend_and_save_to(&mut self, path: &Path) -> Result<(), std::io::Error> {
        let mut updated = self.clone();
        updated.clear_backend();
        self.save_updated_to(updated, path)
    }

    pub fn apply_machine_local_update(&mut self, update: MachineLocalUpdate) {
        self.version = CURRENT_CONFIG_VERSION;

        if let Some(local_port) = update.local_port {
            self.machine_local.local_port = local_port;
        }
        if let Some(input_device_name) = update.input_device_name {
            self.machine_local.input_device_name = normalize_optional_string(input_device_name);
        }
        if let Some(output_device_name) = update.output_device_name {
            self.machine_local.output_device_name = normalize_optional_string(output_device_name);
        }
        if let Some(last_version) = update.last_version {
            self.machine_local.last_version = normalize_optional_string(last_version);
        }
        if let Some(min_meeting_length) = update.min_meeting_length {
            self.machine_local.min_meeting_length = min_meeting_length;
        }
        if let Some(run_on_startup) = update.run_on_startup {
            self.machine_local.run_on_startup = run_on_startup;
        }
    }

    pub fn update_machine_local_and_save(
        &mut self,
        update: MachineLocalUpdate,
    ) -> Result<(), std::io::Error> {
        let config_path = Self::get_config_path();
        self.update_machine_local_and_save_to(update, &config_path)
    }

    fn update_machine_local_and_save_to(
        &mut self,
        update: MachineLocalUpdate,
        path: &Path,
    ) -> Result<(), std::io::Error> {
        let mut updated = self.clone();
        updated.apply_machine_local_update(update);
        self.save_updated_to(updated, path)
    }

    fn save_updated_to(&mut self, updated: Config, path: &Path) -> Result<(), std::io::Error> {
        updated.save_to(path)?;
        *self = updated;
        Ok(())
    }

    pub fn get_app_data_dir() -> PathBuf {
        if cfg!(target_os = "windows") {
            if let Some(base_dirs) = BaseDirs::new() {
                return base_dirs.config_dir().join("Nojoin Companion");
            }
        } else if let Some(proj_dirs) = ProjectDirs::from("com", "Valtora", "Nojoin-Companion") {
            return proj_dirs.config_dir().to_path_buf();
        }

        PathBuf::from(".")
    }

    fn get_config_path() -> PathBuf {
        let config_name = "config.json";

        let cwd_path = PathBuf::from(config_name);
        if cwd_path.exists() {
            info!("Found config in current directory (Dev Override)");
            return cwd_path;
        }

        if let Ok(exe_path) = std::env::current_exe() {
            if let Some(exe_dir) = exe_path.parent() {
                let exe_config = exe_dir.join(config_name);
                if exe_config.exists() {
                    info!("Found config in executable directory (Legacy)");
                    return exe_config;
                }
            }
        }

        Self::get_app_data_dir().join(config_name)
    }

    fn recover_versioned_config(content: &str) -> Option<(Config, bool)> {
        let parsed: VersionedConfigRoot = serde_json::from_str(content).ok()?;
        let parsed_version = parsed.version.unwrap_or_default();
        let mut needs_save = parsed_version != CURRENT_CONFIG_VERSION;

        let machine_local = parsed.machine_local.unwrap_or_else(|| {
            needs_save = true;
            MachineLocalSettings::default()
        });

        let mut backend = match parsed.backend {
            Some(Value::Null) | None => None,
            Some(value) => match serde_json::from_value::<BackendConnection>(value) {
                Ok(connection) => {
                    let normalized = connection.clone().normalized();
                    if normalized != connection {
                        needs_save = true;
                    }
                    if normalized.has_complete_pairing_state() {
                        Some(normalized)
                    } else {
                        warn!(
                            "Clearing incomplete backend trust state during recovery. Start pairing again from Companion Settings."
                        );
                        needs_save = true;
                        None
                    }
                }
                Err(error) => {
                    warn!(
                        "Clearing malformed backend config block during recovery: {}",
                        error
                    );
                    needs_save = true;
                    None
                }
            },
        };

        if parsed_version < CURRENT_CONFIG_VERSION {
            info!("Upgrading from legacy config version {}. Dropping backend trust state to force a clean re-pair.", parsed_version);
            backend = None;
            needs_save = true;
        }

        let config = Config {
            version: CURRENT_CONFIG_VERSION,
            machine_local,
            backend,
        };

        Some((config, needs_save))
    }

    fn migrate_from_current_flat(value: &Value) -> Option<Config> {
        let object = value.as_object()?;
        // Drop legacy trust state to force a clean re-pair for upgrading users
        let backend = None;

        Some(Config {
            version: CURRENT_CONFIG_VERSION,
            machine_local: MachineLocalSettings {
                local_port: optional_u16_field(object, "local_port").unwrap_or(DEFAULT_LOCAL_PORT),
                input_device_name: normalize_optional_string(optional_string_field(
                    object,
                    "input_device_name",
                )),
                output_device_name: normalize_optional_string(optional_string_field(
                    object,
                    "output_device_name",
                )),
                last_version: normalize_optional_string(optional_string_field(
                    object,
                    "last_version",
                )),
                min_meeting_length: optional_u32_field(object, "min_meeting_length"),
                run_on_startup: optional_bool_field(object, "run_on_startup"),
            },
            backend,
        })
    }

    fn migrate_from_legacy(value: &Value) -> Option<Config> {
        let object = value.as_object()?;
        
        // Legacy fields are used to populate some machine-local fallback values if needed,
        // but backend trust state is explicitly dropped to force a clean re-pair for upgrading users.
        let backend = None;

        Some(Config {
            version: CURRENT_CONFIG_VERSION,
            machine_local: MachineLocalSettings {
                local_port: optional_u16_field(object, "local_port").unwrap_or(DEFAULT_LOCAL_PORT),
                input_device_name: normalize_optional_string(optional_string_field(
                    object,
                    "input_device_name",
                )),
                output_device_name: normalize_optional_string(optional_string_field(
                    object,
                    "output_device_name",
                )),
                last_version: normalize_optional_string(optional_string_field(
                    object,
                    "last_version",
                )),
                min_meeting_length: optional_u32_field(object, "min_meeting_length"),
                run_on_startup: optional_bool_field(object, "run_on_startup"),
            },
            backend,
        })
    }

    fn parse_or_migrate(content: &str) -> Option<(Config, bool)> {
        let value: Value = serde_json::from_str(content).ok()?;
        let object = value.as_object()?;

        if object.contains_key("version")
            || object.contains_key("machine_local")
            || object.contains_key("backend")
        {
            return Self::recover_versioned_config(content);
        }

        if object.contains_key("api_url") || object.contains_key("web_app_url") {
            return Self::migrate_from_legacy(&value).map(|config| (config, true));
        }

        if object.contains_key("api_protocol")
            || object.contains_key("api_host")
            || object.contains_key("api_port")
            || object.contains_key("api_token")
            || object.contains_key("tls_fingerprint")
            || object.contains_key("local_port")
            || object.contains_key("input_device_name")
            || object.contains_key("output_device_name")
            || object.contains_key("last_version")
            || object.contains_key("min_meeting_length")
            || object.contains_key("run_on_startup")
        {
            return Self::migrate_from_current_flat(&value).map(|config| (config, true));
        }

        None
    }

    pub fn load() -> Self {
        let config_path = Self::get_config_path();
        info!("Loading config from: {:?}", config_path);

        if config_path.exists() {
            let content = match fs::read_to_string(&config_path) {
                Ok(content) => content,
                Err(error) => {
                    eprintln!("Failed to read config.json: {}. Using defaults.", error);
                    return Self::default();
                }
            };

            if let Some((config, needs_save)) = Self::parse_or_migrate(&content) {
                if needs_save {
                    info!("Saving migrated or recovered config to {:?}", config_path);
                    if let Err(error) = config.save_to(&config_path) {
                        eprintln!("Failed to save migrated config: {}", error);
                    }
                }
                return config;
            }

            warn!(
                "Failed to parse config.json. Resetting to defaults to clear unsafe backend state."
            );
            let default_config = Self::default();
            if let Err(error) = default_config.save_to(&config_path) {
                eprintln!("Failed to write recovered default config.json: {}", error);
            }
            return default_config;
        }

        info!(
            "config.json not found. Creating default config at {:?}",
            config_path
        );
        let default_config = Self::default();
        if let Err(error) = default_config.save_to(&config_path) {
            eprintln!("Failed to write default config.json: {}", error);
        }
        default_config
    }

    fn save_to(&self, path: &Path) -> Result<(), std::io::Error> {
        if let Some(parent) = path.parent() {
            if !parent.as_os_str().is_empty() {
                fs::create_dir_all(parent)?;
            }
        }

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
        Self {
            version: CURRENT_CONFIG_VERSION,
            machine_local: MachineLocalSettings::default(),
            backend: None,
        }
    }
}

#[cfg(test)]
mod tests {
    use super::{BackendConnection, Config, MachineLocalUpdate, CURRENT_CONFIG_VERSION};
    use std::fs;
    use std::path::PathBuf;
    use std::time::{SystemTime, UNIX_EPOCH};

    fn temp_config_path(name: &str) -> PathBuf {
        let unique = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_nanos();
        std::env::temp_dir().join(format!("nojoin-config-{name}-{unique}.json"))
    }

    #[test]
    fn new_format_round_trip_preserves_nested_schema() {
        let path = temp_config_path("round-trip");
        let mut config = Config::default();
        config.apply_machine_local_update(MachineLocalUpdate {
            local_port: Some(23456),
            input_device_name: Some(Some("Mic".to_string())),
            output_device_name: Some(Some("Speakers".to_string())),
            last_version: Some(Some("1.2.3".to_string())),
            min_meeting_length: Some(Some(12)),
            run_on_startup: Some(Some(true)),
        });
        config.replace_backend(BackendConnection {
            api_protocol: "https".to_string(),
            api_host: "prod.example.com".to_string(),
            api_port: 443,
            api_token: "bootstrap-token".to_string(),
            tls_fingerprint: Some("AA:BB".to_string()),
            paired_web_origin: Some("https://app.example.com/workspace".to_string()),
            local_control_secret: Some("reserved-secret".to_string()),
            backend_pairing_id: Some("pairing-one".to_string()),
            local_control_secret_version: Some(1),
        });

        config.save_to(&path).unwrap();
        let content = fs::read_to_string(&path).unwrap();
        let (loaded, needs_save) = Config::parse_or_migrate(&content).unwrap();

        assert!(!needs_save);
        assert_eq!(loaded.version, CURRENT_CONFIG_VERSION);
        assert_eq!(loaded, config);
        assert_eq!(
            loaded.paired_web_origin().as_deref(),
            Some("https://app.example.com")
        );

        let _ = fs::remove_file(path);
    }

    #[test]
    fn legacy_config_migrates_into_versioned_root() {
        let content = r#"{
            "api_url": "https://legacy.example.com:15443/api/v1",
            "api_token": "legacy-token",
            "web_app_url": "https://web.legacy.example.com/dashboard",
            "input_device_name": "Legacy Mic",
            "output_device_name": "Legacy Speakers"
        }"#;

        let (config, needs_save) = Config::parse_or_migrate(content).unwrap();

        assert!(needs_save);
        assert_eq!(config.version, CURRENT_CONFIG_VERSION);
        assert_eq!(
            config.machine_local.input_device_name.as_deref(),
            Some("Legacy Mic")
        );
        assert_eq!(
            config.machine_local.output_device_name.as_deref(),
            Some("Legacy Speakers")
        );
        // The backend trust block is explicitly dropped during migration now
        assert!(config.backend.is_none());
    }

    #[test]
    fn current_flat_config_migrates_and_preserves_machine_local_fields() {
        let content = r#"{
            "api_protocol": "https",
            "api_host": "flat.example.com",
            "api_port": 14443,
            "api_token": "flat-token",
            "tls_fingerprint": "AA:BB:CC",
            "local_port": 45678,
            "input_device_name": "Desk Mic",
            "output_device_name": "Desk Speakers",
            "last_version": "0.8.1",
            "min_meeting_length": 8,
            "run_on_startup": true
        }"#;

        let (config, needs_save) = Config::parse_or_migrate(content).unwrap();

        assert!(needs_save);
        assert_eq!(config.version, CURRENT_CONFIG_VERSION);
        assert_eq!(config.local_port(), 45678);
        assert_eq!(config.input_device_name(), Some("Desk Mic"));
        assert_eq!(config.output_device_name(), Some("Desk Speakers"));
        assert_eq!(config.last_version(), Some("0.8.1"));
        assert_eq!(config.min_meeting_length(), Some(8));
        assert_eq!(config.run_on_startup(), Some(true));
        
        // The backend trust block is explicitly dropped during migration now
        assert!(config.backend.is_none());
    }

    #[test]
    fn malformed_versioned_backend_is_cleared_without_losing_machine_local_settings() {
        let content = r#"{
            "version": 1,
            "machine_local": {
                "local_port": 22000,
                "input_device_name": "USB Mic",
                "min_meeting_length": 15
            },
            "backend": {
                "api_protocol": "https",
                "api_host": 123,
                "api_port": 14443,
                "api_token": "bad"
            }
        }"#;

        let (config, needs_save) = Config::parse_or_migrate(content).unwrap();

        assert!(needs_save);
        assert_eq!(config.local_port(), 22000);
        assert_eq!(config.input_device_name(), Some("USB Mic"));
        assert_eq!(config.min_meeting_length(), Some(15));
        assert!(config.backend.is_none());
        assert!(!config.is_authenticated());
    }

    #[test]
    fn malformed_non_json_config_falls_back_to_defaults() {
        assert!(Config::parse_or_migrate("not valid json").is_none());
    }

    #[test]
    fn replace_backend_swaps_backend_block_atomically() {
        let mut config = Config::default();
        config.replace_backend(BackendConnection {
            api_protocol: "https".to_string(),
            api_host: "old.example.com".to_string(),
            api_port: 443,
            api_token: "old-token".to_string(),
            tls_fingerprint: Some("OLD".to_string()),
            paired_web_origin: Some("https://old.example.com".to_string()),
            local_control_secret: Some("old-secret".to_string()),
            backend_pairing_id: Some("pairing-old".to_string()),
            local_control_secret_version: Some(1),
        });

        let before = config.backend.clone().unwrap();
        let replacement = BackendConnection {
            api_protocol: "https".to_string(),
            api_host: "new.example.com".to_string(),
            api_port: 8443,
            api_token: "new-token".to_string(),
            tls_fingerprint: Some("NEW".to_string()),
            paired_web_origin: Some("https://new.example.com:8443/app".to_string()),
            local_control_secret: None,
            backend_pairing_id: None,
            local_control_secret_version: None,
        };

        config.replace_backend(replacement);

        assert_ne!(config.backend.as_ref().unwrap(), &before);
        assert_eq!(config.api_host(), "new.example.com");
        assert_eq!(config.api_port(), 8443);
        assert_eq!(config.api_token(), "new-token");
        assert_eq!(config.tls_fingerprint().as_deref(), Some("NEW"));
        assert_eq!(
            config.paired_web_origin().as_deref(),
            Some("https://new.example.com:8443")
        );
        assert_eq!(config.backend.as_ref().unwrap().local_control_secret, None);
    }

    #[test]
    fn failed_backend_save_keeps_existing_backend_state_on_disk_and_in_memory() {
        let path = temp_config_path("atomic-save-failure");
        let mut config = Config::default();
        config.replace_backend(BackendConnection {
            api_protocol: "https".to_string(),
            api_host: "stable.example.com".to_string(),
            api_port: 443,
            api_token: "stable-token".to_string(),
            tls_fingerprint: Some("STABLE".to_string()),
            paired_web_origin: Some("https://stable.example.com".to_string()),
            local_control_secret: None,
            backend_pairing_id: None,
            local_control_secret_version: None,
        });
        config.save_to(&path).unwrap();

        let original_config = config.clone();
        let original_disk = fs::read_to_string(&path).unwrap();

        let mut permissions = fs::metadata(&path).unwrap().permissions();
        permissions.set_readonly(true);
        fs::set_permissions(&path, permissions).unwrap();

        let result = config.replace_backend_and_save_to(
            BackendConnection {
                api_protocol: "https".to_string(),
                api_host: "replacement.example.com".to_string(),
                api_port: 8443,
                api_token: "replacement-token".to_string(),
                tls_fingerprint: Some("REPLACEMENT".to_string()),
                paired_web_origin: Some("https://replacement.example.com:8443".to_string()),
                local_control_secret: Some("future-secret".to_string()),
                backend_pairing_id: Some("pairing-replacement".to_string()),
                local_control_secret_version: Some(2),
            },
            &path,
        );

        assert!(result.is_err());
        assert_eq!(config, original_config);
        assert_eq!(fs::read_to_string(&path).unwrap(), original_disk);

        let mut permissions = fs::metadata(&path).unwrap().permissions();
        permissions.set_readonly(false);
        fs::set_permissions(&path, permissions).unwrap();
        let _ = fs::remove_file(path);
    }

    #[test]
    fn machine_local_settings_survive_backend_replacement() {
        let mut config = Config::default();
        config.apply_machine_local_update(MachineLocalUpdate {
            local_port: Some(31000),
            input_device_name: Some(Some("Mic One".to_string())),
            output_device_name: Some(Some("Speaker One".to_string())),
            last_version: Some(Some("0.8.2".to_string())),
            min_meeting_length: Some(Some(25)),
            run_on_startup: Some(Some(false)),
        });
        let machine_before = config.machine_local.clone();

        config.replace_backend(BackendConnection {
            api_protocol: "https".to_string(),
            api_host: "first.example.com".to_string(),
            api_port: 443,
            api_token: "first-token".to_string(),
            tls_fingerprint: Some("FIRST".to_string()),
            paired_web_origin: Some("https://first.example.com".to_string()),
            local_control_secret: None,
            backend_pairing_id: None,
            local_control_secret_version: None,
        });

        config.replace_backend(BackendConnection {
            api_protocol: "https".to_string(),
            api_host: "second.example.com".to_string(),
            api_port: 443,
            api_token: "second-token".to_string(),
            tls_fingerprint: Some("SECOND".to_string()),
            paired_web_origin: Some("https://second.example.com".to_string()),
            local_control_secret: None,
            backend_pairing_id: None,
            local_control_secret_version: None,
        });

        assert_eq!(config.machine_local, machine_before);
        assert_eq!(config.api_host(), "second.example.com");
        assert_eq!(config.api_token(), "second-token");
    }

    #[test]
    fn clear_backend_save_drops_backend_trust_block() {
        let path = temp_config_path("clear-backend");
        let mut config = Config::default();
        config.replace_backend(BackendConnection {
            api_protocol: "https".to_string(),
            api_host: "paired.example.com".to_string(),
            api_port: 443,
            api_token: "bootstrap-token".to_string(),
            tls_fingerprint: Some("AA:BB:CC".to_string()),
            paired_web_origin: Some("https://paired.example.com".to_string()),
            local_control_secret: Some("local-control-secret".to_string()),
            backend_pairing_id: Some("pairing-123".to_string()),
            local_control_secret_version: Some(3),
        });
        config.save_to(&path).unwrap();

        config.clear_backend_and_save_to(&path).unwrap();

        let disk = fs::read_to_string(&path).unwrap();
        let (loaded, needs_save) = Config::parse_or_migrate(&disk).unwrap();

        assert!(!needs_save);
        assert!(loaded.backend.is_none());
        assert!(!loaded.is_authenticated());

        let _ = fs::remove_file(path);
    }

    #[test]
    fn current_version_backend_without_fingerprint_is_cleared_on_recovery() {
        let content = r#"{
            "version": 2,
            "machine_local": {
                "local_port": 12345
            },
            "backend": {
                "api_protocol": "https",
                "api_host": "paired.example.com",
                "api_port": 443,
                "api_token": "bootstrap-token",
                "paired_web_origin": "https://paired.example.com",
                "local_control_secret": "local-control-secret",
                "backend_pairing_id": "pairing-123",
                "local_control_secret_version": 3
            }
        }"#;

        let (config, needs_save) = Config::parse_or_migrate(content).unwrap();

        assert!(needs_save);
        assert!(config.backend.is_none());
        assert!(!config.is_authenticated());
    }
}
