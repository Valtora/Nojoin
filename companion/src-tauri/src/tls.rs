use rustls::client::danger::{HandshakeSignatureValid, ServerCertVerified, ServerCertVerifier};
use rustls::crypto::{
    verify_tls12_signature as verify_tls12_handshake_signature,
    verify_tls13_signature as verify_tls13_handshake_signature, WebPkiSupportedAlgorithms,
};
use rustls::pki_types::{CertificateDer, ServerName, UnixTime};
use rustls::{DigitallySignedStruct, Error as RustlsError, SignatureScheme};
use sha2::{Digest, Sha256};
use std::sync::Arc;
use std::time::Duration;
use tokio::net::TcpStream;
use tokio::time::timeout;
use tokio_rustls::TlsConnector;

const TLS_CONNECT_TIMEOUT_SECS: u64 = 5;

fn supported_signature_algorithms() -> WebPkiSupportedAlgorithms {
    rustls::crypto::ring::default_provider().signature_verification_algorithms
}

pub fn format_certificate_fingerprint(certificate: &[u8]) -> String {
    let mut hasher = Sha256::new();
    hasher.update(certificate);
    let result = hasher.finalize();

    result
        .iter()
        .map(|byte| format!("{:02X}", byte))
        .collect::<Vec<String>>()
        .join(":")
}

#[derive(Debug)]
pub struct PinnedCertVerifier {
    expected_fingerprint: Option<String>,
    signature_algorithms: WebPkiSupportedAlgorithms,
}

impl PinnedCertVerifier {
    pub fn new(expected_fingerprint: Option<String>) -> Self {
        Self {
            expected_fingerprint: expected_fingerprint
                .map(|value| value.trim().to_string())
                .filter(|value| !value.is_empty()),
            signature_algorithms: supported_signature_algorithms(),
        }
    }
}

#[derive(Debug)]
struct CaptureOnlyVerifier {
    signature_algorithms: WebPkiSupportedAlgorithms,
}

impl CaptureOnlyVerifier {
    fn new() -> Self {
        Self {
            signature_algorithms: supported_signature_algorithms(),
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
        let fingerprint = format_certificate_fingerprint(end_entity.as_ref());

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

        Err(RustlsError::General(
            "TLS fingerprint missing for paired backend".into(),
        ))
    }

    fn verify_tls12_signature(
        &self,
        message: &[u8],
        cert: &CertificateDer<'_>,
        dss: &DigitallySignedStruct,
    ) -> Result<HandshakeSignatureValid, RustlsError> {
        verify_tls12_handshake_signature(message, cert, dss, &self.signature_algorithms)
    }

    fn verify_tls13_signature(
        &self,
        message: &[u8],
        cert: &CertificateDer<'_>,
        dss: &DigitallySignedStruct,
    ) -> Result<HandshakeSignatureValid, RustlsError> {
        verify_tls13_handshake_signature(message, cert, dss, &self.signature_algorithms)
    }

    fn supported_verify_schemes(&self) -> Vec<SignatureScheme> {
        self.signature_algorithms.supported_schemes()
    }
}

impl ServerCertVerifier for CaptureOnlyVerifier {
    fn verify_server_cert(
        &self,
        _end_entity: &CertificateDer<'_>,
        _intermediates: &[CertificateDer<'_>],
        _server_name: &ServerName<'_>,
        _ocsp_response: &[u8],
        _now: UnixTime,
    ) -> Result<ServerCertVerified, RustlsError> {
        Ok(ServerCertVerified::assertion())
    }

    fn verify_tls12_signature(
        &self,
        message: &[u8],
        cert: &CertificateDer<'_>,
        dss: &DigitallySignedStruct,
    ) -> Result<HandshakeSignatureValid, RustlsError> {
        verify_tls12_handshake_signature(message, cert, dss, &self.signature_algorithms)
    }

    fn verify_tls13_signature(
        &self,
        message: &[u8],
        cert: &CertificateDer<'_>,
        dss: &DigitallySignedStruct,
    ) -> Result<HandshakeSignatureValid, RustlsError> {
        verify_tls13_handshake_signature(message, cert, dss, &self.signature_algorithms)
    }

    fn supported_verify_schemes(&self) -> Vec<SignatureScheme> {
        self.signature_algorithms.supported_schemes()
    }
}

fn create_capture_tls_config() -> rustls::ClientConfig {
    let mut config = rustls::ClientConfig::builder()
        .dangerous()
        .with_custom_certificate_verifier(Arc::new(CaptureOnlyVerifier::new()))
        .with_no_client_auth();
    config.alpn_protocols = vec![b"http/1.1".to_vec()];
    config
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

pub async fn capture_tls_fingerprint(host: &str, port: u16) -> Result<String, String> {
    let server_name = ServerName::try_from(host.to_owned())
        .map_err(|err| format!("Failed to prepare TLS server name: {}", err))?;

    let tcp_stream = match timeout(
        Duration::from_secs(TLS_CONNECT_TIMEOUT_SECS),
        TcpStream::connect((host, port)),
    )
    .await
    {
        Ok(Ok(stream)) => stream,
        Ok(Err(err)) => {
            return Err(format!(
                "Failed to connect to the pairing backend over TCP: {}",
                err
            ));
        }
        Err(_) => {
            return Err(
                "Timed out connecting to the pairing backend before TLS capture completed."
                    .to_string(),
            );
        }
    };

    let connector = TlsConnector::from(Arc::new(create_capture_tls_config()));
    let tls_stream = match timeout(
        Duration::from_secs(TLS_CONNECT_TIMEOUT_SECS),
        connector.connect(server_name, tcp_stream),
    )
    .await
    {
        Ok(Ok(stream)) => stream,
        Ok(Err(err)) => {
            return Err(format!(
                "Failed to complete the pairing TLS handshake: {}",
                err
            ));
        }
        Err(_) => {
            return Err(
                "Timed out while capturing the pairing backend TLS certificate.".to_string(),
            );
        }
    };

    let (_, connection) = tls_stream.get_ref();
    let peer_certificate = connection
        .peer_certificates()
        .and_then(|certs| certs.first())
        .ok_or_else(|| "Pairing backend did not present a TLS certificate.".to_string())?;

    Ok(format_certificate_fingerprint(peer_certificate.as_ref()))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn fingerprint_format_is_uppercase_and_colon_delimited() {
        let fingerprint = format_certificate_fingerprint(b"nojoin");

        assert_eq!(fingerprint.split(':').count(), 32);
        assert_eq!(fingerprint, fingerprint.to_ascii_uppercase());
        assert!(fingerprint
            .chars()
            .all(|value| value == ':' || value.is_ascii_hexdigit()));
    }

    #[test]
    fn pinned_verifier_accepts_matching_fingerprint() {
        let certificate = CertificateDer::from(vec![1_u8, 2, 3, 4]);
        let expected = format_certificate_fingerprint(certificate.as_ref());
        let verifier = PinnedCertVerifier::new(Some(expected));
        let server_name = ServerName::try_from("example.com").unwrap();

        let result = verifier.verify_server_cert(
            &certificate,
            &[],
            &server_name,
            &[],
            UnixTime::since_unix_epoch(Duration::from_secs(0)),
        );

        assert!(result.is_ok());
    }

    #[test]
    fn pinned_verifier_rejects_mismatched_fingerprint() {
        let certificate = CertificateDer::from(vec![5_u8, 6, 7, 8]);
        let verifier = PinnedCertVerifier::new(Some("AA:BB:CC".to_string()));
        let server_name = ServerName::try_from("example.com").unwrap();

        let result = verifier.verify_server_cert(
            &certificate,
            &[],
            &server_name,
            &[],
            UnixTime::since_unix_epoch(Duration::from_secs(0)),
        );

        assert!(matches!(result, Err(RustlsError::General(_))));
    }

    #[test]
    fn pinned_verifier_rejects_missing_fingerprint() {
        let certificate = CertificateDer::from(vec![9_u8, 10, 11, 12]);
        let verifier = PinnedCertVerifier::new(None);
        let server_name = ServerName::try_from("example.com").unwrap();

        let result = verifier.verify_server_cert(
            &certificate,
            &[],
            &server_name,
            &[],
            UnixTime::since_unix_epoch(Duration::from_secs(0)),
        );

        assert!(matches!(result, Err(RustlsError::General(_))));
    }
}
