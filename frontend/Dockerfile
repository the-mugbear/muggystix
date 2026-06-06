# Build stage
# node:22-alpine ships npm 10.x which has the optional-deps install bug
# (npm/cli#4828) largely fixed.  Node 18 is past EOL (April 2025) and
# its bundled npm 9.x intermittently fails to install platform-specific
# native binaries — see v4.7.3 attempted-fix history.
FROM node:22-alpine AS builder

WORKDIR /app

# Install git for version info
RUN apk add --no-cache git

# Copy package files for dependency caching
COPY package*.json ./
COPY generate-build-info.js ./

# Install all dependencies (including dev dependencies for build).
# Layered defense against npm bug #4828 ("Cannot find native binding"):
#   1. Delete the in-container lockfile (repo copy untouched — SBOM
#      still sources from it) so npm does a fresh per-platform resolve
#      driven by package.json's version ranges.
#   2. Pass --include=optional explicitly even though it's the default;
#      some npm versions treat optional deps as best-effort otherwise.
#   3. Verify the Tailwind v4 oxide binding actually loads.  If it
#      doesn't, fall back to an explicit install of the matching
#      platform-specific package (auto-detected via process.arch on
#      Alpine = musl).  Failing fast HERE with a clear message beats
#      letting Vite crash 8 layers later with a generic stack trace.
RUN rm -f package-lock.json \
    && npm install --include=optional --no-audit --no-fund \
    && npm cache clean --force \
    && ( node -e "require('@tailwindcss/oxide')" \
         || ( echo "[Dockerfile] @tailwindcss/oxide native binding missing after install — repairing via explicit platform install (npm bug #4828 fallback)" \
              && ARCH="$(node -e 'process.stdout.write(process.arch)')" \
              && OXIDE_PKG="@tailwindcss/oxide-linux-${ARCH}-musl" \
              && ROLLUP_PKG="@rollup/rollup-linux-${ARCH}-musl" \
              && echo "[Dockerfile] Installing ${OXIDE_PKG} + ${ROLLUP_PKG}" \
              && npm install --no-save --include=optional --force "${OXIDE_PKG}" "${ROLLUP_PKG}" \
              && node -e "require('@tailwindcss/oxide')" ) )

# Cache-busting argument
ARG CACHE_BUST=default_value

# API URL and version arguments for build-time environment variables
ARG REACT_APP_API_URL
ARG BACKEND_VERSION
# CR4-8 — platform_version.json isn't in the frontend-only build context, so
# the frontend version is passed in here; generate-build-info.js reads it.
ARG FRONTEND_VERSION

# Set environment variables for build process
ENV REACT_APP_API_URL=$REACT_APP_API_URL
ENV BACKEND_VERSION=$BACKEND_VERSION
ENV FRONTEND_VERSION=$FRONTEND_VERSION

# Backward compatibility for scripts expecting REACT_APP_BACKEND_VERSION
ENV REACT_APP_BACKEND_VERSION=$BACKEND_VERSION

# Debug: Show environment variables during build
RUN echo "Build-time environment variables:" && \
    echo "REACT_APP_API_URL: $REACT_APP_API_URL" && \
    echo "BACKEND_VERSION: $BACKEND_VERSION" && \
    env | grep REACT_APP || echo "No REACT_APP variables found"

# Use cache-bust to invalidate layers for source code
RUN echo "Cache bust: $CACHE_BUST" > /tmp/cache_bust.txt

# Copy source code (cache-busted)
COPY . .

# Generate build info and build the application
RUN node generate-build-info.js \
    && npm run build \
    && npm prune --production

# Production stage
FROM nginx:alpine AS production

# Install security updates and curl for health check
RUN apk upgrade --no-cache \
    && apk add --no-cache curl

# Copy build files (nginx config is mounted by docker-compose at runtime)
COPY --from=builder /app/build /usr/share/nginx/html

# Ensure proper permissions for nginx
RUN chmod -R 755 /usr/share/nginx/html

# Expose port
EXPOSE 3000

# Health check - HTTPS only
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD curl -f -k https://localhost:443/ || exit 1

# Start nginx
CMD ["nginx", "-g", "daemon off;"]
