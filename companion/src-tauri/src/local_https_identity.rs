use crate::config::Config;
use crate::secret_store::{
    protect_bytes_with_description, replace_file, temp_write_path, unprotect_bytes,
};
use base64::{engine::general_purpose::STANDARD as BASE64_STANDARD, Engine as _};
#[cfg(windows)]
use log::{error, info, warn};
use rcgen::{
    BasicConstraints, CertificateParams, CertificateRevocationListParams, CrlDistributionPoint,
    DistinguishedName, DnType, ExtendedKeyUsagePurpose, IsCa, Issuer, KeyIdMethod, KeyPair,
    KeyUsagePurpose, PublicKeyData, SerialNumber, SigningKey,
};
use reqwest::Url;
use rustls::pki_types::{CertificateDer, PrivateKeyDer, PrivatePkcs8KeyDer};
use serde::{Deserialize, Deserializer, Serialize, Serializer};
use sha2::{Digest, Sha256};
use std::collections::BTreeSet;
use std::fs;
use std::net::{IpAddr, Ipv4Addr, Ipv6Addr};
use std::path::{Path, PathBuf};
use time::{Duration, OffsetDateTime};
use x509_parser::extensions::{DistributionPointName, GeneralName, ParsedExtension};
use x509_parser::prelude::{FromDer, X509Certificate};

const LOCAL_HTTPS_DIR: &str = "local_https";
const PUBLIC_IDENTITY_FILE: &str = "identity.json";
const PRIVATE_MATERIAL_FILE: &str = "identity.private.bin";
const REVOCATION_LIST_FILE: &str = "ca.crl";
#[cfg(windows)]
const FIREFOX_SUPPORT_INSTALL_DIR: &str = "firefox_machine_root_support";
#[cfg(windows)]
const FIREFOX_SUPPORT_INSTALL_LOG_FILE: &str = "install-firefox-machine-root.log";
#[cfg(windows)]
const FIREFOX_SUPPORT_LAUNCHER_FILE: &str = "launch-firefox-machine-root.ps1";
const LOCAL_HTTPS_DPAPI_DESCRIPTION: &str = "Nojoin Companion Local HTTPS Identity";
const LOCAL_HTTPS_SCHEMA_VERSION: u32 = 1;
const CA_COMMON_NAME: &str = "Nojoin Companion Local HTTPS CA";
const LEAF_COMMON_NAME: &str = "Nojoin Companion Local HTTPS";

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct LocalHttpsPaths {
    pub root_dir: PathBuf,
    pub public_metadata_path: PathBuf,
    pub encrypted_private_material_path: PathBuf,
    pub revocation_list_path: PathBuf,
}

impl LocalHttpsPaths {
    #[allow(dead_code)]
    pub fn current() -> Self {
        Self::from_app_data_dir(Config::get_app_data_dir())
    }

    pub fn from_app_data_dir(app_data_dir: impl AsRef<Path>) -> Self {
        let root_dir = app_data_dir.as_ref().join(LOCAL_HTTPS_DIR);

        Self {
            public_metadata_path: root_dir.join(PUBLIC_IDENTITY_FILE),
            encrypted_private_material_path: root_dir.join(PRIVATE_MATERIAL_FILE),
            revocation_list_path: root_dir.join(REVOCATION_LIST_FILE),
            root_dir,
        }
    }
}

#[derive(Clone, Debug, Serialize, Deserialize, PartialEq, Eq)]
pub struct PersistedLocalHttpsIdentity {
    pub schema_version: u32,
    pub ca: PersistedCertificateRecord,
    pub leaf: Option<PersistedCertificateRecord>,
}

#[derive(Clone, Debug, Serialize, Deserialize, PartialEq, Eq)]
pub struct PersistedCertificateRecord {
    #[serde(with = "base64_bytes")]
    pub certificate_der: Vec<u8>,
    pub sha256_fingerprint: String,
    pub public_key_spki_sha256: String,
    pub not_before_unix: i64,
    pub not_after_unix: i64,
}

#[allow(dead_code)]
#[derive(Debug)]
pub struct LocalHttpsServerIdentity {
    pub certificate_chain: Vec<CertificateDer<'static>>,
    pub private_key: PrivateKeyDer<'static>,
}

#[allow(dead_code)]
#[derive(Debug)]
pub struct LocalHttpsReadyIdentity {
    pub persisted_identity: PersistedLocalHttpsIdentity,
    pub server_identity: LocalHttpsServerIdentity,
}

#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct LocalHttpsReconcileChanges {
    pub bootstrapped_identity: bool,
    pub leaf_regenerated: bool,
    pub trust_installed: bool,
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub enum LocalHttpsRepairReason {
    InvalidCaMaterial,
    UnsupportedSchema,
    TrustStoreFailure,
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct LocalHttpsRepairRequired {
    pub reason: LocalHttpsRepairReason,
    pub message: String,
}

#[allow(dead_code)]
#[derive(Debug)]
pub enum LocalHttpsReconcileState {
    Ready(LocalHttpsReadyIdentity),
    RepairRequired(LocalHttpsRepairRequired),
}

#[allow(dead_code)]
#[derive(Debug)]
pub struct LocalHttpsReconcileResult {
    pub state: LocalHttpsReconcileState,
    pub changes: LocalHttpsReconcileChanges,
}

impl LocalHttpsReconcileResult {
    fn ready(ready_identity: LocalHttpsReadyIdentity, changes: LocalHttpsReconcileChanges) -> Self {
        Self {
            state: LocalHttpsReconcileState::Ready(ready_identity),
            changes,
        }
    }

    fn repair(
        reason: LocalHttpsRepairReason,
        message: impl Into<String>,
        changes: LocalHttpsReconcileChanges,
    ) -> Self {
        Self {
            state: LocalHttpsReconcileState::RepairRequired(LocalHttpsRepairRequired {
                reason,
                message: message.into(),
            }),
            changes,
        }
    }
}

pub trait LocalCaTrustStore {
    fn is_ca_trusted(&self, ca_certificate_der: &[u8]) -> Result<bool, String>;

    fn install_ca(&self, ca_certificate_der: &[u8]) -> Result<(), String>;

    fn install_crl(&self, crl_der: &[u8]) -> Result<(), String>;

    fn ensure_ca_trusted(&self, ca_certificate_der: &[u8]) -> Result<bool, String> {
        if self.is_ca_trusted(ca_certificate_der)? {
            return Ok(false);
        }

        self.install_ca(ca_certificate_der)?;

        if !self.is_ca_trusted(ca_certificate_der)? {
            return Err(
                "The CA was installed into the trust store but could not be verified afterwards."
                    .to_string(),
            );
        }

        Ok(true)
    }
}

#[allow(dead_code)]
pub struct SystemLocalCaTrustStore;

#[allow(dead_code)]
pub fn ensure_local_https_identity() -> Result<LocalHttpsReconcileResult, String> {
    let paths = LocalHttpsPaths::current();
    ensure_local_https_identity_with(&paths, &SystemLocalCaTrustStore, OffsetDateTime::now_utc())
}

pub fn ensure_local_https_identity_with<T: LocalCaTrustStore>(
    paths: &LocalHttpsPaths,
    trust_store: &T,
    now: OffsetDateTime,
) -> Result<LocalHttpsReconcileResult, String> {
    let stored_identity = load_stored_identity(paths);

    match stored_identity {
        StoredIdentityState::Missing => {
            let generated = generate_full_identity(paths, now)?;
            write_stored_identity(
                paths,
                &generated.public_identity,
                &generated.private_material,
            )?;
            let revocation_list_der = write_local_revocation_list(
                paths,
                &generated.public_identity.ca,
                &generated.private_material.ca,
                now,
            )?;

            let trust_installed = match trust_store
                .ensure_ca_trusted(&generated.public_identity.ca.certificate_der)
            {
                Ok(value) => value,
                Err(error) => {
                    return Ok(LocalHttpsReconcileResult::repair(
                        LocalHttpsRepairReason::TrustStoreFailure,
                        format!(
                            "Created a new local HTTPS identity, but could not install the CA in the current-user trust store: {}",
                            error
                        ),
                        LocalHttpsReconcileChanges {
                            bootstrapped_identity: true,
                            leaf_regenerated: false,
                            trust_installed: false,
                        },
                    ));
                }
            };

            if let Err(error) = trust_store.install_crl(&revocation_list_der) {
                return Ok(LocalHttpsReconcileResult::repair(
                    LocalHttpsRepairReason::TrustStoreFailure,
                    format!(
                        "Created a new local HTTPS identity, but could not publish the local certificate revocation list into the current-user CA store: {}",
                        error
                    ),
                    LocalHttpsReconcileChanges {
                        bootstrapped_identity: true,
                        leaf_regenerated: false,
                        trust_installed,
                    },
                ));
            }

            let ready_identity = LocalHttpsReadyIdentity {
                server_identity: build_server_identity(
                    &generated.public_identity.ca.certificate_der,
                    generated
                        .public_identity
                        .leaf
                        .as_ref()
                        .expect("leaf present after bootstrap"),
                    generated
                        .private_material
                        .leaf
                        .as_ref()
                        .expect("leaf private key present after bootstrap"),
                ),
                persisted_identity: generated.public_identity,
            };

            return Ok(LocalHttpsReconcileResult::ready(
                ready_identity,
                LocalHttpsReconcileChanges {
                    bootstrapped_identity: true,
                    leaf_regenerated: false,
                    trust_installed,
                },
            ));
        }
        StoredIdentityState::Partial => {
            return Ok(LocalHttpsReconcileResult::repair(
                LocalHttpsRepairReason::InvalidCaMaterial,
                "The local HTTPS identity is incomplete on disk and must be repaired before it can be used.",
                LocalHttpsReconcileChanges::default(),
            ));
        }
        StoredIdentityState::Malformed(message) => {
            return Ok(LocalHttpsReconcileResult::repair(
                LocalHttpsRepairReason::InvalidCaMaterial,
                message,
                LocalHttpsReconcileChanges::default(),
            ));
        }
        StoredIdentityState::Present(mut public_identity, mut private_material) => {
            if public_identity.schema_version != LOCAL_HTTPS_SCHEMA_VERSION
                || private_material.schema_version != LOCAL_HTTPS_SCHEMA_VERSION
            {
                return Ok(LocalHttpsReconcileResult::repair(
                    LocalHttpsRepairReason::UnsupportedSchema,
                    format!(
                        "Unsupported local HTTPS identity schema version. Expected {}, found public={} and private={}.",
                        LOCAL_HTTPS_SCHEMA_VERSION,
                        public_identity.schema_version,
                        private_material.schema_version
                    ),
                    LocalHttpsReconcileChanges::default(),
                ));
            }

            let ca_material =
                match validate_ca_material(&public_identity.ca, &private_material.ca, now) {
                    Ok(material) => material,
                    Err(repair_required) => {
                        return Ok(LocalHttpsReconcileResult::repair(
                            repair_required.reason,
                            repair_required.message,
                            LocalHttpsReconcileChanges::default(),
                        ));
                    }
                };

            let trust_installed = match trust_store
                .ensure_ca_trusted(&public_identity.ca.certificate_der)
            {
                Ok(value) => value,
                Err(error) => {
                    return Ok(LocalHttpsReconcileResult::repair(
                        LocalHttpsRepairReason::TrustStoreFailure,
                        format!(
                            "The local HTTPS CA is valid, but the current-user trust store could not be updated: {}",
                            error
                        ),
                        LocalHttpsReconcileChanges::default(),
                    ));
                }
            };

            let mut changes = LocalHttpsReconcileChanges {
                bootstrapped_identity: false,
                leaf_regenerated: false,
                trust_installed,
            };

            let revocation_list_der =
                write_local_revocation_list(paths, &public_identity.ca, &private_material.ca, now)?;
            if let Err(error) = trust_store.install_crl(&revocation_list_der) {
                return Ok(LocalHttpsReconcileResult::repair(
                    LocalHttpsRepairReason::TrustStoreFailure,
                    format!(
                        "The local HTTPS CA is valid, but the current-user CA store could not be updated with the local certificate revocation list: {}",
                        error
                    ),
                    changes,
                ));
            }

            let validated_leaf = match validate_leaf_material(
                paths,
                public_identity.leaf.as_ref(),
                private_material.leaf.as_ref(),
            ) {
                Ok(Some(material)) if !should_renew_leaf(material.not_after, now) => material,
                Ok(_) => {
                    let regenerated_leaf = generate_leaf_from_existing_ca(
                        paths,
                        &public_identity.ca,
                        &private_material.ca,
                        now,
                    )?;
                    public_identity.leaf = Some(regenerated_leaf.to_certificate_record());
                    private_material.leaf = Some(regenerated_leaf.to_private_key_record());
                    write_stored_identity(paths, &public_identity, &private_material)?;
                    changes.leaf_regenerated = true;
                    validated_leaf_from_records(
                        public_identity
                            .leaf
                            .as_ref()
                            .expect("leaf record present after regeneration"),
                        private_material
                            .leaf
                            .as_ref()
                            .expect("leaf key present after regeneration"),
                    )
                }
                Err(error) => return Err(error),
            };

            let ready_identity = LocalHttpsReadyIdentity {
                server_identity: build_server_identity(
                    &public_identity.ca.certificate_der,
                    &validated_leaf.certificate_record,
                    &validated_leaf.private_key_record,
                ),
                persisted_identity: public_identity,
            };

            let _ = ca_material;

            Ok(LocalHttpsReconcileResult::ready(ready_identity, changes))
        }
    }
}

#[cfg(windows)]
pub fn install_firefox_machine_root_support() -> Result<(), String> {
    let paths = LocalHttpsPaths::current();
    let now = OffsetDateTime::now_utc();
    info!(
        "Starting Firefox machine-root support setup. local_https_dir={} metadata_path={} private_material_path={}",
        paths.root_dir.display(),
        paths.public_metadata_path.display(),
        paths.encrypted_private_material_path.display()
    );

    let (public_identity, private_material) = match load_stored_identity(&paths) {
        StoredIdentityState::Present(public_identity, private_material) => {
            info!(
                "Loaded local HTTPS identity for Firefox support setup. ca_fingerprint={} ca_not_after_unix={}",
                public_identity.ca.sha256_fingerprint,
                public_identity.ca.not_after_unix
            );
            (public_identity, private_material)
        }
        StoredIdentityState::Missing => {
            warn!(
                "Firefox support setup could not start because the local HTTPS identity is missing. local_https_dir={}",
                paths.root_dir.display()
            );
            return Err(
                "The local HTTPS identity has not been created yet. Restart the Companion and try Firefox support setup again."
                    .to_string(),
            );
        }
        StoredIdentityState::Partial => {
            warn!(
                "Firefox support setup could not start because the local HTTPS identity is incomplete. metadata_exists={} private_material_exists={}",
                paths.public_metadata_path.exists(),
                paths.encrypted_private_material_path.exists()
            );
            return Err(
                "The local HTTPS identity is incomplete. Repair local HTTPS before enabling Firefox support."
                    .to_string(),
            );
        }
        StoredIdentityState::Malformed(message) => {
            error!(
                "Firefox support setup could not parse the local HTTPS identity: {}",
                message
            );
            return Err(message);
        }
    };

    if public_identity.schema_version != LOCAL_HTTPS_SCHEMA_VERSION
        || private_material.schema_version != LOCAL_HTTPS_SCHEMA_VERSION
    {
        warn!(
            "Firefox support setup found unsupported local HTTPS identity schema versions. expected={} public={} private={}",
            LOCAL_HTTPS_SCHEMA_VERSION,
            public_identity.schema_version,
            private_material.schema_version
        );
        return Err(format!(
            "Unsupported local HTTPS identity schema version. Expected {}, found public={} and private={}.",
            LOCAL_HTTPS_SCHEMA_VERSION,
            public_identity.schema_version,
            private_material.schema_version
        ));
    }

    validate_ca_material(&public_identity.ca, &private_material.ca, now).map_err(|repair| {
        warn!(
            "Firefox support setup cannot continue because the local HTTPS CA needs repair. reason={:?} message={}",
            repair.reason,
            repair.message
        );
        format!(
            "The local HTTPS CA must be repaired before Firefox support can be enabled: {}",
            repair.message
        )
    })?;
    info!(
        "Validated local HTTPS CA for Firefox support setup. ca_fingerprint={}",
        public_identity.ca.sha256_fingerprint
    );

    let revocation_list_der =
        write_local_revocation_list(&paths, &public_identity.ca, &private_material.ca, now)?;
    info!(
        "Prepared local HTTPS CRL for Firefox support setup. crl_path={} crl_bytes={}",
        paths.revocation_list_path.display(),
        revocation_list_der.len()
    );
    let install_files = write_firefox_machine_root_install_files(
        &paths,
        &public_identity.ca.certificate_der,
        &revocation_list_der,
    )?;
    info!(
        "Prepared Firefox machine-root support installer. installer_path={} launcher_path={} log_path={}",
        install_files.installer_path.display(),
        install_files.launcher_path.display(),
        install_files.log_path.display()
    );

    run_firefox_machine_root_installer(&install_files)
}

#[cfg(not(windows))]
#[allow(dead_code)]
pub fn install_firefox_machine_root_support() -> Result<(), String> {
    Err("Firefox machine-root support setup is only available on Windows.".to_string())
}

#[cfg(windows)]
struct FirefoxMachineRootInstallFiles {
    installer_path: PathBuf,
    launcher_path: PathBuf,
    log_path: PathBuf,
}

#[cfg(windows)]
fn write_firefox_machine_root_install_files(
    paths: &LocalHttpsPaths,
    ca_certificate_der: &[u8],
    revocation_list_der: &[u8],
) -> Result<FirefoxMachineRootInstallFiles, String> {
    let install_dir = paths.root_dir.join(FIREFOX_SUPPORT_INSTALL_DIR);
    fs::create_dir_all(&install_dir).map_err(|error| {
        format!(
            "Failed to create the Firefox support setup directory {}: {}",
            install_dir.display(),
            error
        )
    })?;

    let ca_path = install_dir.join("nojoin-local-https-ca.cer");
    let crl_path = install_dir.join("nojoin-local-https-ca.crl");
    let script_path = install_dir.join("install-firefox-machine-root.ps1");
    let launcher_path = install_dir.join(FIREFOX_SUPPORT_LAUNCHER_FILE);
    let log_path = firefox_machine_root_install_log_path(&script_path);

    write_atomic_file(
        &ca_path,
        ca_certificate_der,
        "Firefox support local CA certificate",
    )?;
    write_atomic_file(
        &crl_path,
        revocation_list_der,
        "Firefox support local CA revocation list",
    )?;

    let script = build_firefox_machine_root_install_script(&ca_path, &crl_path, &log_path);
    write_atomic_file(
        &script_path,
        script.as_bytes(),
        "Firefox support elevated install script",
    )?;

    let launcher = build_firefox_machine_root_launcher_script(&script_path, &log_path);
    write_atomic_file(
        &launcher_path,
        launcher.as_bytes(),
        "Firefox support elevation launcher script",
    )?;

    Ok(FirefoxMachineRootInstallFiles {
        installer_path: script_path,
        launcher_path,
        log_path,
    })
}

#[cfg(windows)]
fn run_firefox_machine_root_installer(
    install_files: &FirefoxMachineRootInstallFiles,
) -> Result<(), String> {
    use std::process::Command;

    info!(
        "Launching Firefox support setup through launcher. installer_path={} launcher_path={} log_path={}",
        install_files.installer_path.display(),
        install_files.launcher_path.display(),
        install_files.log_path.display()
    );

    let output = Command::new("powershell.exe")
        .args([
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
        ])
        .arg("-File")
        .arg(&install_files.launcher_path)
        .output()
        .map_err(|error| {
            format!(
                "Failed to launch the elevated Firefox support setup: {}",
                error
            )
        })?;

    info!(
        "Firefox support setup launcher finished. status={} stdout_bytes={} stderr_bytes={} log_path={}",
        process_exit_status_summary(&output.status),
        output.stdout.len(),
        output.stderr.len(),
        install_files.log_path.display()
    );

    if output.status.success() {
        info!(
            "Firefox support setup completed successfully. installer_log_path={}",
            install_files.log_path.display()
        );
        return Ok(());
    }

    let stdout = String::from_utf8_lossy(&output.stdout).trim().to_string();
    let stderr = String::from_utf8_lossy(&output.stderr).trim().to_string();
    if !stdout.is_empty() {
        warn!("Firefox support setup stdout: {}", stdout);
    }
    if !stderr.is_empty() {
        warn!("Firefox support setup stderr: {}", stderr);
    }

    let install_log_summary = read_firefox_machine_root_install_log_summary(&install_files.log_path);
    if let Some(summary) = install_log_summary.as_ref() {
        warn!(
            "Firefox support setup installer log tail from {}: {}",
            install_files.log_path.display(),
            summary
        );
    } else {
        warn!(
            "Firefox support setup did not produce a readable installer log. log_path={}",
            install_files.log_path.display()
        );
    }

    let status_summary = process_exit_status_summary(&output.status);

    let details = [stdout, stderr]
        .into_iter()
        .filter(|value| !value.is_empty())
        .collect::<Vec<String>>()
        .join(" ");

    if details.is_empty() {
        let log_hint = install_log_summary
            .map(|summary| format!(" Last installer log lines: {}", summary))
            .unwrap_or_default();
        return Err(
            format!(
                "Firefox support setup exited without stdout/stderr. Status: {}. Installer log: {}.{}",
                status_summary,
                install_files.log_path.display(),
                log_hint
            ),
        );
    }

    Err(format!(
        "Firefox support setup failed with status {}: {} Installer log: {}",
        status_summary,
        details,
        install_files.log_path.display()
    ))
}

#[cfg(windows)]
fn powershell_single_quoted_path(path: &Path) -> String {
    format!("'{}'", path.to_string_lossy().replace('\'', "''"))
}

#[cfg(windows)]
fn firefox_machine_root_install_log_path(script_path: &Path) -> PathBuf {
    script_path.with_file_name(FIREFOX_SUPPORT_INSTALL_LOG_FILE)
}

#[cfg(windows)]
fn process_exit_status_summary(status: &std::process::ExitStatus) -> String {
    let code_summary = status
        .code()
        .map(|code| format!("code={} hex=0x{:08X}", code, code as u32))
        .unwrap_or_else(|| "code=<none>".to_string());
    format!("{} ({})", status, code_summary)
}

#[cfg(windows)]
fn build_firefox_machine_root_launcher_script(installer_path: &Path, log_path: &Path) -> String {
    let lines = [
        "$ErrorActionPreference = 'Stop'".to_string(),
        format!("$logPath = {}", powershell_single_quoted_path(log_path)),
        format!(
            "$installerPath = {}",
            powershell_single_quoted_path(installer_path)
        ),
        "function Write-SetupLog {".to_string(),
        "    param([string]$Message)".to_string(),
        "    $timestamp = Get-Date -Format 'yyyy-MM-dd HH:mm:ss.fff K'".to_string(),
        "    Add-Content -LiteralPath $logPath -Encoding UTF8 -Value \"[$timestamp] $Message\"".to_string(),
        "}".to_string(),
        "Set-Content -LiteralPath $logPath -Encoding UTF8 -Value \"[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss.fff K')] Preparing Nojoin Firefox support elevation request.\"".to_string(),
        "try {".to_string(),
        "    Write-SetupLog \"Launcher PowerShell version: $($PSVersionTable.PSVersion)\"".to_string(),
        "    Write-SetupLog \"Launcher process id: $PID\"".to_string(),
        "    Write-SetupLog \"Installer script: $installerPath\"".to_string(),
        "    if (-not (Test-Path -LiteralPath $installerPath)) {".to_string(),
        "        Write-SetupLog 'Installer script is missing before elevation.'".to_string(),
        "        exit 2".to_string(),
        "    }".to_string(),
        "    $installerCommandPath = $installerPath.Replace(\"'\", \"''\")".to_string(),
        "    $childCommand = \"& '$installerCommandPath'\"".to_string(),
        "    $encodedCommand = [Convert]::ToBase64String([System.Text.Encoding]::Unicode.GetBytes($childCommand))".to_string(),
        "    $argumentList = \"-NoProfile -ExecutionPolicy Bypass -EncodedCommand $encodedCommand\"".to_string(),
        "    Write-SetupLog 'Requesting elevation with Start-Process -Verb RunAs.'".to_string(),
        "    $process = Start-Process -FilePath 'powershell.exe' -ArgumentList $argumentList -Verb RunAs -Wait -PassThru".to_string(),
        "    if ($null -eq $process) {".to_string(),
        "        Write-SetupLog 'Start-Process returned no process object.'".to_string(),
        "        exit 1".to_string(),
        "    }".to_string(),
        "    Write-SetupLog \"Elevated installer process id: $($process.Id)\"".to_string(),
        "    Write-SetupLog \"Elevated installer process exit code: $($process.ExitCode)\"".to_string(),
        "    if ($null -eq $process.ExitCode) {".to_string(),
        "        Write-SetupLog 'Elevated installer exit code was null.'".to_string(),
        "        exit 1".to_string(),
        "    }".to_string(),
        "    exit $process.ExitCode".to_string(),
        "} catch {".to_string(),
        "    Write-SetupLog (\"Elevation request failed. Exception type: \" + $_.Exception.GetType().FullName)".to_string(),
        "    Write-SetupLog (\"HRESULT: \" + $_.Exception.HResult)".to_string(),
        "    Write-SetupLog (\"Message: \" + $_.Exception.Message)".to_string(),
        "    Write-SetupLog $_.ScriptStackTrace".to_string(),
        "    if ($_.Exception.Message -match 'cancel') { exit 1223 }".to_string(),
        "    exit 1".to_string(),
        "}".to_string(),
    ];

    format!("{}\n", lines.join("\n"))
}

#[cfg(windows)]
fn build_firefox_machine_root_install_script(
    ca_path: &Path,
    crl_path: &Path,
    log_path: &Path,
) -> String {
    let lines = [
        "$ErrorActionPreference = 'Stop'".to_string(),
        format!("$logPath = {}", powershell_single_quoted_path(log_path)),
        "function Write-SetupLog {".to_string(),
        "    param([string]$Message)".to_string(),
        "    $timestamp = Get-Date -Format 'yyyy-MM-dd HH:mm:ss.fff K'".to_string(),
        "    Add-Content -LiteralPath $logPath -Encoding UTF8 -Value \"[$timestamp] $Message\"".to_string(),
        "}".to_string(),
        "Set-Content -LiteralPath $logPath -Encoding UTF8 -Value \"[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss.fff K')] Starting elevated Nojoin Firefox support setup.\"".to_string(),
        "try {".to_string(),
        "    $certutil = Join-Path $env:SystemRoot 'System32\\certutil.exe'".to_string(),
        "    Write-SetupLog \"Using certutil at $certutil\"".to_string(),
        format!(
            "    Write-SetupLog \"Adding local HTTPS CA to Local Machine Root store from {}\"",
            ca_path.display()
        ),
        format!(
            "    & $certutil -f -addstore Root {} *>&1 | ForEach-Object {{ Write-SetupLog $_.ToString() }}",
            powershell_single_quoted_path(ca_path)
        ),
        "    $rootExitCode = $LASTEXITCODE".to_string(),
        "    Write-SetupLog \"certutil Root exit code: $rootExitCode\"".to_string(),
        "    if ($rootExitCode -ne 0) { exit $rootExitCode }".to_string(),
        format!(
            "    Write-SetupLog \"Adding local HTTPS CRL to Local Machine CA store from {}\"",
            crl_path.display()
        ),
        format!(
            "    & $certutil -f -addstore CA {} *>&1 | ForEach-Object {{ Write-SetupLog $_.ToString() }}",
            powershell_single_quoted_path(crl_path)
        ),
        "    $caExitCode = $LASTEXITCODE".to_string(),
        "    Write-SetupLog \"certutil CA exit code: $caExitCode\"".to_string(),
        "    if ($caExitCode -ne 0) { exit $caExitCode }".to_string(),
        "    Write-SetupLog 'Completed Nojoin Firefox support setup successfully.'".to_string(),
        "    exit 0".to_string(),
        "} catch {".to_string(),
        "    Write-SetupLog (\"Unhandled error: \" + $_.Exception.Message)".to_string(),
        "    Write-SetupLog $_.ScriptStackTrace".to_string(),
        "    exit 1".to_string(),
        "}".to_string(),
    ];

    format!("{}\n", lines.join("\n"))
}

#[cfg(windows)]
fn read_firefox_machine_root_install_log_summary(log_path: &Path) -> Option<String> {
    let contents = read_firefox_machine_root_install_log(log_path)?;
    let lines = contents
        .lines()
        .rev()
        .take(12)
        .collect::<Vec<&str>>()
        .into_iter()
        .rev()
        .collect::<Vec<&str>>()
        .join(" | ");

    if lines.trim().is_empty() {
        None
    } else {
        Some(lines)
    }
}

#[cfg(windows)]
fn read_firefox_machine_root_install_log(log_path: &Path) -> Option<String> {
    let bytes = fs::read(log_path).ok()?;
    match String::from_utf8(bytes.clone()) {
        Ok(contents) => Some(contents),
        Err(_) => decode_utf16le_text(&bytes),
    }
}

#[cfg(windows)]
fn decode_utf16le_text(bytes: &[u8]) -> Option<String> {
    let bytes = bytes.strip_prefix(&[0xFF, 0xFE]).unwrap_or(bytes);
    if bytes.len() % 2 != 0 {
        return None;
    }

    let code_units = bytes
        .chunks_exact(2)
        .map(|chunk| u16::from_le_bytes([chunk[0], chunk[1]]))
        .collect::<Vec<u16>>();
    String::from_utf16(&code_units).ok()
}

#[derive(Clone, Debug, Serialize, Deserialize, PartialEq, Eq)]
struct PersistedLocalHttpsPrivateMaterial {
    schema_version: u32,
    ca: PersistedPrivateKeyRecord,
    leaf: Option<PersistedPrivateKeyRecord>,
}

#[derive(Clone, Debug, Serialize, Deserialize, PartialEq, Eq)]
struct PersistedPrivateKeyRecord {
    #[serde(with = "base64_bytes")]
    private_key_pkcs8_der: Vec<u8>,
    public_key_spki_sha256: String,
}

#[derive(Clone, Debug)]
struct ValidatedCaMaterial {
    _certificate_record: PersistedCertificateRecord,
    _private_key_record: PersistedPrivateKeyRecord,
}

#[derive(Clone, Debug)]
struct ValidatedLeafMaterial {
    certificate_record: PersistedCertificateRecord,
    private_key_record: PersistedPrivateKeyRecord,
    not_after: OffsetDateTime,
}

#[derive(Debug)]
struct GeneratedLocalHttpsIdentity {
    public_identity: PersistedLocalHttpsIdentity,
    private_material: PersistedLocalHttpsPrivateMaterial,
}

#[derive(Clone, Debug)]
struct IssuedCertificateMaterial {
    certificate_der: Vec<u8>,
    private_key_pkcs8_der: Vec<u8>,
    sha256_fingerprint: String,
    public_key_spki_sha256: String,
    not_before: OffsetDateTime,
    not_after: OffsetDateTime,
}

impl IssuedCertificateMaterial {
    fn new(
        certificate_der: Vec<u8>,
        private_key_pkcs8_der: Vec<u8>,
        public_key_spki_der: Vec<u8>,
        not_before: OffsetDateTime,
        not_after: OffsetDateTime,
    ) -> Self {
        Self {
            sha256_fingerprint: sha256_fingerprint(&certificate_der),
            public_key_spki_sha256: sha256_fingerprint(&public_key_spki_der),
            certificate_der,
            private_key_pkcs8_der,
            not_before,
            not_after,
        }
    }

    fn to_certificate_record(&self) -> PersistedCertificateRecord {
        PersistedCertificateRecord {
            certificate_der: self.certificate_der.clone(),
            sha256_fingerprint: self.sha256_fingerprint.clone(),
            public_key_spki_sha256: self.public_key_spki_sha256.clone(),
            not_before_unix: self.not_before.unix_timestamp(),
            not_after_unix: self.not_after.unix_timestamp(),
        }
    }

    fn to_private_key_record(&self) -> PersistedPrivateKeyRecord {
        PersistedPrivateKeyRecord {
            private_key_pkcs8_der: self.private_key_pkcs8_der.clone(),
            public_key_spki_sha256: self.public_key_spki_sha256.clone(),
        }
    }
}

enum StoredIdentityState {
    Missing,
    Partial,
    Malformed(String),
    Present(
        PersistedLocalHttpsIdentity,
        PersistedLocalHttpsPrivateMaterial,
    ),
}

fn load_stored_identity(paths: &LocalHttpsPaths) -> StoredIdentityState {
    let public_exists = paths.public_metadata_path.exists();
    let private_exists = paths.encrypted_private_material_path.exists();

    match (public_exists, private_exists) {
        (false, false) => StoredIdentityState::Missing,
        (true, false) | (false, true) => StoredIdentityState::Partial,
        (true, true) => {
            let public_bytes = match fs::read(&paths.public_metadata_path) {
                Ok(bytes) => bytes,
                Err(error) => {
                    return StoredIdentityState::Malformed(format!(
                        "Failed to read local HTTPS identity metadata {}: {}",
                        paths.public_metadata_path.display(),
                        error
                    ));
                }
            };
            let public_identity =
                match serde_json::from_slice::<PersistedLocalHttpsIdentity>(&public_bytes) {
                    Ok(identity) => identity,
                    Err(error) => {
                        return StoredIdentityState::Malformed(format!(
                            "Failed to parse local HTTPS identity metadata {}: {}",
                            paths.public_metadata_path.display(),
                            error
                        ));
                    }
                };

            let protected_private_bytes = match fs::read(&paths.encrypted_private_material_path) {
                Ok(bytes) => bytes,
                Err(error) => {
                    return StoredIdentityState::Malformed(format!(
                        "Failed to read local HTTPS private material {}: {}",
                        paths.encrypted_private_material_path.display(),
                        error
                    ));
                }
            };
            let private_bytes = match unprotect_bytes(&protected_private_bytes) {
                Ok(bytes) => bytes,
                Err(error) => {
                    return StoredIdentityState::Malformed(format!(
                        "Failed to decrypt local HTTPS private material {}: {}",
                        paths.encrypted_private_material_path.display(),
                        error
                    ));
                }
            };
            let private_material = match serde_json::from_slice::<PersistedLocalHttpsPrivateMaterial>(
                &private_bytes,
            ) {
                Ok(material) => material,
                Err(error) => {
                    return StoredIdentityState::Malformed(format!(
                        "Failed to parse local HTTPS private material {}: {}",
                        paths.encrypted_private_material_path.display(),
                        error
                    ));
                }
            };

            StoredIdentityState::Present(public_identity, private_material)
        }
    }
}

fn write_stored_identity(
    paths: &LocalHttpsPaths,
    public_identity: &PersistedLocalHttpsIdentity,
    private_material: &PersistedLocalHttpsPrivateMaterial,
) -> Result<(), String> {
    fs::create_dir_all(&paths.root_dir).map_err(|error| {
        format!(
            "Failed to create the local HTTPS storage directory {}: {}",
            paths.root_dir.display(),
            error
        )
    })?;

    let public_bytes = serde_json::to_vec_pretty(public_identity).map_err(|error| {
        format!(
            "Failed to serialize local HTTPS identity metadata: {}",
            error
        )
    })?;
    let private_bytes = serde_json::to_vec(private_material).map_err(|error| {
        format!(
            "Failed to serialize local HTTPS private material: {}",
            error
        )
    })?;
    let protected_private_bytes =
        protect_bytes_with_description(LOCAL_HTTPS_DPAPI_DESCRIPTION, &private_bytes)?;

    write_atomic_file(
        &paths.public_metadata_path,
        &public_bytes,
        "local HTTPS identity metadata",
    )?;
    write_atomic_file(
        &paths.encrypted_private_material_path,
        &protected_private_bytes,
        "local HTTPS private material",
    )?;

    Ok(())
}

fn write_atomic_file(path: &Path, bytes: &[u8], label: &str) -> Result<(), String> {
    let temp_path = temp_write_path(path);
    fs::write(&temp_path, bytes).map_err(|error| {
        format!(
            "Failed to write temporary {} file {}: {}",
            label,
            temp_path.display(),
            error
        )
    })?;

    replace_file(&temp_path, path)
}

fn write_local_revocation_list(
    paths: &LocalHttpsPaths,
    ca_certificate_record: &PersistedCertificateRecord,
    ca_private_key_record: &PersistedPrivateKeyRecord,
    now: OffsetDateTime,
) -> Result<Vec<u8>, String> {
    fs::create_dir_all(&paths.root_dir).map_err(|error| {
        format!(
            "Failed to create the local HTTPS storage directory {}: {}",
            paths.root_dir.display(),
            error
        )
    })?;

    let ca_key_pair = KeyPair::try_from(ca_private_key_record.private_key_pkcs8_der.clone())
        .map_err(|error| {
            format!(
                "Failed to load the persisted local HTTPS CA private key while preparing the certificate revocation list: {}",
                error
            )
        })?;
    let ca_certificate_der = CertificateDer::from(ca_certificate_record.certificate_der.clone());
    let issuer = Issuer::from_ca_cert_der(&ca_certificate_der, ca_key_pair).map_err(|error| {
        format!(
            "Failed to reuse the persisted local HTTPS CA while preparing the certificate revocation list: {}",
            error
        )
    })?;
    let revocation_list_der = issue_empty_revocation_list_with_issuer(now, &issuer)?;

    write_atomic_file(
        &paths.revocation_list_path,
        &revocation_list_der,
        "local HTTPS certificate revocation list",
    )?;

    Ok(revocation_list_der)
}

fn generate_full_identity(
    paths: &LocalHttpsPaths,
    now: OffsetDateTime,
) -> Result<GeneratedLocalHttpsIdentity, String> {
    let ca_key_pair = KeyPair::generate()
        .map_err(|error| format!("Failed to generate the local HTTPS CA key pair: {}", error))?;
    let ca_private_key_pkcs8_der = ca_key_pair.serialize_der();
    let ca_public_key_spki = ca_key_pair.subject_public_key_info();
    let ca_params = ca_certificate_params(now)?;
    let ca_certificate = ca_params.self_signed(&ca_key_pair).map_err(|error| {
        format!(
            "Failed to self-sign the local HTTPS CA certificate: {}",
            error
        )
    })?;
    let ca_material = IssuedCertificateMaterial::new(
        ca_certificate.der().as_ref().to_vec(),
        ca_private_key_pkcs8_der,
        ca_public_key_spki,
        ca_params.not_before,
        ca_params.not_after,
    );

    let issuer = Issuer::from_ca_cert_der(ca_certificate.der(), ca_key_pair).map_err(|error| {
        format!(
            "Failed to prepare the local HTTPS CA as an issuing identity: {}",
            error
        )
    })?;
    let leaf_material = issue_leaf_with_issuer(paths, now, &issuer)?;

    Ok(GeneratedLocalHttpsIdentity {
        public_identity: PersistedLocalHttpsIdentity {
            schema_version: LOCAL_HTTPS_SCHEMA_VERSION,
            ca: ca_material.to_certificate_record(),
            leaf: Some(leaf_material.to_certificate_record()),
        },
        private_material: PersistedLocalHttpsPrivateMaterial {
            schema_version: LOCAL_HTTPS_SCHEMA_VERSION,
            ca: ca_material.to_private_key_record(),
            leaf: Some(leaf_material.to_private_key_record()),
        },
    })
}

fn generate_leaf_from_existing_ca(
    paths: &LocalHttpsPaths,
    ca_certificate_record: &PersistedCertificateRecord,
    ca_private_key_record: &PersistedPrivateKeyRecord,
    now: OffsetDateTime,
) -> Result<IssuedCertificateMaterial, String> {
    let ca_key_pair = KeyPair::try_from(ca_private_key_record.private_key_pkcs8_der.clone())
        .map_err(|error| {
            format!(
                "Failed to load the persisted local HTTPS CA private key while renewing the leaf certificate: {}",
                error
            )
        })?;
    let ca_certificate_der = CertificateDer::from(ca_certificate_record.certificate_der.clone());
    let issuer = Issuer::from_ca_cert_der(&ca_certificate_der, ca_key_pair).map_err(|error| {
        format!(
            "Failed to reuse the persisted local HTTPS CA while renewing the leaf certificate: {}",
            error
        )
    })?;

    issue_leaf_with_issuer(paths, now, &issuer)
}

fn issue_leaf_with_issuer<S>(
    paths: &LocalHttpsPaths,
    now: OffsetDateTime,
    issuer: &Issuer<'_, S>,
) -> Result<IssuedCertificateMaterial, String>
where
    S: SigningKey,
{
    let leaf_key_pair = KeyPair::generate().map_err(|error| {
        format!(
            "Failed to generate the local HTTPS leaf key pair: {}",
            error
        )
    })?;
    let leaf_private_key_pkcs8_der = leaf_key_pair.serialize_der();
    let leaf_public_key_spki = leaf_key_pair.subject_public_key_info();
    let leaf_params = leaf_certificate_params(paths, now)?;
    let leaf_certificate = leaf_params
        .signed_by(&leaf_key_pair, issuer)
        .map_err(|error| format!("Failed to sign the local HTTPS leaf certificate: {}", error))?;

    Ok(IssuedCertificateMaterial::new(
        leaf_certificate.der().as_ref().to_vec(),
        leaf_private_key_pkcs8_der,
        leaf_public_key_spki,
        leaf_params.not_before,
        leaf_params.not_after,
    ))
}

fn ca_certificate_params(now: OffsetDateTime) -> Result<CertificateParams, String> {
    let mut params = CertificateParams::new(Vec::<String>::new()).map_err(|error| {
        format!(
            "Failed to prepare local HTTPS CA certificate parameters: {}",
            error
        )
    })?;
    let mut distinguished_name = DistinguishedName::new();
    distinguished_name.push(DnType::CommonName, CA_COMMON_NAME);
    params.distinguished_name = distinguished_name;
    params.is_ca = IsCa::Ca(BasicConstraints::Constrained(0));
    params.key_usages = vec![KeyUsagePurpose::KeyCertSign, KeyUsagePurpose::CrlSign];
    params.not_before = now - certificate_backdate();
    params.not_after = now + ca_validity();
    params.use_authority_key_identifier_extension = true;
    Ok(params)
}

fn leaf_certificate_params(
    paths: &LocalHttpsPaths,
    now: OffsetDateTime,
) -> Result<CertificateParams, String> {
    let mut params =
        CertificateParams::new(required_leaf_subject_alt_names()).map_err(|error| {
            format!(
                "Failed to prepare local HTTPS leaf certificate parameters: {}",
                error
            )
        })?;
    let mut distinguished_name = DistinguishedName::new();
    distinguished_name.push(DnType::CommonName, LEAF_COMMON_NAME);
    params.distinguished_name = distinguished_name;
    params.is_ca = IsCa::ExplicitNoCa;
    params.key_usages = vec![KeyUsagePurpose::DigitalSignature];
    params.extended_key_usages = vec![ExtendedKeyUsagePurpose::ServerAuth];
    params.not_before = now - certificate_backdate();
    params.not_after = now + leaf_validity();
    params.use_authority_key_identifier_extension = true;
    params.crl_distribution_points = vec![CrlDistributionPoint {
        uris: vec![required_leaf_crl_distribution_point_uri(paths)?],
    }];
    Ok(params)
}

fn issue_empty_revocation_list_with_issuer<S>(
    now: OffsetDateTime,
    issuer: &Issuer<'_, S>,
) -> Result<Vec<u8>, String>
where
    S: SigningKey,
{
    let revocation_list = CertificateRevocationListParams {
        this_update: now - certificate_backdate(),
        next_update: now + revocation_list_validity(),
        crl_number: current_revocation_list_number(now),
        issuing_distribution_point: None,
        revoked_certs: Vec::new(),
        key_identifier_method: KeyIdMethod::Sha256,
    }
    .signed_by(issuer)
    .map_err(|error| {
        format!(
            "Failed to sign the local HTTPS certificate revocation list: {}",
            error
        )
    })?;

    Ok(revocation_list.der().as_ref().to_vec())
}

fn validate_ca_material(
    certificate_record: &PersistedCertificateRecord,
    private_key_record: &PersistedPrivateKeyRecord,
    now: OffsetDateTime,
) -> Result<ValidatedCaMaterial, LocalHttpsRepairRequired> {
    let parsed_certificate =
        validate_certificate_record(certificate_record).map_err(|message| {
            LocalHttpsRepairRequired {
                reason: LocalHttpsRepairReason::InvalidCaMaterial,
                message,
            }
        })?;

    if !parsed_certificate.is_ca {
        return Err(LocalHttpsRepairRequired {
            reason: LocalHttpsRepairReason::InvalidCaMaterial,
            message:
                "The persisted local HTTPS CA certificate is not marked as a certificate authority."
                    .to_string(),
        });
    }
    if parsed_certificate.not_after <= now {
        return Err(LocalHttpsRepairRequired {
            reason: LocalHttpsRepairReason::InvalidCaMaterial,
            message: "The persisted local HTTPS CA certificate has expired and must be repaired."
                .to_string(),
        });
    }

    validate_private_key_record(
        private_key_record,
        &certificate_record.public_key_spki_sha256,
    )
    .map_err(|message| LocalHttpsRepairRequired {
        reason: LocalHttpsRepairReason::InvalidCaMaterial,
        message,
    })?;

    Ok(ValidatedCaMaterial {
        _certificate_record: certificate_record.clone(),
        _private_key_record: private_key_record.clone(),
    })
}

fn validate_leaf_material(
    paths: &LocalHttpsPaths,
    certificate_record: Option<&PersistedCertificateRecord>,
    private_key_record: Option<&PersistedPrivateKeyRecord>,
) -> Result<Option<ValidatedLeafMaterial>, String> {
    let Some(certificate_record) = certificate_record else {
        return Ok(None);
    };
    let Some(private_key_record) = private_key_record else {
        return Ok(None);
    };
    let parsed_certificate = match validate_certificate_record(certificate_record) {
        Ok(parsed_certificate) => parsed_certificate,
        Err(_) => return Ok(None),
    };

    if parsed_certificate.is_ca
        || parsed_certificate.subject_alt_names != required_leaf_subject_alt_name_set()
        || parsed_certificate.crl_distribution_point_uris
            != required_leaf_crl_distribution_point_set(paths)?
    {
        return Ok(None);
    }

    validate_private_key_record(
        private_key_record,
        &certificate_record.public_key_spki_sha256,
    )
    .map_err(|error| {
        format!(
            "Failed to validate the persisted local HTTPS leaf private key: {}",
            error
        )
    })?;

    Ok(Some(validated_leaf_from_records(
        certificate_record,
        private_key_record,
    )))
}

fn validated_leaf_from_records(
    certificate_record: &PersistedCertificateRecord,
    private_key_record: &PersistedPrivateKeyRecord,
) -> ValidatedLeafMaterial {
    ValidatedLeafMaterial {
        certificate_record: certificate_record.clone(),
        private_key_record: private_key_record.clone(),
        not_after: OffsetDateTime::from_unix_timestamp(certificate_record.not_after_unix)
            .expect("persisted certificate not_after must be valid"),
    }
}

fn validate_certificate_record(
    certificate_record: &PersistedCertificateRecord,
) -> Result<ParsedCertificateDetails, String> {
    let parsed_certificate = parse_certificate_details(&certificate_record.certificate_der)?;
    let actual_fingerprint = sha256_fingerprint(&certificate_record.certificate_der);
    if normalize_fingerprint(&certificate_record.sha256_fingerprint) != actual_fingerprint {
        return Err("The persisted local HTTPS certificate fingerprint does not match the certificate on disk."
            .to_string());
    }

    let public_key_spki_sha256 = sha256_fingerprint(&parsed_certificate.public_key_spki_der);
    if normalize_fingerprint(&certificate_record.public_key_spki_sha256) != public_key_spki_sha256 {
        return Err(
            "The persisted local HTTPS certificate public-key fingerprint does not match the certificate on disk."
                .to_string(),
        );
    }

    if certificate_record.not_before_unix != parsed_certificate.not_before.unix_timestamp()
        || certificate_record.not_after_unix != parsed_certificate.not_after.unix_timestamp()
    {
        return Err(
            "The persisted local HTTPS certificate validity timestamps do not match the certificate on disk."
                .to_string(),
        );
    }

    Ok(parsed_certificate)
}

fn validate_private_key_record(
    private_key_record: &PersistedPrivateKeyRecord,
    expected_public_key_spki_sha256: &str,
) -> Result<(), String> {
    let key_pair =
        KeyPair::try_from(private_key_record.private_key_pkcs8_der.clone()).map_err(|error| {
            format!(
                "Failed to load the persisted local HTTPS private key: {}",
                error
            )
        })?;
    let actual_public_key_spki_sha256 = sha256_fingerprint(&key_pair.subject_public_key_info());

    if normalize_fingerprint(&private_key_record.public_key_spki_sha256)
        != actual_public_key_spki_sha256
    {
        return Err(
            "The persisted local HTTPS private key fingerprint does not match the encrypted material on disk."
                .to_string(),
        );
    }
    if normalize_fingerprint(expected_public_key_spki_sha256) != actual_public_key_spki_sha256 {
        return Err(
            "The persisted local HTTPS private key does not match its certificate.".to_string(),
        );
    }

    Ok(())
}

fn build_server_identity(
    ca_certificate_der: &[u8],
    leaf_certificate_record: &PersistedCertificateRecord,
    leaf_private_key_record: &PersistedPrivateKeyRecord,
) -> LocalHttpsServerIdentity {
    LocalHttpsServerIdentity {
        certificate_chain: vec![
            CertificateDer::from(leaf_certificate_record.certificate_der.clone()),
            CertificateDer::from(ca_certificate_der.to_vec()),
        ],
        private_key: PrivateKeyDer::Pkcs8(PrivatePkcs8KeyDer::from(
            leaf_private_key_record.private_key_pkcs8_der.clone(),
        )),
    }
}

fn should_renew_leaf(not_after: OffsetDateTime, now: OffsetDateTime) -> bool {
    not_after - now <= leaf_renewal_window()
}

fn normalize_fingerprint(value: &str) -> String {
    value.trim().to_ascii_uppercase()
}

fn sha256_fingerprint(bytes: &[u8]) -> String {
    let mut hasher = Sha256::new();
    hasher.update(bytes);
    let digest = hasher.finalize();

    digest
        .iter()
        .map(|byte| format!("{:02X}", byte))
        .collect::<Vec<String>>()
        .join(":")
}

fn required_leaf_subject_alt_names() -> Vec<String> {
    required_leaf_subject_alt_name_set().into_iter().collect()
}

fn required_leaf_subject_alt_name_set() -> BTreeSet<String> {
    ["localhost", "127.0.0.1", "::1"]
        .into_iter()
        .map(str::to_string)
        .collect()
}

fn certificate_backdate() -> Duration {
    Duration::hours(1)
}

fn ca_validity() -> Duration {
    Duration::days(365 * 5)
}

fn leaf_validity() -> Duration {
    Duration::days(180)
}

fn leaf_renewal_window() -> Duration {
    Duration::days(30)
}

fn revocation_list_validity() -> Duration {
    leaf_validity()
}

fn current_revocation_list_number(now: OffsetDateTime) -> SerialNumber {
    let number = u64::try_from(now.unix_timestamp_nanos()).unwrap_or(1);
    SerialNumber::from(number)
}

fn required_leaf_crl_distribution_point_set(
    paths: &LocalHttpsPaths,
) -> Result<BTreeSet<String>, String> {
    Ok([required_leaf_crl_distribution_point_uri(paths)?]
        .into_iter()
        .collect())
}

fn required_leaf_crl_distribution_point_uri(paths: &LocalHttpsPaths) -> Result<String, String> {
    let revocation_list_path = absolute_path(&paths.revocation_list_path)?;
    Url::from_file_path(&revocation_list_path)
        .map(|url| url.to_string())
        .map_err(|()| {
            format!(
                "Failed to convert the local HTTPS certificate revocation list path {} into a file URL.",
                revocation_list_path.display()
            )
        })
}

fn absolute_path(path: &Path) -> Result<PathBuf, String> {
    if path.is_absolute() {
        return Ok(path.to_path_buf());
    }

    std::env::current_dir()
        .map(|current_dir| current_dir.join(path))
        .map_err(|error| {
            format!(
                "Failed to resolve the local HTTPS path {} relative to the current working directory: {}",
                path.display(),
                error
            )
        })
}

struct ParsedCertificateDetails {
    not_before: OffsetDateTime,
    not_after: OffsetDateTime,
    public_key_spki_der: Vec<u8>,
    is_ca: bool,
    subject_alt_names: BTreeSet<String>,
    crl_distribution_point_uris: BTreeSet<String>,
}

fn parse_certificate_details(certificate_der: &[u8]) -> Result<ParsedCertificateDetails, String> {
    let (_, certificate) = X509Certificate::from_der(certificate_der).map_err(|error| {
        format!(
            "Failed to parse a persisted local HTTPS certificate: {}",
            error
        )
    })?;

    let subject_alt_names = certificate
        .subject_alternative_name()
        .map_err(|error| {
            format!(
                "Failed to parse the subject alternative names from a persisted local HTTPS certificate: {}",
                error
            )
        })?
        .map(|extension| {
            extension
                .value
                .general_names
                .iter()
                .filter_map(|general_name| match general_name {
                    GeneralName::DNSName(name) => Some(name.to_string()),
                    GeneralName::IPAddress(bytes) => ip_address_from_der(bytes).map(|value| value.to_string()),
                    _ => None,
                })
                .collect::<BTreeSet<String>>()
        })
        .unwrap_or_default();

    let is_ca = certificate
        .basic_constraints()
        .map_err(|error| {
            format!(
                "Failed to parse the basic constraints from a persisted local HTTPS certificate: {}",
                error
            )
        })?
        .map(|extension| extension.value.ca)
        .unwrap_or(false);

    let crl_distribution_point_uris = certificate
        .iter_extensions()
        .filter_map(|extension| match extension.parsed_extension() {
            ParsedExtension::CRLDistributionPoints(points) => Some(points),
            _ => None,
        })
        .flat_map(|points| points.iter())
        .filter_map(|distribution_point| distribution_point.distribution_point.as_ref())
        .flat_map(|distribution_point| match distribution_point {
            DistributionPointName::FullName(names) => names
                .iter()
                .filter_map(|name| match name {
                    GeneralName::URI(uri) => Some(uri.to_string()),
                    _ => None,
                })
                .collect::<Vec<String>>(),
            DistributionPointName::NameRelativeToCRLIssuer(_) => Vec::new(),
        })
        .collect();

    Ok(ParsedCertificateDetails {
        not_before: certificate.validity().not_before.to_datetime(),
        not_after: certificate.validity().not_after.to_datetime(),
        public_key_spki_der: certificate.public_key().raw.to_vec(),
        is_ca,
        subject_alt_names,
        crl_distribution_point_uris,
    })
}

fn ip_address_from_der(bytes: &[u8]) -> Option<IpAddr> {
    match bytes.len() {
        4 => Some(IpAddr::V4(Ipv4Addr::new(
            bytes[0], bytes[1], bytes[2], bytes[3],
        ))),
        16 => {
            let octets: [u8; 16] = bytes.try_into().ok()?;
            Some(IpAddr::V6(Ipv6Addr::from(octets)))
        }
        _ => None,
    }
}

#[cfg(windows)]
impl LocalCaTrustStore for SystemLocalCaTrustStore {
    fn is_ca_trusted(&self, ca_certificate_der: &[u8]) -> Result<bool, String> {
        let store = open_current_user_root_store()?;
        cert_store_contains_der(&store, ca_certificate_der)
    }

    fn install_ca(&self, ca_certificate_der: &[u8]) -> Result<(), String> {
        use std::ptr;
        use windows_sys::Win32::Security::Cryptography::{
            CertAddEncodedCertificateToStore, CERT_STORE_ADD_USE_EXISTING, PKCS_7_ASN_ENCODING,
            X509_ASN_ENCODING,
        };

        let store = open_current_user_root_store()?;
        let result = unsafe {
            CertAddEncodedCertificateToStore(
                store.0,
                X509_ASN_ENCODING | PKCS_7_ASN_ENCODING,
                ca_certificate_der.as_ptr(),
                ca_certificate_der.len() as u32,
                CERT_STORE_ADD_USE_EXISTING,
                ptr::null_mut(),
            )
        };

        if result == 0 {
            return Err(format!(
                "Failed to add the local HTTPS CA to the current-user ROOT store: {}",
                std::io::Error::last_os_error()
            ));
        }

        Ok(())
    }

    fn install_crl(&self, crl_der: &[u8]) -> Result<(), String> {
        use std::ptr;
        use windows_sys::Win32::Security::Cryptography::{
            CertAddEncodedCRLToStore, CERT_STORE_ADD_REPLACE_EXISTING, X509_ASN_ENCODING,
        };

        let store = open_current_user_ca_store()?;
        let result = unsafe {
            CertAddEncodedCRLToStore(
                store.0,
                X509_ASN_ENCODING,
                crl_der.as_ptr(),
                crl_der.len() as u32,
                CERT_STORE_ADD_REPLACE_EXISTING,
                ptr::null_mut(),
            )
        };

        if result == 0 {
            return Err(format!(
                "Failed to add the local HTTPS certificate revocation list to the current-user CA store: {}",
                std::io::Error::last_os_error()
            ));
        }

        Ok(())
    }
}

#[cfg(not(windows))]
impl LocalCaTrustStore for SystemLocalCaTrustStore {
    fn is_ca_trusted(&self, _ca_certificate_der: &[u8]) -> Result<bool, String> {
        Err(
            "Windows current-user trust-store integration is only implemented on Windows."
                .to_string(),
        )
    }

    fn install_ca(&self, _ca_certificate_der: &[u8]) -> Result<(), String> {
        Err(
            "Windows current-user trust-store integration is only implemented on Windows."
                .to_string(),
        )
    }

    fn install_crl(&self, _crl_der: &[u8]) -> Result<(), String> {
        Err(
            "Windows current-user trust-store integration is only implemented on Windows."
                .to_string(),
        )
    }
}

#[cfg(windows)]
struct CertStoreHandle(windows_sys::Win32::Security::Cryptography::HCERTSTORE);

#[cfg(windows)]
impl Drop for CertStoreHandle {
    fn drop(&mut self) {
        use windows_sys::Win32::Security::Cryptography::CertCloseStore;

        if !self.0.is_null() {
            unsafe {
                CertCloseStore(self.0, 0);
            }
        }
    }
}

#[cfg(windows)]
fn open_current_user_root_store() -> Result<CertStoreHandle, String> {
    open_current_user_system_store("ROOT")
}

#[cfg(windows)]
fn open_current_user_ca_store() -> Result<CertStoreHandle, String> {
    open_current_user_system_store("CA")
}

#[cfg(windows)]
fn open_current_user_system_store(store_name: &str) -> Result<CertStoreHandle, String> {
    use windows_sys::Win32::Security::Cryptography::CertOpenSystemStoreW;

    let store_name: Vec<u16> = format!("{}\0", store_name).encode_utf16().collect();
    let store = unsafe { CertOpenSystemStoreW(0, store_name.as_ptr()) };
    if store.is_null() {
        return Err(format!(
            "Failed to open the current-user {} certificate store: {}",
            store_name
                .strip_suffix(&[0])
                .and_then(|value| String::from_utf16(value).ok())
                .unwrap_or_else(|| "system".to_string()),
            std::io::Error::last_os_error()
        ));
    }

    Ok(CertStoreHandle(store))
}

#[cfg(windows)]
fn cert_store_contains_der(store: &CertStoreHandle, expected_der: &[u8]) -> Result<bool, String> {
    use std::ptr;
    use std::slice;
    use windows_sys::Win32::Security::Cryptography::{
        CertEnumCertificatesInStore, CertFreeCertificateContext,
    };

    let mut previous = ptr::null();
    loop {
        let certificate = unsafe { CertEnumCertificatesInStore(store.0, previous) };
        if certificate.is_null() {
            return Ok(false);
        }

        let certificate_der = unsafe {
            slice::from_raw_parts(
                (*certificate).pbCertEncoded,
                (*certificate).cbCertEncoded as usize,
            )
        };
        if certificate_der == expected_der {
            unsafe {
                CertFreeCertificateContext(certificate);
            }
            return Ok(true);
        }

        previous = certificate;
    }
}

mod base64_bytes {
    use super::*;

    pub fn serialize<S>(bytes: &[u8], serializer: S) -> Result<S::Ok, S::Error>
    where
        S: Serializer,
    {
        serializer.serialize_str(&BASE64_STANDARD.encode(bytes))
    }

    pub fn deserialize<'de, D>(deserializer: D) -> Result<Vec<u8>, D::Error>
    where
        D: Deserializer<'de>,
    {
        let encoded = String::deserialize(deserializer)?;
        BASE64_STANDARD
            .decode(encoded.as_bytes())
            .map_err(serde::de::Error::custom)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use rand::random;
    use std::sync::Mutex;
    use x509_parser::parse_x509_crl;

    #[derive(Default)]
    struct FakeTrustStore {
        trusted_fingerprints: Mutex<BTreeSet<String>>,
        install_calls: Mutex<u32>,
    }

    impl FakeTrustStore {
        fn trust(&self, certificate_der: &[u8]) {
            self.trusted_fingerprints
                .lock()
                .unwrap()
                .insert(sha256_fingerprint(certificate_der));
        }

        fn install_call_count(&self) -> u32 {
            *self.install_calls.lock().unwrap()
        }
    }

    impl LocalCaTrustStore for FakeTrustStore {
        fn is_ca_trusted(&self, ca_certificate_der: &[u8]) -> Result<bool, String> {
            Ok(self
                .trusted_fingerprints
                .lock()
                .unwrap()
                .contains(&sha256_fingerprint(ca_certificate_der)))
        }

        fn install_ca(&self, ca_certificate_der: &[u8]) -> Result<(), String> {
            *self.install_calls.lock().unwrap() += 1;
            self.trusted_fingerprints
                .lock()
                .unwrap()
                .insert(sha256_fingerprint(ca_certificate_der));
            Ok(())
        }

        fn install_crl(&self, _crl_der: &[u8]) -> Result<(), String> {
            Ok(())
        }
    }

    struct TestDir {
        path: PathBuf,
    }

    impl TestDir {
        fn new() -> Self {
            let path = std::env::temp_dir().join(format!(
                "nojoin-local-https-{}-{}",
                std::process::id(),
                random::<u64>()
            ));
            fs::create_dir_all(&path).unwrap();
            Self { path }
        }

        fn paths(&self) -> LocalHttpsPaths {
            LocalHttpsPaths::from_app_data_dir(&self.path)
        }
    }

    impl Drop for TestDir {
        fn drop(&mut self) {
            let _ = fs::remove_dir_all(&self.path);
        }
    }

    #[test]
    fn persisted_identity_round_trips_with_versioned_base64_fields() {
        let temp_dir = TestDir::new();
        let generated = generate_full_identity(&temp_dir.paths(), fixed_now()).unwrap();

        let serialized = serde_json::to_vec(&generated.public_identity).unwrap();
        let round_trip: PersistedLocalHttpsIdentity = serde_json::from_slice(&serialized).unwrap();

        assert_eq!(round_trip.schema_version, LOCAL_HTTPS_SCHEMA_VERSION);
        assert_eq!(round_trip, generated.public_identity);
    }

    #[test]
    fn unsupported_schema_requires_repair() {
        let temp_dir = TestDir::new();
        let paths = temp_dir.paths();
        let trust_store = FakeTrustStore::default();
        let (mut public_identity, private_material) = store_generated_identity(&paths, fixed_now());
        public_identity.schema_version += 1;
        write_stored_identity(&paths, &public_identity, &private_material).unwrap();

        let result = ensure_local_https_identity_with(&paths, &trust_store, fixed_now()).unwrap();

        match result.state {
            LocalHttpsReconcileState::RepairRequired(repair) => {
                assert_eq!(repair.reason, LocalHttpsRepairReason::UnsupportedSchema);
            }
            LocalHttpsReconcileState::Ready(_) => panic!("expected repair-required result"),
        }
    }

    #[test]
    fn missing_identity_bootstraps_and_installs_trust() {
        let temp_dir = TestDir::new();
        let paths = temp_dir.paths();
        let trust_store = FakeTrustStore::default();

        let result = ensure_local_https_identity_with(&paths, &trust_store, fixed_now()).unwrap();

        assert!(paths.public_metadata_path.exists());
        assert!(paths.encrypted_private_material_path.exists());
        assert!(paths.revocation_list_path.exists());
        assert_eq!(trust_store.install_call_count(), 1);
        assert_eq!(
            result.changes,
            LocalHttpsReconcileChanges {
                bootstrapped_identity: true,
                leaf_regenerated: false,
                trust_installed: true,
            }
        );

        let revocation_list = fs::read(&paths.revocation_list_path).unwrap();
        let (_, revocation_list) = parse_x509_crl(&revocation_list).unwrap();
        assert_eq!(revocation_list.iter_revoked_certificates().count(), 0);
        assert!(
            revocation_list
                .next_update()
                .expect("revocation list should include nextUpdate")
                .to_datetime()
                > fixed_now()
        );

        match result.state {
            LocalHttpsReconcileState::Ready(ready_identity) => {
                assert_eq!(ready_identity.server_identity.certificate_chain.len(), 2);
                assert!(matches!(
                    ready_identity.server_identity.private_key,
                    PrivateKeyDer::Pkcs8(_)
                ));
                let leaf = ready_identity.persisted_identity.leaf.unwrap();
                let parsed_leaf = parse_certificate_details(&leaf.certificate_der).unwrap();
                assert_eq!(
                    parsed_leaf.crl_distribution_point_uris,
                    required_leaf_crl_distribution_point_set(&paths).unwrap()
                );
            }
            LocalHttpsReconcileState::RepairRequired(_) => panic!("expected a ready identity"),
        }
    }

    #[test]
    fn missing_leaf_is_regenerated_without_replacing_the_ca() {
        let temp_dir = TestDir::new();
        let paths = temp_dir.paths();
        let trust_store = FakeTrustStore::default();
        let (mut public_identity, mut private_material) =
            store_generated_identity(&paths, fixed_now());
        let expected_ca_fingerprint = public_identity.ca.sha256_fingerprint.clone();
        trust_store.trust(&public_identity.ca.certificate_der);
        public_identity.leaf = None;
        private_material.leaf = None;
        write_stored_identity(&paths, &public_identity, &private_material).unwrap();

        let result = ensure_local_https_identity_with(&paths, &trust_store, fixed_now()).unwrap();

        assert!(result.changes.leaf_regenerated);
        assert!(!result.changes.bootstrapped_identity);
        match result.state {
            LocalHttpsReconcileState::Ready(ready_identity) => {
                assert_eq!(
                    ready_identity.persisted_identity.ca.sha256_fingerprint,
                    expected_ca_fingerprint
                );
                assert!(ready_identity.persisted_identity.leaf.is_some());
            }
            LocalHttpsReconcileState::RepairRequired(_) => panic!("expected a ready identity"),
        }
    }

    #[test]
    fn expired_leaf_is_regenerated() {
        let temp_dir = TestDir::new();
        let paths = temp_dir.paths();
        let trust_store = FakeTrustStore::default();
        let now = fixed_now();
        let issue_time = now - leaf_validity() - Duration::days(1);
        let (public_identity, _private_material) = store_generated_identity(&paths, issue_time);
        trust_store.trust(&public_identity.ca.certificate_der);

        let result = ensure_local_https_identity_with(&paths, &trust_store, now).unwrap();

        assert!(result.changes.leaf_regenerated);
        match result.state {
            LocalHttpsReconcileState::Ready(ready_identity) => {
                let leaf = ready_identity.persisted_identity.leaf.unwrap();
                assert!(leaf.not_after_unix > now.unix_timestamp());
            }
            LocalHttpsReconcileState::RepairRequired(_) => panic!("expected a ready identity"),
        }
    }

    #[test]
    fn leaf_renews_inside_the_30_day_window() {
        let temp_dir = TestDir::new();
        let paths = temp_dir.paths();
        let trust_store = FakeTrustStore::default();
        let now = fixed_now();
        let issue_time = now - (leaf_validity() - leaf_renewal_window() + Duration::days(1));
        let (public_identity, _private_material) = store_generated_identity(&paths, issue_time);
        trust_store.trust(&public_identity.ca.certificate_der);

        let result = ensure_local_https_identity_with(&paths, &trust_store, now).unwrap();

        assert!(result.changes.leaf_regenerated);
        match result.state {
            LocalHttpsReconcileState::Ready(ready_identity) => {
                let leaf = ready_identity.persisted_identity.leaf.unwrap();
                assert!(leaf.not_after_unix > public_identity.leaf.unwrap().not_after_unix);
            }
            LocalHttpsReconcileState::RepairRequired(_) => panic!("expected a ready identity"),
        }
    }

    #[test]
    fn missing_trust_reinstalls_the_same_ca() {
        let temp_dir = TestDir::new();
        let paths = temp_dir.paths();
        let trust_store = FakeTrustStore::default();
        let (public_identity, _private_material) = store_generated_identity(&paths, fixed_now());
        let expected_ca_fingerprint = public_identity.ca.sha256_fingerprint.clone();

        let result = ensure_local_https_identity_with(&paths, &trust_store, fixed_now()).unwrap();

        assert!(result.changes.trust_installed);
        assert_eq!(trust_store.install_call_count(), 1);
        match result.state {
            LocalHttpsReconcileState::Ready(ready_identity) => {
                assert_eq!(
                    ready_identity.persisted_identity.ca.sha256_fingerprint,
                    expected_ca_fingerprint
                );
            }
            LocalHttpsReconcileState::RepairRequired(_) => panic!("expected a ready identity"),
        }
    }

    #[test]
    fn missing_revocation_list_is_rewritten_without_regenerating_the_leaf() {
        let temp_dir = TestDir::new();
        let paths = temp_dir.paths();
        let trust_store = FakeTrustStore::default();

        let first_result =
            ensure_local_https_identity_with(&paths, &trust_store, fixed_now()).unwrap();
        assert!(matches!(
            first_result.state,
            LocalHttpsReconcileState::Ready(_)
        ));

        fs::remove_file(&paths.revocation_list_path).unwrap();
        let second_result =
            ensure_local_https_identity_with(&paths, &trust_store, fixed_now()).unwrap();

        assert!(paths.revocation_list_path.exists());
        assert!(!second_result.changes.leaf_regenerated);
        assert!(!second_result.changes.bootstrapped_identity);
    }

    #[test]
    fn legacy_leaf_without_a_crl_distribution_point_is_regenerated() {
        let temp_dir = TestDir::new();
        let paths = temp_dir.paths();
        let trust_store = FakeTrustStore::default();
        let now = fixed_now();
        let (mut public_identity, mut private_material) = store_generated_identity(&paths, now);
        let expected_ca_fingerprint = public_identity.ca.sha256_fingerprint.clone();
        trust_store.trust(&public_identity.ca.certificate_der);

        let legacy_leaf = issue_legacy_leaf_without_crl_distribution_point(
            &public_identity.ca,
            &private_material.ca,
            now,
        );
        public_identity.leaf = Some(legacy_leaf.to_certificate_record());
        private_material.leaf = Some(legacy_leaf.to_private_key_record());
        write_stored_identity(&paths, &public_identity, &private_material).unwrap();

        let result = ensure_local_https_identity_with(&paths, &trust_store, now).unwrap();

        assert!(result.changes.leaf_regenerated);
        match result.state {
            LocalHttpsReconcileState::Ready(ready_identity) => {
                assert_eq!(
                    ready_identity.persisted_identity.ca.sha256_fingerprint,
                    expected_ca_fingerprint
                );
                let leaf = ready_identity.persisted_identity.leaf.unwrap();
                let parsed_leaf = parse_certificate_details(&leaf.certificate_der).unwrap();
                assert_eq!(
                    parsed_leaf.crl_distribution_point_uris,
                    required_leaf_crl_distribution_point_set(&paths).unwrap()
                );
            }
            LocalHttpsReconcileState::RepairRequired(_) => panic!("expected a ready identity"),
        }
    }

    #[test]
    fn malformed_ca_requires_repair() {
        let temp_dir = TestDir::new();
        let paths = temp_dir.paths();
        let trust_store = FakeTrustStore::default();
        let (mut public_identity, private_material) = store_generated_identity(&paths, fixed_now());
        public_identity.ca.certificate_der = vec![1_u8, 2, 3, 4];
        public_identity.ca.sha256_fingerprint =
            sha256_fingerprint(&public_identity.ca.certificate_der);
        public_identity.ca.public_key_spki_sha256 =
            sha256_fingerprint(&public_identity.ca.certificate_der);
        public_identity.ca.not_before_unix = 0;
        public_identity.ca.not_after_unix = 0;
        write_stored_identity(&paths, &public_identity, &private_material).unwrap();

        let result = ensure_local_https_identity_with(&paths, &trust_store, fixed_now()).unwrap();

        match result.state {
            LocalHttpsReconcileState::RepairRequired(repair) => {
                assert_eq!(repair.reason, LocalHttpsRepairReason::InvalidCaMaterial);
            }
            LocalHttpsReconcileState::Ready(_) => panic!("expected repair-required result"),
        }
    }

    fn fixed_now() -> OffsetDateTime {
        OffsetDateTime::from_unix_timestamp(1_730_000_000).unwrap()
    }

    fn issue_legacy_leaf_without_crl_distribution_point(
        ca_certificate_record: &PersistedCertificateRecord,
        ca_private_key_record: &PersistedPrivateKeyRecord,
        now: OffsetDateTime,
    ) -> IssuedCertificateMaterial {
        let ca_key_pair =
            KeyPair::try_from(ca_private_key_record.private_key_pkcs8_der.clone()).unwrap();
        let ca_certificate_der =
            CertificateDer::from(ca_certificate_record.certificate_der.clone());
        let issuer = Issuer::from_ca_cert_der(&ca_certificate_der, ca_key_pair).unwrap();
        let leaf_key_pair = KeyPair::generate().unwrap();
        let leaf_private_key_pkcs8_der = leaf_key_pair.serialize_der();
        let leaf_public_key_spki = leaf_key_pair.subject_public_key_info();
        let leaf_params = legacy_leaf_certificate_params(now).unwrap();
        let leaf_certificate = leaf_params.signed_by(&leaf_key_pair, &issuer).unwrap();

        IssuedCertificateMaterial::new(
            leaf_certificate.der().as_ref().to_vec(),
            leaf_private_key_pkcs8_der,
            leaf_public_key_spki,
            leaf_params.not_before,
            leaf_params.not_after,
        )
    }

    fn legacy_leaf_certificate_params(now: OffsetDateTime) -> Result<CertificateParams, String> {
        let mut params =
            CertificateParams::new(required_leaf_subject_alt_names()).map_err(|error| {
                format!(
                    "Failed to prepare legacy local HTTPS leaf certificate parameters: {}",
                    error
                )
            })?;
        let mut distinguished_name = DistinguishedName::new();
        distinguished_name.push(DnType::CommonName, LEAF_COMMON_NAME);
        params.distinguished_name = distinguished_name;
        params.is_ca = IsCa::ExplicitNoCa;
        params.key_usages = vec![KeyUsagePurpose::DigitalSignature];
        params.extended_key_usages = vec![ExtendedKeyUsagePurpose::ServerAuth];
        params.not_before = now - certificate_backdate();
        params.not_after = now + leaf_validity();
        params.use_authority_key_identifier_extension = true;
        Ok(params)
    }

    fn store_generated_identity(
        paths: &LocalHttpsPaths,
        now: OffsetDateTime,
    ) -> (
        PersistedLocalHttpsIdentity,
        PersistedLocalHttpsPrivateMaterial,
    ) {
        let generated = generate_full_identity(paths, now).unwrap();
        write_stored_identity(
            paths,
            &generated.public_identity,
            &generated.private_material,
        )
        .unwrap();
        (generated.public_identity, generated.private_material)
    }
}
