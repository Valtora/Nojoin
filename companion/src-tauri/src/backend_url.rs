#![cfg_attr(not(any(windows, test)), allow(dead_code))]

//! Validation and pinned-client construction for outbound Companion requests.
//!
//! All Companion-originated HTTPS calls to a paired Nojoin backend funnel through
//! this module. The validator enforces:
//!
//! * `https` only.
//! * Syntactically safe host (no embedded credentials, paths, queries, or
//!   whitespace).
//! * Non-zero port.
//! * Successful round-trip through `reqwest::Url::parse`.
//!
//! At pairing completion the strict allowlist is enforced via
//! [`enforce_origin_matches_target`]: the backend target submitted in the
//! pairing payload must equal the browser `Origin` header (which is the
//! operator's `WEB_APP_URL`). After pairing, steady-state calls reuse the
//! validated host that was stored in `BackendConnection`.
//!
//! Every outbound request also resolves the host once via
//! [`resolve_pinned_addrs`] and pins the resulting `SocketAddr` set into the
//! `reqwest` client through `resolve_to_addrs`. This closes the DNS-rebinding
//! window between validation and connection.

use std::net::SocketAddr;
use std::time::Duration;

use reqwest::Url;
use tokio::net::lookup_host;
use tokio::time::timeout;

const DNS_RESOLVE_TIMEOUT_SECS: u64 = 5;
const MAX_HOST_LEN: usize = 253;

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ValidatedBackendTarget {
    pub protocol: String,
    pub host: String,
    pub port: u16,
}

impl ValidatedBackendTarget {
    /// Returns the canonical `scheme://host[:port]` origin string. The port is
    /// omitted when it matches the scheme default (443 for https).
    pub fn origin(&self) -> String {
        if is_default_port(&self.protocol, self.port) {
            format!("{}://{}", self.protocol, format_host(&self.host))
        } else {
            format!(
                "{}://{}:{}",
                self.protocol,
                format_host(&self.host),
                self.port
            )
        }
    }

    /// Builds a fully qualified URL by joining `path` (which must start with
    /// `/`) onto the validated origin via `Url::parse`.
    pub fn build_url(&self, path: &str) -> Result<Url, String> {
        if !path.starts_with('/') {
            return Err(format!(
                "Backend URL path must start with '/' (received '{}').",
                path
            ));
        }
        let raw = format!(
            "{}://{}:{}{}",
            self.protocol,
            format_host(&self.host),
            self.port,
            path
        );
        Url::parse(&raw).map_err(|err| format!("Failed to build backend URL: {}", err))
    }
}

fn is_default_port(scheme: &str, port: u16) -> bool {
    matches!((scheme, port), ("https", 443) | ("http", 80))
}

fn format_host(host: &str) -> String {
    if host.contains(':') && !host.starts_with('[') {
        format!("[{}]", host)
    } else {
        host.to_string()
    }
}

/// Validates a (protocol, host, port) tuple as a syntactically safe HTTPS
/// backend target. Returns a normalized [`ValidatedBackendTarget`] on success.
pub fn validate_backend_target(
    protocol: &str,
    host: &str,
    port: u16,
) -> Result<ValidatedBackendTarget, String> {
    let scheme = protocol.trim().to_ascii_lowercase();
    if scheme != "https" {
        return Err(format!(
            "Backend protocol must be 'https' (received '{}').",
            protocol
        ));
    }

    let trimmed_host = host.trim();
    if trimmed_host.is_empty() {
        return Err("Backend host must not be empty.".to_string());
    }
    if trimmed_host.len() > MAX_HOST_LEN {
        return Err(format!(
            "Backend host exceeds {} characters.",
            MAX_HOST_LEN
        ));
    }
    if trimmed_host.chars().any(|c| {
        matches!(
            c,
            '@' | '/' | '\\' | '?' | '#' | ' ' | '\t' | '\n' | '\r' | '<' | '>' | '"'
        )
    }) {
        return Err("Backend host contains forbidden characters.".to_string());
    }

    if port == 0 {
        return Err("Backend port must be greater than zero.".to_string());
    }

    let synthesized = format!("{}://{}:{}/", scheme, format_host(trimmed_host), port);
    let parsed = Url::parse(&synthesized)
        .map_err(|err| format!("Backend target failed URL validation: {}", err))?;

    if parsed.scheme() != scheme {
        return Err("Backend scheme mismatch after URL parsing.".to_string());
    }
    if !parsed.username().is_empty() || parsed.password().is_some() {
        return Err("Backend URL must not contain credentials.".to_string());
    }
    let parsed_host = parsed
        .host_str()
        .ok_or_else(|| "Backend URL parsed without a host component.".to_string())?
        .to_ascii_lowercase();
    if parsed.port_or_known_default() != Some(port) {
        return Err("Backend port mismatch after URL parsing.".to_string());
    }

    Ok(ValidatedBackendTarget {
        protocol: scheme,
        host: parsed_host,
        port,
    })
}

/// Strict allowlist enforcement at pairing time. The payload-supplied backend
/// target must canonicalize to the same `scheme://host:port` as the browser
/// `Origin` header on the `/pair/complete` request. Because the browser is
/// loaded from the operator-configured `WEB_APP_URL`, this pins the Companion
/// to that single backend origin.
pub fn enforce_origin_matches_target(
    target: &ValidatedBackendTarget,
    origin_header: &str,
) -> Result<(), String> {
    let origin_url = Url::parse(origin_header.trim())
        .map_err(|_| "Origin header is not a valid URL.".to_string())?;

    let origin_scheme = origin_url.scheme().to_ascii_lowercase();
    let origin_host = origin_url
        .host_str()
        .ok_or_else(|| "Origin header is missing a host.".to_string())?
        .to_ascii_lowercase();
    let origin_port = origin_url
        .port_or_known_default()
        .ok_or_else(|| "Origin header is missing a port.".to_string())?;

    if origin_scheme != target.protocol
        || origin_host != target.host
        || origin_port != target.port
    {
        return Err(format!(
            "Backend target {} does not match the browser origin scheme/host/port.",
            target.origin()
        ));
    }

    Ok(())
}

/// Resolves the validated host once via the system resolver and returns the
/// concrete `SocketAddr` set. The result should be pinned into the outbound
/// `reqwest` client via [`build_pinned_client`] to defeat DNS rebinding
/// between validation and connection.
pub async fn resolve_pinned_addrs(
    target: &ValidatedBackendTarget,
) -> Result<Vec<SocketAddr>, String> {
    let host_port = format!("{}:{}", target.host, target.port);
    let resolved: Vec<SocketAddr> = timeout(
        Duration::from_secs(DNS_RESOLVE_TIMEOUT_SECS),
        lookup_host(host_port.as_str()),
    )
    .await
    .map_err(|_| format!("DNS resolution for {} timed out.", target.host))?
    .map_err(|err| format!("DNS resolution for {} failed: {}", target.host, err))?
    .collect();

    if resolved.is_empty() {
        return Err(format!(
            "DNS resolution for {} returned no addresses.",
            target.host
        ));
    }

    Ok(resolved)
}

/// Builds a `reqwest::Client` that is TLS-pinned to the supplied fingerprint
/// (when known) and DNS-pinned to the addresses produced by
/// [`resolve_pinned_addrs`]. Subsequent requests through this client cannot be
/// re-resolved to a different IP for the duration of the client's lifetime.
pub async fn build_pinned_client(
    target: &ValidatedBackendTarget,
    fingerprint: Option<String>,
) -> Result<reqwest::Client, String> {
    let addrs = resolve_pinned_addrs(target).await?;
    crate::tls::create_client_builder(fingerprint)
        .resolve_to_addrs(&target.host, &addrs)
        .build()
        .map_err(|err| format!("Failed to build pinned HTTP client: {}", err))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn validates_canonical_https_target() {
        let target = validate_backend_target("HTTPS", "Nojoin.Example.com", 14443).unwrap();
        assert_eq!(target.protocol, "https");
        assert_eq!(target.host, "nojoin.example.com");
        assert_eq!(target.port, 14443);
        assert_eq!(target.origin(), "https://nojoin.example.com:14443");
    }

    #[test]
    fn omits_default_port_in_origin() {
        let target = validate_backend_target("https", "nojoin.example.com", 443).unwrap();
        assert_eq!(target.origin(), "https://nojoin.example.com");
    }

    #[test]
    fn rejects_non_https_scheme() {
        assert!(validate_backend_target("http", "nojoin.example.com", 14443).is_err());
        assert!(validate_backend_target("ftp", "nojoin.example.com", 14443).is_err());
        assert!(validate_backend_target("", "nojoin.example.com", 14443).is_err());
    }

    #[test]
    fn rejects_zero_port() {
        assert!(validate_backend_target("https", "nojoin.example.com", 0).is_err());
    }

    #[test]
    fn rejects_empty_or_whitespace_host() {
        assert!(validate_backend_target("https", "", 14443).is_err());
        assert!(validate_backend_target("https", "   ", 14443).is_err());
    }

    #[test]
    fn rejects_hosts_with_credentials_or_path_chars() {
        for bad in [
            "evil@nojoin.example.com",
            "nojoin.example.com/extra",
            "nojoin.example.com?query",
            "nojoin.example.com#frag",
            "nojoin example.com",
            "nojoin.example.com\\bad",
        ] {
            assert!(
                validate_backend_target("https", bad, 14443).is_err(),
                "expected rejection for host '{}'",
                bad
            );
        }
    }

    #[test]
    fn rejects_overlong_host() {
        let long = "a".repeat(MAX_HOST_LEN + 1);
        assert!(validate_backend_target("https", &long, 14443).is_err());
    }

    #[test]
    fn accepts_loopback_localhost() {
        let target = validate_backend_target("https", "localhost", 14443).unwrap();
        assert_eq!(target.host, "localhost");
        assert_eq!(target.origin(), "https://localhost:14443");
    }

    #[test]
    fn accepts_ipv4_literal() {
        let target = validate_backend_target("https", "127.0.0.1", 14443).unwrap();
        assert_eq!(target.host, "127.0.0.1");
    }

    #[test]
    fn build_url_requires_leading_slash() {
        let target = validate_backend_target("https", "nojoin.example.com", 14443).unwrap();
        assert!(target.build_url("api/v1/login").is_err());
        let url = target.build_url("/api/v1/login").unwrap();
        assert_eq!(
            url.as_str(),
            "https://nojoin.example.com:14443/api/v1/login"
        );
    }

    #[test]
    fn origin_match_passes_for_localhost_pair() {
        let target = validate_backend_target("https", "localhost", 14443).unwrap();
        enforce_origin_matches_target(&target, "https://localhost:14443").unwrap();
    }

    #[test]
    fn origin_match_passes_for_remote_default_https() {
        let target = validate_backend_target("https", "nojoin.example.com", 443).unwrap();
        enforce_origin_matches_target(&target, "https://nojoin.example.com").unwrap();
    }

    #[test]
    fn origin_match_rejects_scheme_mismatch() {
        let target = validate_backend_target("https", "localhost", 14443).unwrap();
        assert!(enforce_origin_matches_target(&target, "http://localhost:14443").is_err());
    }

    #[test]
    fn origin_match_rejects_host_mismatch() {
        let target = validate_backend_target("https", "nojoin.example.com", 14443).unwrap();
        assert!(
            enforce_origin_matches_target(&target, "https://attacker.example.com:14443").is_err()
        );
    }

    #[test]
    fn origin_match_rejects_port_mismatch() {
        let target = validate_backend_target("https", "nojoin.example.com", 14443).unwrap();
        assert!(
            enforce_origin_matches_target(&target, "https://nojoin.example.com:9999").is_err()
        );
    }

    #[test]
    fn origin_match_rejects_metadata_endpoint_when_payload_targets_it() {
        // Even if payload+origin both pointed at metadata, both must equal the
        // operator-configured WEB_APP_URL. If WEB_APP_URL is a real backend,
        // the metadata host will not match and the request is rejected.
        let target = validate_backend_target("https", "nojoin.example.com", 14443).unwrap();
        assert!(
            enforce_origin_matches_target(&target, "https://169.254.169.254").is_err()
        );
    }

    #[tokio::test]
    async fn resolves_loopback_literal() {
        let target = validate_backend_target("https", "127.0.0.1", 14443).unwrap();
        let addrs = resolve_pinned_addrs(&target).await.unwrap();
        assert!(addrs.iter().any(|addr| addr.ip().is_loopback()));
    }
}
