use crate::config::{BackendConnection, Config};
use serde::{Deserialize, Serialize};
use std::fs;
use std::path::{Path, PathBuf};

const SECRET_STORE_DIR: &str = "secrets";
const SECRET_FILE_EXTENSION: &str = "bin";
const BACKEND_SECRET_DESCRIPTION: &str = "Nojoin Companion Backend Secret";

#[derive(Clone, Debug, Serialize, Deserialize, PartialEq, Eq)]
pub struct BackendSecretBundle {
    pub companion_credential_secret: String,
    pub local_control_secret: String,
}

impl BackendSecretBundle {
    fn normalized(&self) -> Option<Self> {
        let companion_credential_secret = self.companion_credential_secret.trim();
        let local_control_secret = self.local_control_secret.trim();

        if companion_credential_secret.is_empty() || local_control_secret.is_empty() {
            return None;
        }

        Some(Self {
            companion_credential_secret: companion_credential_secret.to_string(),
            local_control_secret: local_control_secret.to_string(),
        })
    }
}

fn secret_store_dir() -> PathBuf {
    Config::get_app_data_dir().join(SECRET_STORE_DIR)
}

fn secret_file_path(pairing_id: &str) -> PathBuf {
    secret_store_dir().join(format!("{}.{}", pairing_id, SECRET_FILE_EXTENSION))
}

fn normalized_pairing_id(pairing_id: &str) -> Result<String, String> {
    let normalized = pairing_id.trim();
    if normalized.is_empty() {
        return Err("Backend pairing id is required to access companion secrets.".to_string());
    }

    Ok(normalized.to_string())
}

fn backend_pairing_id(backend: &BackendConnection) -> Result<String, String> {
    backend
        .backend_pairing_id
        .as_deref()
        .ok_or_else(|| "Backend pairing id is missing from the paired backend state.".to_string())
        .and_then(normalized_pairing_id)
}

pub fn load_backend_secret_bundle(pairing_id: &str) -> Result<BackendSecretBundle, String> {
    let normalized_pairing_id = normalized_pairing_id(pairing_id)?;
    let path = secret_file_path(&normalized_pairing_id);
    let protected_bytes = fs::read(&path).map_err(|error| {
        format!(
            "Failed to read backend secret bundle {}: {}",
            path.display(),
            error
        )
    })?;
    let decrypted = unprotect_bytes(&protected_bytes)?;
    let bundle: BackendSecretBundle = serde_json::from_slice(&decrypted).map_err(|error| {
        format!(
            "Failed to parse backend secret bundle {}: {}",
            path.display(),
            error
        )
    })?;

    bundle.normalized().ok_or_else(|| {
        format!(
            "Backend secret bundle {} is incomplete or invalid.",
            path.display()
        )
    })
}

pub fn load_backend_secret_bundle_for_backend(
    backend: &BackendConnection,
) -> Result<BackendSecretBundle, String> {
    let pairing_id = backend_pairing_id(backend)?;
    load_backend_secret_bundle(&pairing_id)
}

pub fn save_backend_secret_bundle(
    pairing_id: &str,
    bundle: &BackendSecretBundle,
) -> Result<(), String> {
    let normalized_pairing_id = normalized_pairing_id(pairing_id)?;
    let normalized_bundle = bundle
        .normalized()
        .ok_or_else(|| "Companion secret bundle is incomplete and cannot be saved.".to_string())?;
    let path = secret_file_path(&normalized_pairing_id);

    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent).map_err(|error| {
            format!(
                "Failed to create companion secret store directory {}: {}",
                parent.display(),
                error
            )
        })?;
    }

    let serialized = serde_json::to_vec(&normalized_bundle)
        .map_err(|error| format!("Failed to serialize backend secret bundle: {}", error))?;
    let protected = protect_bytes_with_description(BACKEND_SECRET_DESCRIPTION, &serialized)?;
    let temp_path = temp_write_path(&path);

    fs::write(&temp_path, protected).map_err(|error| {
        format!(
            "Failed to write temporary backend secret bundle {}: {}",
            temp_path.display(),
            error
        )
    })?;

    replace_file(&temp_path, &path)
}

pub fn save_backend_secret_bundle_for_backend(
    backend: &BackendConnection,
    bundle: &BackendSecretBundle,
) -> Result<(), String> {
    let pairing_id = backend_pairing_id(backend)?;
    save_backend_secret_bundle(&pairing_id, bundle)
}

pub fn delete_backend_secret_bundle(pairing_id: &str) -> Result<(), String> {
    let normalized_pairing_id = normalized_pairing_id(pairing_id)?;
    let path = secret_file_path(&normalized_pairing_id);
    if !path.exists() {
        return Ok(());
    }

    fs::remove_file(&path).map_err(|error| {
        format!(
            "Failed to delete backend secret bundle {}: {}",
            path.display(),
            error
        )
    })
}

pub fn delete_backend_secret_bundle_for_backend(backend: &BackendConnection) -> Result<(), String> {
    let pairing_id = backend_pairing_id(backend)?;
    delete_backend_secret_bundle(&pairing_id)
}

pub(crate) fn temp_write_path(path: &Path) -> PathBuf {
    let mut temp_path = path.to_path_buf();
    temp_path.set_extension("tmp");
    temp_path
}

pub(crate) fn replace_file(source: &Path, target: &Path) -> Result<(), String> {
    if target.exists() {
        fs::remove_file(target).map_err(|error| {
            format!(
                "Failed to replace backend secret bundle {}: {}",
                target.display(),
                error
            )
        })?;
    }

    fs::rename(source, target).map_err(|error| {
        format!(
            "Failed to move backend secret bundle into place {}: {}",
            target.display(),
            error
        )
    })
}

#[cfg(windows)]
pub(crate) fn protect_bytes_with_description(
    description: &str,
    data: &[u8],
) -> Result<Vec<u8>, String> {
    use std::ptr;
    use windows_sys::Win32::Foundation::LocalFree;
    use windows_sys::Win32::Security::Cryptography::{
        CryptProtectData, CRYPTPROTECT_UI_FORBIDDEN, CRYPT_INTEGER_BLOB,
    };

    let description: Vec<u16> = format!("{}\0", description).encode_utf16().collect();
    let mut input = CRYPT_INTEGER_BLOB {
        cbData: data.len() as u32,
        pbData: data.as_ptr() as *mut u8,
    };
    let mut output = CRYPT_INTEGER_BLOB {
        cbData: 0,
        pbData: ptr::null_mut(),
    };

    let result = unsafe {
        CryptProtectData(
            &mut input,
            description.as_ptr(),
            ptr::null(),
            ptr::null_mut(),
            ptr::null_mut(),
            CRYPTPROTECT_UI_FORBIDDEN,
            &mut output,
        )
    };
    if result == 0 {
        return Err(format!(
            "DPAPI failed to protect companion secret data: {}",
            std::io::Error::last_os_error()
        ));
    }

    let bytes =
        unsafe { std::slice::from_raw_parts(output.pbData, output.cbData as usize).to_vec() };
    unsafe {
        LocalFree(output.pbData.cast());
    }
    Ok(bytes)
}

#[cfg(windows)]
pub(crate) fn unprotect_bytes(data: &[u8]) -> Result<Vec<u8>, String> {
    use std::ptr;
    use windows_sys::Win32::Foundation::LocalFree;
    use windows_sys::Win32::Security::Cryptography::{
        CryptUnprotectData, CRYPTPROTECT_UI_FORBIDDEN, CRYPT_INTEGER_BLOB,
    };

    let mut input = CRYPT_INTEGER_BLOB {
        cbData: data.len() as u32,
        pbData: data.as_ptr() as *mut u8,
    };
    let mut output = CRYPT_INTEGER_BLOB {
        cbData: 0,
        pbData: ptr::null_mut(),
    };
    let mut description_ptr = ptr::null_mut();

    let result = unsafe {
        CryptUnprotectData(
            &mut input,
            &mut description_ptr,
            ptr::null(),
            ptr::null_mut(),
            ptr::null_mut(),
            CRYPTPROTECT_UI_FORBIDDEN,
            &mut output,
        )
    };
    if result == 0 {
        return Err(format!(
            "DPAPI failed to decrypt companion secret data: {}",
            std::io::Error::last_os_error()
        ));
    }

    let bytes =
        unsafe { std::slice::from_raw_parts(output.pbData, output.cbData as usize).to_vec() };
    unsafe {
        if !description_ptr.is_null() {
            LocalFree(description_ptr.cast());
        }
        LocalFree(output.pbData.cast());
    }
    Ok(bytes)
}

#[cfg(not(windows))]
pub(crate) fn protect_bytes_with_description(
    _description: &str,
    data: &[u8],
) -> Result<Vec<u8>, String> {
    Ok(data.to_vec())
}

#[cfg(not(windows))]
pub(crate) fn unprotect_bytes(data: &[u8]) -> Result<Vec<u8>, String> {
    Ok(data.to_vec())
}
