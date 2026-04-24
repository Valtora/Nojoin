use rustls::client::danger::{HandshakeSignatureValid, ServerCertVerified, ServerCertVerifier};
use rustls::pki_types::{CertificateDer, ServerName, UnixTime};
use rustls::{DigitallySignedStruct, Error as RustlsError, SignatureScheme};
use sha2::{Digest, Sha256};
use std::sync::Arc;
use std::time::Duration;

#[derive(Debug)]
pub struct PinnedCertVerifier {
    expected_fingerprint: Option<String>,
}

impl PinnedCertVerifier {
    pub fn new(expected_fingerprint: Option<String>) -> Self {
        Self {
            expected_fingerprint,
        }
    }
}

impl ServerCertVerifier for PinnedCertVerifier {
    fn verify_server_cert(
        &self,
        end_entity: &CertificateDer<'_>,
        _intermediates: &[CertificateDer<'_>],
        _server_name: &ServerName<'_>,
        _ocsp_response: &[u8],
        _now: UnixTime,
    ) -> Result<ServerCertVerified, RustlsError> {
        let mut hasher = Sha256::new();
        hasher.update(end_entity.as_ref());
        let result = hasher.finalize();

        let fingerprint = result
            .iter()
            .map(|b| format!("{:02X}", b))
            .collect::<Vec<String>>()
            .join(":");

        if let Some(expected) = &self.expected_fingerprint {
            if fingerprint.eq_ignore_ascii_case(expected) {
                return Ok(ServerCertVerified::assertion());
            }
            log::error!(
                "TLS Certificate pinning failure! Expected: {}, Found: {}",
                expected,
                fingerprint
            );
            return Err(RustlsError::General(
                "Certificate fingerprint mismatch".into(),
            ));
        }

        // If no fingerprint is pinned, we allow it (Fallback or unconfigured state).
        Ok(ServerCertVerified::assertion())
    }

    fn verify_tls12_signature(
        &self,
        _message: &[u8],
        _cert: &CertificateDer<'_>,
        _dss: &DigitallySignedStruct,
    ) -> Result<HandshakeSignatureValid, RustlsError> {
        Ok(HandshakeSignatureValid::assertion())
    }

    fn verify_tls13_signature(
        &self,
        _message: &[u8],
        _cert: &CertificateDer<'_>,
        _dss: &DigitallySignedStruct,
    ) -> Result<HandshakeSignatureValid, RustlsError> {
        Ok(HandshakeSignatureValid::assertion())
    }

    fn supported_verify_schemes(&self) -> Vec<SignatureScheme> {
        rustls::crypto::ring::default_provider()
            .signature_verification_algorithms
            .supported_schemes()
    }
}

pub fn create_tls_config(fingerprint: Option<String>) -> rustls::ClientConfig {
    let mut config = rustls::ClientConfig::builder()
        .dangerous()
        .with_custom_certificate_verifier(Arc::new(PinnedCertVerifier::new(fingerprint)))
        .with_no_client_auth();
    // Pairing and backend control requests do not need HTTP/2, and forcing HTTP/1.1
    // avoids an extra transport variable while debugging Cloudflare-fronted handshakes.
    config.alpn_protocols = vec![b"http/1.1".to_vec()];
    config
}

pub fn create_client_builder(fingerprint: Option<String>) -> reqwest::ClientBuilder {
    reqwest::Client::builder()
        .use_preconfigured_tls(create_tls_config(fingerprint))
        .http1_only()
        .no_proxy()
        .connect_timeout(Duration::from_secs(5))
        .user_agent(concat!("Nojoin-Companion/", env!("CARGO_PKG_VERSION")))
}

pub fn create_client(fingerprint: Option<String>) -> Result<reqwest::Client, reqwest::Error> {
    create_client_builder(fingerprint).build()
}
