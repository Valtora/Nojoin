//! Log sanitisation helpers.
//!
//! These helpers are intentionally conservative: they truncate long inputs,
//! strip bearer tokens, JSON values for sensitive keys, JWT-shaped triples,
//! and long base64url-ish runs that look like opaque secrets. The goal is
//! defence-in-depth against any future code path that ends up logging an
//! HTTP response body, error string, or panic message that could contain
//! credentials.

const REDACTED: &str = "[REDACTED]";
const MAX_LEN: usize = 512;

/// JSON keys whose string values must never appear in logs.
const SENSITIVE_KEYS: &[&str] = &[
    "access_token",
    "refresh_token",
    "upload_token",
    "api_token",
    "companion_credential_secret",
    "credential_secret",
    "token",
    "secret",
    "password",
    "authorization",
    "auth",
    "key",
    "private_key",
    "session_id",
];

/// Truncate to `MAX_LEN` characters (not bytes), appending an indicator.
fn truncate(input: &str) -> String {
    let total: usize = input.chars().count();
    if total <= MAX_LEN {
        return input.to_string();
    }
    let mut out: String = input.chars().take(MAX_LEN).collect();
    out.push_str(&format!("...[truncated {} chars]", total - MAX_LEN));
    out
}

#[inline]
fn is_token_byte(b: u8) -> bool {
    matches!(b,
        b'A'..=b'Z' | b'a'..=b'z' | b'0'..=b'9' | b'-' | b'_' | b'.' | b'+' | b'/' | b'='
    )
}

#[inline]
fn is_b64url_byte(b: u8) -> bool {
    matches!(b,
        b'A'..=b'Z' | b'a'..=b'z' | b'0'..=b'9' | b'-' | b'_'
    )
}

/// Push the next char from `input` starting at byte position `i` into `out`
/// and return the byte length consumed. Safe for any UTF-8 string.
fn copy_one_char(input: &str, i: usize, out: &mut String) -> usize {
    let bytes = input.as_bytes();
    if bytes[i] < 128 {
        out.push(bytes[i] as char);
        return 1;
    }
    let mut j = i + 1;
    while j < bytes.len() && !input.is_char_boundary(j) {
        j += 1;
    }
    out.push_str(&input[i..j]);
    j - i
}

/// Redact `Bearer <token>` (case-insensitive) including the token run.
fn redact_bearer(input: &str) -> String {
    let bytes = input.as_bytes();
    let mut out = String::with_capacity(input.len());
    let mut i = 0usize;
    while i < bytes.len() {
        if i + 7 <= bytes.len() && bytes[i..i + 7].eq_ignore_ascii_case(b"Bearer ") {
            out.push_str("Bearer ");
            let mut j = i + 7;
            while j < bytes.len() && bytes[j] == b' ' {
                j += 1;
            }
            let start = j;
            while j < bytes.len() && bytes[j] < 128 && is_token_byte(bytes[j]) {
                j += 1;
            }
            if j > start {
                out.push_str(REDACTED);
                i = j;
                continue;
            }
            i = j;
            continue;
        }
        i += copy_one_char(input, i, &mut out);
    }
    out
}

/// Redact JSON string values for known sensitive keys: `"key": "value"`.
fn redact_json_values(input: &str) -> String {
    let bytes = input.as_bytes();
    let mut out = String::with_capacity(input.len());
    let mut i = 0usize;
    while i < bytes.len() {
        if bytes[i] == b'"' {
            let key_start = i + 1;
            let mut j = key_start;
            while j < bytes.len() && bytes[j] != b'"' {
                if bytes[j] == b'\\' && j + 1 < bytes.len() {
                    j += 2;
                    continue;
                }
                j += 1;
            }
            if j >= bytes.len() {
                out.push_str(&input[i..]);
                break;
            }
            let key = &input[key_start..j];
            let key_lower = key.to_ascii_lowercase();
            let is_sensitive = SENSITIVE_KEYS.iter().any(|k| {
                key_lower == *k
                    || key_lower.ends_with(&format!("_{}", k))
                    || key_lower.ends_with(&format!("-{}", k))
            });

            let mut k = j + 1;
            while k < bytes.len() && (bytes[k] == b' ' || bytes[k] == b'\t') {
                k += 1;
            }
            if is_sensitive && k < bytes.len() && bytes[k] == b':' {
                let mut v = k + 1;
                while v < bytes.len() && (bytes[v] == b' ' || bytes[v] == b'\t') {
                    v += 1;
                }
                if v < bytes.len() && bytes[v] == b'"' {
                    let val_start = v + 1;
                    let mut e = val_start;
                    while e < bytes.len() && bytes[e] != b'"' {
                        if bytes[e] == b'\\' && e + 1 < bytes.len() {
                            e += 2;
                            continue;
                        }
                        e += 1;
                    }
                    out.push_str(&input[i..val_start]);
                    out.push_str(REDACTED);
                    if e < bytes.len() {
                        out.push('"');
                        i = e + 1;
                    } else {
                        i = e;
                    }
                    continue;
                }
            }
            out.push_str(&input[i..=j]);
            i = j + 1;
            continue;
        }
        i += copy_one_char(input, i, &mut out);
    }
    out
}

/// Redact JWT-shaped triples: three base64url runs of >=20 chars each,
/// separated by single dots.
fn redact_jwts(input: &str) -> String {
    let bytes = input.as_bytes();
    let mut out = String::with_capacity(input.len());
    let mut i = 0usize;
    while i < bytes.len() {
        let preceded = i > 0 && bytes[i - 1] < 128 && is_b64url_byte(bytes[i - 1]);
        if !preceded && bytes[i] < 128 && is_b64url_byte(bytes[i]) {
            let s1 = i;
            let mut j = i;
            while j < bytes.len() && bytes[j] < 128 && is_b64url_byte(bytes[j]) {
                j += 1;
            }
            if j - s1 >= 20 && j < bytes.len() && bytes[j] == b'.' {
                let s2 = j + 1;
                let mut k = s2;
                while k < bytes.len() && bytes[k] < 128 && is_b64url_byte(bytes[k]) {
                    k += 1;
                }
                if k - s2 >= 20 && k < bytes.len() && bytes[k] == b'.' {
                    let s3 = k + 1;
                    let mut m = s3;
                    while m < bytes.len() && bytes[m] < 128 && is_b64url_byte(bytes[m]) {
                        m += 1;
                    }
                    if m - s3 >= 20 {
                        out.push_str(REDACTED);
                        i = m;
                        continue;
                    }
                }
            }
        }
        i += copy_one_char(input, i, &mut out);
    }
    out
}

/// Redact opaque-looking secrets: runs of >=40 base64url bytes. Aggressive
/// by design; may also redact long hex hashes.
fn redact_long_b64(input: &str) -> String {
    let bytes = input.as_bytes();
    let mut out = String::with_capacity(input.len());
    let mut i = 0usize;
    while i < bytes.len() {
        if bytes[i] < 128 && is_b64url_byte(bytes[i]) {
            let start = i;
            let mut j = i;
            while j < bytes.len() && bytes[j] < 128 && is_b64url_byte(bytes[j]) {
                j += 1;
            }
            if j - start >= 40 {
                out.push_str(REDACTED);
            } else {
                out.push_str(&input[start..j]);
            }
            i = j;
            continue;
        }
        i += copy_one_char(input, i, &mut out);
    }
    out
}

/// Sanitise an arbitrary string for inclusion in a log line.
pub fn sanitize_for_log(input: &str) -> String {
    let s = truncate(input);
    let s = redact_bearer(&s);
    let s = redact_json_values(&s);
    let s = redact_jwts(&s);
    redact_long_b64(&s)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn redacts_bearer() {
        let out = sanitize_for_log("Authorization: Bearer abc.def.ghijk and more");
        assert!(out.contains("Bearer [REDACTED]"));
        assert!(!out.contains("abc.def.ghijk"));
    }

    #[test]
    fn redacts_jwt_shape() {
        let jwt = format!("{}.{}.{}", "a".repeat(25), "b".repeat(25), "c".repeat(25));
        let body = format!("got token {} fine", jwt);
        let out = sanitize_for_log(&body);
        assert!(out.contains("[REDACTED]"));
        assert!(!out.contains(&jwt));
    }

    #[test]
    fn redacts_json_sensitive_value() {
        let body = r#"{"access_token": "abcdef", "user": "alice"}"#;
        let out = sanitize_for_log(body);
        assert!(out.contains("[REDACTED]"));
        assert!(!out.contains("abcdef"));
        assert!(out.contains("alice"));
    }

    #[test]
    fn redacts_long_opaque_run() {
        let body = format!("opaque: {}", "x".repeat(80));
        let out = sanitize_for_log(&body);
        assert!(out.contains("[REDACTED]"));
        assert!(!out.contains(&"x".repeat(80)));
    }

    #[test]
    fn truncates_long_input() {
        let body = "a".repeat(2000);
        let out = sanitize_for_log(&body);
        assert!(out.contains("truncated"));
        assert!(out.chars().count() < 700);
    }

    #[test]
    fn preserves_short_safe_strings() {
        let out = sanitize_for_log("status=404 path=/recordings/123/segment");
        assert_eq!(out, "status=404 path=/recordings/123/segment");
    }

    #[test]
    fn handles_unicode_bodies() {
        let out = sanitize_for_log("error: café — service unavailable");
        assert!(out.contains("café"));
    }
}
