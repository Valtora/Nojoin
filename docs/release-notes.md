# Release v0.5.8

## Security Enhancements

- **Strict Path Sanitization**: Addressed potential path injection and information exposure vulnerabilities by enforcing strict path validation on API endpoints.
- **Regex Safety**: Mitigated Regex Denial of Service (ReDoS) vulnerabilities in the find/replace functionality by implementing `google-re2`.
- **Companion Security**: Resolved an integer overflow vulnerability in the `bytes` crate dependency within the Companion App.

## Bug Fixes & Improvements

- **Audio Playback**: Fixed an issue causing loud static noise during speaker snippet playback.
- **Build**: Updated `next.js` to version 16.1.5 for improved performance and stability.
- **Code Quality**: Applied explicit integer casting for sequence parameters to improve type safety and prevent potential runtime errors.
