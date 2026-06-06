#!/bin/bash

#
# This script generates a simple self-signed SSL certificate for the BlueStick application.
#
# What it does:
# - Takes a domain name or IP address as an argument.
# - Creates a directory for the SSL certificates if it doesn't exist.
# - Generates a 2048-bit RSA private key.
# - Generates a self-signed X.509 certificate valid for 365 days.
# - The certificate is created with a Subject Alternative Name (SAN) that includes localhost, the provided domain/IP, and 127.0.0.1.
#
# How it does it:
# - Uses the `openssl` command-line tool to generate the private key and certificate.
# - The `genrsa` command is used to create the private key.
# - The `req` command is used to create the self-signed certificate with the specified subject and SAN.
#

# Simple SSL Certificate Generation for BlueStick
set -e

DOMAIN="$1"
if [[ -z "$DOMAIN" ]]; then
    echo "Usage: $0 <domain_or_ip>"
    exit 1
fi

CERT_DIR="ssl/certs"
mkdir -p "$CERT_DIR"

KEY_FILE="$CERT_DIR/networkmapper.key"
CRT_FILE="$CERT_DIR/networkmapper.crt"

echo "Generating SSL certificate for $DOMAIN..."

# Generate private key
openssl genrsa -out "$KEY_FILE" 2048
chmod 600 "$KEY_FILE"

# Build SAN entries — always include localhost and 127.0.0.1
SAN="DNS:localhost,IP:127.0.0.1"

# Detect whether the argument is an IP address or a hostname and add it
# to the appropriate SAN field (avoid duplicating localhost/127.0.0.1)
if [[ "$DOMAIN" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
    # It's an IPv4 address
    [[ "$DOMAIN" != "127.0.0.1" ]] && SAN="$SAN,IP:$DOMAIN"
elif [[ "$DOMAIN" == *:* ]]; then
    # It's an IPv6 address
    SAN="$SAN,IP:$DOMAIN"
else
    # It's a hostname
    [[ "$DOMAIN" != "localhost" ]] && SAN="$SAN,DNS:$DOMAIN"
fi

echo "SAN entries: $SAN"

# Generate self-signed certificate with SAN
openssl req -new -x509 -key "$KEY_FILE" -out "$CRT_FILE" -days 365 \
    -subj "/C=US/ST=State/L=City/O=BlueStick/CN=$DOMAIN" \
    -addext "subjectAltName=$SAN"

chmod 644 "$CRT_FILE"

echo "SSL certificate generated successfully!"
echo "Certificate: $CRT_FILE"
echo "Private key: $KEY_FILE"