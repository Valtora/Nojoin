#!/bin/sh

CERT_DIR="/etc/nginx/certs"
KEY_FILE="$CERT_DIR/cert.key"
CRT_FILE="$CERT_DIR/cert.crt"

if [ ! -f "$KEY_FILE" ] || [ ! -f "$CRT_FILE" ]; then
    echo "Generating self-signed SSL certificates..."
    
    # Install openssl if not present (assuming alpine)
    if ! command -v openssl >/dev/null 2>&1; then
        apk add --no-cache openssl
    fi

    openssl req -x509 -nodes -days 365 -newkey rsa:2048 \
        -keyout "$KEY_FILE" \
        -out "$CRT_FILE" \
        -subj "/C=US/ST=State/L=City/O=Organization/CN=localhost"
    
    echo "Certificates generated successfully."
    
    # Set permissions so other containers/users can read them if necessary
    chmod 644 "$CRT_FILE"
    chmod 644 "$KEY_FILE"
else
    echo "SSL certificates already exist. Skipping generation."
fi
