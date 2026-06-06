#!/bin/bash

#
# This script generates a self-signed SSL certificate for the BlueStick application with customizable options.
#
# What it does:
# - Provides a command-line interface to generate a self-signed SSL certificate.
# - Allows customization of the domain/IP, Subject Alternative Names (SANs), country, state, city, organization, and other certificate details.
# - Can generate RSA keys of 2048 or 4096 bits.
# - Sets the validity period of the certificate.
# - Creates the necessary OpenSSL configuration file on the fly.
#
# How it does it:
# - Parses command-line arguments to get the desired certificate details.
# - Validates the provided options.
# - Uses `openssl` to generate a private key, a certificate signing request (CSR), and a self-signed certificate.
# - Creates a temporary OpenSSL configuration file to include the Subject Alternative Name (SAN) extension.
# - Sets appropriate file permissions for the generated key and certificate.
# - Verifies the generated certificate and displays its information.
# - Cleans up temporary files.
#

# BlueStick SSL Certificate Generation Script
# This script generates self-signed SSL certificates for HTTPS deployment

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Default values
CERT_DIR="ssl/certs"
KEY_SIZE=2048
DAYS=365
COUNTRY="US"
STATE="State"
CITY="City"
ORG="BlueStick-Organization"
OU="IT-Department"
CN=""
EMAIL=""

# Additional Subject Alternative Names
SANS=()

# Function to print colored output
print_status() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

print_success() {
    echo -e "${GREEN}[SUCCESS]${NC} $1"
}

print_warning() {
    echo -e "${YELLOW}[WARNING]${NC} $1"
}

print_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

# Function to show usage
show_usage() {
    cat << EOF
Usage: $0 [OPTIONS]

Generate self-signed SSL certificates for BlueStick

OPTIONS:
    -d, --domain DOMAIN     Primary domain/IP for certificate (required)
    -s, --san SAN          Additional Subject Alternative Name (can be used multiple times)
    -c, --country CODE     Country code (default: US)
    -st, --state STATE     State/Province (default: State)
    -l, --city CITY        City/Locality (default: City)
    -o, --org ORG          Organization name (default: BlueStick Organization)
    -ou, --unit UNIT       Organizational Unit (default: IT Department)
    -e, --email EMAIL      Email address
    -k, --key-size SIZE    RSA key size in bits (default: 2048)
    -v, --validity DAYS    Certificate validity in days (default: 365)
    --cert-dir DIR         Certificate directory (default: ssl/certs)
    -h, --help             Show this help message

EXAMPLES:
    # Generate certificate for localhost
    $0 -d localhost

    # Generate certificate for specific IP with additional domains
    $0 -d 192.168.1.100 -s localhost -s networkmapper.local

    # Generate certificate with custom organization details
    $0 -d myserver.local -c CA -st Ontario -l Toronto -o "My Company" -ou "Security Team"

    # Generate 4096-bit key valid for 2 years
    $0 -d myserver.local -k 4096 -v 730

EOF
}

# Parse command line arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        -d|--domain)
            CN="$2"
            shift 2
            ;;
        -s|--san)
            SANS+=("$2")
            shift 2
            ;;
        -c|--country)
            COUNTRY="$2"
            shift 2
            ;;
        -st|--state)
            STATE="$2"
            shift 2
            ;;
        -l|--city)
            CITY="$2"
            shift 2
            ;;
        -o|--org)
            ORG="$2"
            shift 2
            ;;
        -ou|--unit)
            OU="$2"
            shift 2
            ;;
        -e|--email)
            EMAIL="$2"
            shift 2
            ;;
        -k|--key-size)
            KEY_SIZE="$2"
            shift 2
            ;;
        -v|--validity)
            DAYS="$2"
            shift 2
            ;;
        --cert-dir)
            CERT_DIR="$2"
            shift 2
            ;;
        -h|--help)
            show_usage
            exit 0
            ;;
        *)
            print_error "Unknown option: $1"
            show_usage
            exit 1
            ;;
    esac
done

# Validate required parameters
if [[ -z "$CN" ]]; then
    print_error "Domain/IP is required. Use -d or --domain option."
    show_usage
    exit 1
fi

# Validate key size
if [[ "$KEY_SIZE" != "2048" && "$KEY_SIZE" != "4096" ]]; then
    print_error "Key size must be 2048 or 4096 bits"
    exit 1
fi

# Validate days
if [[ ! "$DAYS" =~ ^[0-9]+$ ]] || [[ "$DAYS" -lt 1 ]]; then
    print_error "Validity days must be a positive number"
    exit 1
fi

print_status "Generating SSL certificate for BlueStick"
echo
echo "Configuration:"
echo "  Domain/IP: $CN"
echo "  Additional SANs: ${SANS[*]:-none}"
echo "  Country: $COUNTRY"
echo "  State: $STATE"
echo "  City: $CITY"
echo "  Organization: $ORG"
echo "  Unit: $OU"
echo "  Email: ${EMAIL:-not set}"
echo "  Key Size: $KEY_SIZE bits"
echo "  Validity: $DAYS days"
echo "  Output Directory: $CERT_DIR"
echo

# Create certificate directory
print_status "Creating certificate directory"
mkdir -p "$CERT_DIR"

# Set file paths
KEY_FILE="$CERT_DIR/networkmapper.key"
CSR_FILE="$CERT_DIR/networkmapper.csr"
CRT_FILE="$CERT_DIR/networkmapper.crt"
CONFIG_FILE="$CERT_DIR/openssl.conf"

# Check if files already exist
if [[ -f "$KEY_FILE" || -f "$CRT_FILE" ]]; then
    print_warning "Certificate files already exist!"
    echo "Existing files:"
    [[ -f "$KEY_FILE" ]] && echo "  - $KEY_FILE"
    [[ -f "$CRT_FILE" ]] && echo "  - $CRT_FILE"
    echo
    read -p "Do you want to overwrite them? (y/N): " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        print_status "Certificate generation cancelled"
        exit 0
    fi
fi

# Generate private key
print_status "Generating $KEY_SIZE-bit RSA private key"
openssl genrsa -out "$KEY_FILE" "$KEY_SIZE"

# Set secure permissions on private key
chmod 600 "$KEY_FILE"
print_success "Private key generated: $KEY_FILE"

# Create OpenSSL configuration file
print_status "Creating OpenSSL configuration"

# Build subject string
SUBJECT="/C=$COUNTRY/ST=$STATE/L=$CITY/O=$ORG/OU=$OU/CN=$CN"
if [[ -n "$EMAIL" ]]; then
    SUBJECT="$SUBJECT/emailAddress=$EMAIL"
fi

# Create config file with SAN extensions
{
    echo "[req]"
    echo "default_bits = $KEY_SIZE"
    echo "prompt = no"
    echo "distinguished_name = req_distinguished_name"
    echo "req_extensions = v3_req"
    echo ""
    echo "[req_distinguished_name]"
    echo "C = $COUNTRY"
    echo "ST = $STATE"
    echo "L = $CITY"
    echo "O = $ORG"
    echo "OU = $OU"
    echo "CN = $CN"
} > "$CONFIG_FILE"

if [[ -n "$EMAIL" ]]; then
    echo "emailAddress = $EMAIL" >> "$CONFIG_FILE"
fi

{
    echo ""
    echo "[v3_req]"
    echo "basicConstraints = CA:FALSE"
    echo "keyUsage = nonRepudiation, digitalSignature, keyEncipherment"
    echo "extendedKeyUsage = serverAuth"
    echo "subjectAltName = @alt_names"
    echo ""
    echo "[alt_names]"
} >> "$CONFIG_FILE"

# Add primary CN as first SAN
if [[ "$CN" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
    echo "IP.1 = $CN" >> "$CONFIG_FILE"
    SAN_COUNT=1
    DNS_COUNT=0
else
    echo "DNS.1 = $CN" >> "$CONFIG_FILE"
    SAN_COUNT=0
    DNS_COUNT=1
fi

# Add additional SANs
for san in "${SANS[@]}"; do
    if [[ "$san" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
        # IP address
        ((SAN_COUNT++))
        echo "IP.$SAN_COUNT = $san" >> "$CONFIG_FILE"
    else
        # DNS name
        ((DNS_COUNT++))
        echo "DNS.$DNS_COUNT = $san" >> "$CONFIG_FILE"
    fi
done

# Add common defaults if not already included
if [[ "$CN" != "localhost" ]] && [[ ! " ${SANS[*]} " =~ " localhost " ]]; then
    ((DNS_COUNT++))
    echo "DNS.$DNS_COUNT = localhost" >> "$CONFIG_FILE"
fi

if [[ "$CN" != "127.0.0.1" ]] && [[ ! " ${SANS[*]} " =~ " 127.0.0.1 " ]]; then
    ((SAN_COUNT++))
    echo "IP.$SAN_COUNT = 127.0.0.1" >> "$CONFIG_FILE"
fi

print_success "OpenSSL configuration created: $CONFIG_FILE"

# Generate certificate signing request
print_status "Generating Certificate Signing Request (CSR)"
openssl req -new -key "$KEY_FILE" -out "$CSR_FILE" -config "$CONFIG_FILE"
print_success "CSR generated: $CSR_FILE"

# Generate self-signed certificate
print_status "Generating self-signed certificate (valid for $DAYS days)"
openssl x509 -req -days "$DAYS" -in "$CSR_FILE" -signkey "$KEY_FILE" -out "$CRT_FILE" \
    -extensions v3_req -extfile "$CONFIG_FILE"

# Set proper permissions
chmod 644 "$CRT_FILE"
print_success "Certificate generated: $CRT_FILE"

# Verify certificate
print_status "Verifying certificate"
if openssl x509 -in "$CRT_FILE" -text -noout > /dev/null 2>&1; then
    print_success "Certificate verification passed"
else
    print_error "Certificate verification failed"
    exit 1
fi

# Show certificate information
print_status "Certificate Information:"
echo
openssl x509 -in "$CRT_FILE" -text -noout | grep -A1 "Subject:"
openssl x509 -in "$CRT_FILE" -text -noout | grep -A1 "Subject Alternative Name:" || echo "No Subject Alternative Names"
echo
openssl x509 -in "$CRT_FILE" -noout -dates

# Clean up temporary files
rm -f "$CSR_FILE" "$CONFIG_FILE"

print_success "SSL certificate generation completed!"
echo
echo "Generated files:"
echo "  Private Key: $KEY_FILE"
echo "  Certificate: $CRT_FILE"
echo
echo "Next steps:"
echo "1. Deploy with SSL: docker-compose -f docker-compose.yml -f docker-compose.ssl.yml up -d"
echo "2. Access via HTTPS: https://$CN"
echo "3. Accept the self-signed certificate warning in your browser"
echo
print_warning "Note: Browsers will show a security warning for self-signed certificates."
print_warning "For production use, consider using Let's Encrypt or a commercial CA."