# Security Policy

The Nojoin team and community take all security vulnerabilities seriously. Thank you for your efforts to improve the security of Nojoin. We appreciate your efforts and responsible disclosure and will make every effort to acknowledge your contributions.

## Active Development

Nojoin is still in active development and all releases should be considered pre-release. There may be security vulnerabilities in the application. Nojoin's maintainers are not responsible for any data loss or security breaches that may occur as a result of using the application. We also advise users to take additional security measures in general but especially when deploying Nojoin over a publically accessible URL. For example, we recommend using a VPN or a reverse proxy to secure your Nojoin instance.

## First-Run Bootstrap Protection

Nojoin requires an operator-defined `FIRST_RUN_PASSWORD` before the first successful system initialisation can occur.

- The bootstrap password is only used while the system is uninitialised.
- The web client sends the bootstrap secret via the standard `Authorization` header using the `Bootstrap` scheme rather than a bespoke setup header.
- If `FIRST_RUN_PASSWORD` is missing, first-run setup fails closed until the operator sets it and restarts or redeploys.
- After initialisation, normal authenticated admin operations do not use the bootstrap password path.
- The bootstrap password is never returned by the API or persisted into Nojoin configuration.
- Application log output redacts `Authorization`, cookies, bootstrap credentials, passwords, tokens, and API-key fields if they are accidentally included in a log record.

Operators should treat `FIRST_RUN_PASSWORD` as a secret and ensure reverse proxies, ingress layers, and HTTP logging do not record `Authorization` headers or setup request bodies.

## Companion Pairing and Local API Security

The Nojoin Companion app requires a strict manual pairing workflow.

- The Companion exposes a short-lived local API only for authenticated requests.
- Anonymous discovery of the Companion via the loopback interface is explicitly blocked. The frontend cannot silently detect if the Companion is running.
- Pairing must be manually initiated by the user from within the Companion app, which generates a single-use, short-lived 8-character pairing code.
- During pairing, the Companion captures and pins the first backend TLS certificate it sees for that backend target.
- Re-pairing to a different Nojoin backend replaces the previous trust relationship atomically.
- After pairing, all Companion-to-backend HTTPS traffic requires the pinned backend certificate. If the backend certificate changes, the Companion must be explicitly re-paired.
- Explicitly disconnecting the current backend from Companion Settings clears the saved backend certificate pin and returns the app to a clean first-pair state.
- All requests to the Companion's local API require a short-lived local control token and strict Host validation (e.g. `127.0.0.1` or `localhost`).

Operators and users should be aware that switching between deployments requires an explicit re-pair.

## Supported Versions

As Nojoin is in active development, only the latest version is supported. We encourage all users to use the most up-to-date version of the application.

| Version | Supported          |
| ------- | ------------------ |
| latest  | :white_check_mark: |

## Reporting a Vulnerability

Please report any security vulnerabilities.

You should expect a response within 48 hours. If the issue is confirmed, we will release a patch as soon as possible, depending on the complexity of the issue.
