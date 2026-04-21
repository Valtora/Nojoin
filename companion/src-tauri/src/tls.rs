use rustls::client::{HandshakeSignatureValid, ServerCertVerified, ServerCertVerifier};
use rustls::{Certificate, Error as RustlsError, ServerName};
use rustls::{DigitallySignedStruct, SignatureScheme};
use sha2::{Digest, Sha256};
use std::time::SystemTime;

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
        end_entity: &Certificate,
        _intermediates: &[Certificate],
        _server_name: &ServerName,
        _scts: &mut dyn Iterator<Item = &[u8]>,
        _ocsp_response: &[u8],
        _now: SystemTime,
    ) -> Result<ServerCertVerified, RustlsError> {
        let mut hasher = Sha256::new();
        hasher.update(&end_entity.0);
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
        _cert: &Certificate,
        _dss: &DigitallySignedStruct,
    ) -> Result<HandshakeSignatureValid, RustlsError> {
        Ok(HandshakeSignatureValid::assertion())
    }

    fn verify_tls13_signature(
        &self,
        _message: &[u8],
        _cert: &Certificate,
        _dss: &DigitallySignedStruct,
    ) -> Result<HandshakeSignatureValid, RustlsError> {
        Ok(HandshakeSignatureValid::assertion())
    }

    fn supported_verify_schemes(&self) -> Vec<SignatureScheme> {
        vec![
            SignatureScheme::RSA_PKCS1_SHA1,
            SignatureScheme::ECDSA_SHA1_Legacy,
            SignatureScheme::RSA_PKCS1_SHA256,
            SignatureScheme::ECDSA_NISTP256_SHA256,
            SignatureScheme::RSA_PKCS1_SHA384,
            SignatureScheme::ECDSA_NISTP384_SHA384,
            SignatureScheme::RSA_PKCS1_SHA512,
            SignatureScheme::ECDSA_NISTP521_SHA512,
            SignatureScheme::RSA_PSS_SHA256,
            SignatureScheme::RSA_PSS_SHA384,
            SignatureScheme::RSA_PSS_SHA512,
            SignatureScheme::ED25519,
            SignatureScheme::ED448,
        ]
    }
}

pub fn create_tls_config(fingerprint: Option<String>) -> rustls::ClientConfig {
    let mut config = rustls::ClientConfig::builder()
        .with_safe_defaults()
        .with_custom_certificate_verifier(std::sync::Arc::new(PinnedCertVerifier::new(fingerprint)))
        .with_no_client_auth();
    // Required to allow HTTP/1.1 and H2
    config.alpn_protocols = vec![b"h2".to_vec(), b"http/1.1".to_vec()];
    config
}

pub fn create_client(fingerprint: Option<String>) -> Result<reqwest::Client, reqwest::Error> {
    reqwest::Client::builder()
        .use_preconfigured_tls(create_tls_config(fingerprint))
        .build()
}
