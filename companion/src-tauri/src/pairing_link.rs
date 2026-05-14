use base64::engine::general_purpose::URL_SAFE_NO_PAD;
use base64::Engine;
use ed25519_dalek::{Signature, Verifier, VerifyingKey};
use reqwest::Url;
use sha2::{Digest, Sha256};
use std::collections::HashMap;
use std::time::{SystemTime, UNIX_EPOCH};

use crate::backend_url::{validate_backend_target, ValidatedBackendTarget};

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct PairingLaunchRequest {
    pub request_id: String,
    pub request_secret: String,
    pub backend_origin: String,
    pub username: String,
    pub replacement: bool,
    pub expires_at_epoch: u64,
    pub backend_identity_key_id: String,
    pub backend_identity_public_key: String,
}

fn canonical_signature_message(fields: &[(&str, &str)]) -> Vec<u8> {
    let mut parts: Vec<String> = fields
        .iter()
        .map(|(key, value)| format!("{}={}", key, value))
        .collect();
    parts.sort();
    parts.join("\n").into_bytes()
}

fn required_query_value<'a>(query: &'a HashMap<String, String>, key: &str) -> Result<&'a str, String> {
    query
        .get(key)
        .map(String::as_str)
        .map(str::trim)
        .filter(|value| !value.is_empty())
        .ok_or_else(|| format!("Pairing link is missing '{}'", key))
}

fn parse_backend_target(origin: &str) -> Result<(ValidatedBackendTarget, String), String> {
    let parsed = Url::parse(origin).map_err(|err| format!("Invalid backend origin: {}", err))?;
    let host = parsed
        .host_str()
        .ok_or_else(|| "Pairing link backend origin is missing a host.".to_string())?;
    let port = parsed
        .port_or_known_default()
        .ok_or_else(|| "Pairing link backend origin is missing a port.".to_string())?;
    let target = validate_backend_target(parsed.scheme(), host, port)?;

    if parsed.path() != "/" && !parsed.path().is_empty() {
        return Err("Pairing link backend origin must not include a path.".to_string());
    }
    if parsed.query().is_some() || parsed.fragment().is_some() {
        return Err("Pairing link backend origin must not include a query or fragment.".to_string());
    }

    Ok((target.clone(), target.origin()))
}

fn verify_signature(
    public_key: &str,
    signature: &str,
    fields: &[(&str, &str)],
) -> Result<(), String> {
    let public_key_bytes = URL_SAFE_NO_PAD
        .decode(public_key)
        .map_err(|err| format!("Pairing link public key is invalid: {}", err))?;
    let signature_bytes = URL_SAFE_NO_PAD
        .decode(signature)
        .map_err(|err| format!("Pairing link signature is invalid: {}", err))?;
    let verifying_key = VerifyingKey::from_bytes(
        &public_key_bytes
            .try_into()
            .map_err(|_| "Pairing link public key has the wrong length.".to_string())?,
    )
    .map_err(|err| format!("Pairing link public key could not be loaded: {}", err))?;
    let signature = Signature::from_slice(&signature_bytes)
        .map_err(|err| format!("Pairing link signature could not be loaded: {}", err))?;
    verifying_key
        .verify(&canonical_signature_message(fields), &signature)
        .map_err(|_| "Pairing link signature verification failed.".to_string())
}

fn verify_key_id(expected_key_id: &str, public_key: &str) -> Result<(), String> {
    let public_key_bytes = URL_SAFE_NO_PAD
        .decode(public_key)
        .map_err(|err| format!("Pairing link public key is invalid: {}", err))?;
    let actual_key_id = format!("{:x}", Sha256::digest(public_key_bytes));
    if &actual_key_id[..16] != expected_key_id {
        return Err("Pairing link backend identity key id does not match its public key.".to_string());
    }
    Ok(())
}

impl PairingLaunchRequest {
    pub fn parse(raw_url: &str) -> Result<(Self, ValidatedBackendTarget), String> {
        let parsed = Url::parse(raw_url).map_err(|err| format!("Invalid pairing link: {}", err))?;
        if parsed.scheme() != "nojoin" {
            return Err("Pairing link must use the nojoin:// scheme.".to_string());
        }
        if parsed.host_str() != Some("pair") {
            return Err("Pairing link must target nojoin://pair.".to_string());
        }

        let query: HashMap<String, String> = parsed.query_pairs().into_owned().collect();
        let version = required_query_value(&query, "version")?;
        if version != "1" {
            return Err(format!("Unsupported pairing link version '{}'.", version));
        }

        let request_id = required_query_value(&query, "request_id")?.to_string();
        let request_secret = required_query_value(&query, "request_secret")?.to_string();
        let backend_origin = required_query_value(&query, "backend_origin")?.to_string();
        let username = required_query_value(&query, "username")?.to_string();
        let replacement = matches!(required_query_value(&query, "replacement")?, "1" | "true");
        let expires_at = required_query_value(&query, "expires_at")?
            .parse::<u64>()
            .map_err(|_| "Pairing link expiry is invalid.".to_string())?;
        let backend_identity_key_id = required_query_value(&query, "key_id")?.to_string();
        let backend_identity_public_key = required_query_value(&query, "public_key")?.to_string();
        let signature = required_query_value(&query, "signature")?;

        verify_key_id(&backend_identity_key_id, &backend_identity_public_key)?;
        verify_signature(
            &backend_identity_public_key,
            signature,
            &[
                ("backend_origin", &backend_origin),
                ("expires_at", required_query_value(&query, "expires_at")?),
                ("key_id", &backend_identity_key_id),
                ("replacement", required_query_value(&query, "replacement")?),
                ("request_id", &request_id),
                ("request_secret", &request_secret),
                ("username", &username),
                ("version", version),
            ],
        )?;

        let (backend_target, canonical_origin) = parse_backend_target(&backend_origin)?;

        Ok((
            Self {
                request_id,
                request_secret,
                backend_origin: canonical_origin,
                username,
                replacement,
                expires_at_epoch: expires_at,
                backend_identity_key_id,
                backend_identity_public_key,
            },
            backend_target,
        ))
    }

    pub fn is_expired(&self) -> bool {
        SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .map(|duration| duration.as_secs() >= self.expires_at_epoch)
            .unwrap_or(true)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn pairing_link_rejects_wrong_scheme() {
        let error = PairingLaunchRequest::parse("https://pair?request_id=x")
            .err()
            .unwrap();
        assert!(error.contains("nojoin:// scheme"));
    }
}