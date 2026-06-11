# Build stage
FROM python:3.11-slim as builder

WORKDIR /app

# Install build dependencies
RUN apt-get update && apt-get install -y \
    gcc \
    libpq-dev \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements and install Python dependencies in a virtual environment
COPY requirements.txt .
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt

# Production stage
FROM python:3.11-slim as production

WORKDIR /app

# Install runtime dependencies: libpq5 (psycopg2), curl (health check), and
# the WeasyPrint native stack (Pango/Cairo/GObject/GDK-PixBuf + fonts) used
# by the PDF report + test-plan PDF exports.  libpangocairo pulls in
# libcairo2 + libglib2.0-0 (gobject-2.0) transitively; fonts-dejavu-core gives
# WeasyPrint a usable default font so text renders.
RUN apt-get update && apt-get install -y \
    libpq5 \
    curl \
    libpangocairo-1.0-0 \
    libpango-1.0-0 \
    libgdk-pixbuf-2.0-0 \
    libffi8 \
    libjpeg62-turbo \
    fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/* \
    && apt-get clean

# Copy virtual environment from builder stage
COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Cache-busting argument
ARG CACHE_BUST=default_value

# Use cache-bust to invalidate layers for application code
RUN echo "Cache bust: $CACHE_BUST" > /tmp/cache_bust.txt

# Copy application code (cache-busted)
COPY . .

# Create uploads directory with proper permissions
RUN mkdir -p uploads \
    && chmod 755 uploads

# Create non-root user for security.
# CR5-C2 — pin UID/GID to 999 explicitly (was `-r`, which picks the next
# free system id and only *happened* to land on 999).  Pinning makes the
# host-side `chown 999:999 uploads/ingestion_queue` remediation in
# scripts/deploy.sh + the ingestion_service error message correct and
# stable across rebuilds/base-image changes.
RUN groupadd -g 999 appgroup && useradd -u 999 -g appgroup -r appuser \
    && chown -R appuser:appgroup /app
USER appuser

# Expose port
EXPOSE 8000

# Health check - give backend time to wait for database initialization
HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

# Start the application with multiple workers for concurrent request handling.
# The worker count can be overridden via UVICORN_WORKERS env var.
# The mkdir ensures the ingestion_queue directory exists on bind-mounted volumes.
# --proxy-headers + --forwarded-allow-ips=* so X-Forwarded-For from nginx is
# honoured: without it Uvicorn trusts forwarded headers only from 127.0.0.1,
# but nginx connects over the Docker network, so request.client.host was
# nginx's IP for EVERY client — globally exploitable login throttling and
# wrong audit attribution (code-review C2).  '*' is safe only while the
# backend is unexposed (reachable solely via the nginx proxy).
CMD ["sh", "-c", "mkdir -p /app/uploads/ingestion_queue 2>/dev/null; uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers ${UVICORN_WORKERS:-4} --proxy-headers --forwarded-allow-ips=${FORWARDED_ALLOW_IPS:-*}"]