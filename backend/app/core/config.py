import json
import os
from pathlib import Path
from typing import List, Dict, Any

class Settings:
    # Version configuration
    _version_data = {}
    _version_file_env = os.getenv("PLATFORM_VERSION_FILE")
    _candidate_roots = [
        Path(__file__).resolve().parents[i]
        for i in range(1, min(5, len(Path(__file__).resolve().parents)))
    ]
    _candidate_roots.append(Path.cwd())

    candidate_files = []
    if _version_file_env:
        candidate_files.append(Path(_version_file_env))
    candidate_files.extend(root / "platform_version.json" for root in _candidate_roots)

    for candidate in candidate_files:
        if not candidate.exists():
            continue
        try:
            with candidate.open("r", encoding="utf-8") as handle:
                _version_data = json.load(handle)
                break
        except (OSError, json.JSONDecodeError):
            continue

    APP_VERSION: str = os.getenv("APP_VERSION", _version_data.get("backend", "1.0.0"))
    FRONTEND_VERSION: str = os.getenv("FRONTEND_VERSION", _version_data.get("frontend", "1.0.0"))

    # Database configuration
    DATABASE_URL: str = os.getenv(
        "DATABASE_URL",
        "postgresql://nmapuser:nmappass@localhost:5432/networkMapper"
    )
    DATABASE_SSL_MODE: str = os.getenv("DATABASE_SSL_MODE", "require")

    # Connection pool sizing — PER uvicorn worker process.  The API
    # container runs UVICORN_WORKERS separate processes, each with its
    # own pool, so the real Postgres connection ceiling is
    #   UVICORN_WORKERS * (DB_POOL_SIZE + DB_MAX_OVERFLOW) + worker container
    # and MUST stay under the db container's max_connections.  Defaults:
    # 4*(5+10) + (5+10) = 75, comfortably under PG max_connections=120.
    DB_POOL_SIZE: int = int(os.getenv("DB_POOL_SIZE", "5"))
    DB_MAX_OVERFLOW: int = int(os.getenv("DB_MAX_OVERFLOW", "10"))
    # pool_timeout: how long a checkout blocks before raising when the pool
    # is exhausted.  Explicit + tunable so operators can fail fast under
    # burst instead of piling requests behind the post-response audit
    # writer (which briefly holds a 2nd connection per agent request).
    DB_POOL_TIMEOUT: int = int(os.getenv("DB_POOL_TIMEOUT", "30"))
    # pool_recycle: proactively retire connections older than this (seconds)
    # so a proxy / Postgres idle timeout can't hand back a dead socket.
    # pool_pre_ping already revalidates on checkout; this avoids the churn.
    DB_POOL_RECYCLE: int = int(os.getenv("DB_POOL_RECYCLE", "1800"))

    # Security settings
    SECRET_KEY: str = os.getenv("SECRET_KEY", "")
    JWT_SECRET_KEY: str = os.getenv("JWT_SECRET_KEY", "") or os.getenv("SECRET_KEY", "")
    # Audit finding C4: credential encryption (LLM provider API keys,
    # scanner integration secrets) now uses its own key separate from
    # the JWT secret.  Previously both were derived from SECRET_KEY
    # via HKDF, which meant any compromise of SECRET_KEY leaked every
    # stored credential *and* forged JWTs for every user, and
    # rotating SECRET_KEY silently invalidated every stored
    # integration.  Falls back to SECRET_KEY for backwards
    # compatibility during the deprecation window — the encryption
    # service logs a warning on startup if the dedicated key is
    # unset and the fallback is being used.
    CREDENTIAL_ENCRYPTION_KEY: str = (
        os.getenv("CREDENTIAL_ENCRYPTION_KEY", "")
        or os.getenv("SECRET_KEY", "")
    )
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "480"))  # 8 hours
    # Mandatory 2FA.  When true (the default), every user must enroll in TOTP
    # 2FA before they can reach any data endpoint — the post-login gate returns
    # 403 ``two_factor_setup_required`` until they do, and the frontend forces
    # enrollment.  Set REQUIRE_2FA=false to make 2FA opt-in (e.g. an air-gapped
    # single-operator deployment).
    REQUIRE_2FA: bool = os.getenv("REQUIRE_2FA", "true").lower() in ("1", "true", "yes", "on")
    AGENT_KEY_TTL_HOURS: int = int(os.getenv("AGENT_KEY_TTL_HOURS", "24"))
    # Hard upper bound on per-key TTL.  Operators creating an agent key
    # (or renewing one) can request any TTL up to this cap; requests
    # over it are clamped down and a warning is logged.  Default 168h
    # (7 days) — multi-day engagements are real, but past a week the
    # blast radius of a leaked key is too large for "I forgot" to be
    # an acceptable excuse.  Raise via env var for engagements that
    # genuinely need it.
    AGENT_KEY_MAX_TTL_HOURS: int = int(os.getenv("AGENT_KEY_MAX_TTL_HOURS", "168"))
    # Max bytes of raw command output stored per test execution result.
    # Default 100KB.  Configurable because some tools (Nessus, nmap scripts)
    # produce verbose output that operators may want to retain in full.
    TEST_OUTPUT_MAX_BYTES: int = int(os.getenv("TEST_OUTPUT_MAX_BYTES", str(100 * 1024)))
    # Max hosts a single in-memory report (PDF/HTML/JSON, agent/markdown zips)
    # may materialize — protects worker memory.  Raised from the old hard-coded
    # 10k now that the cap is surfaced (truncation banner/flag/header) instead
    # of silently dropping rows.  The streaming CSV inventory ignores this and
    # exports the full filtered set.
    REPORT_MAX_HOSTS: int = int(os.getenv("REPORT_MAX_HOSTS", "50000"))
    # Chunk size for the streaming CSV inventory cursor (hosts hydrated +
    # serialized per batch, bounding peak memory regardless of total rows).
    REPORT_STREAM_CHUNK: int = int(os.getenv("REPORT_STREAM_CHUNK", "500"))

    # CORS origins - read from environment variable, fall back to localhost
    @property
    def CORS_ORIGINS(self) -> List[str]:
        cors_env = os.getenv("CORS_ORIGINS")
        if cors_env:
            return [origin.strip() for origin in cors_env.split(",")]
        return [
            "https://localhost",
            "https://localhost:3000",
            "https://127.0.0.1",
        ]
    
    # File upload settings
    UPLOAD_DIR: str = os.getenv("UPLOAD_DIR", os.path.join(os.getcwd(), "uploads"))
    MAX_FILE_SIZE: int = int(os.getenv("MAX_FILE_SIZE", str(1024 * 1024 * 1024)))  # 1GB default
    UPLOAD_CHUNK_SIZE: int = int(os.getenv("UPLOAD_CHUNK_SIZE", str(5 * 1024 * 1024)))  # 5MB chunks
    INGESTION_STORAGE_DIR: str = os.getenv(
        "INGESTION_STORAGE_DIR",
        os.path.join(os.getcwd(), "uploads", "ingestion_queue")
    )
    # v2.91.1 (code review NEW F) — INGESTION_WORKERS setting removed.
    # It was read nowhere in the codebase, but operators saw the env
    # var documented and reasonably assumed setting it would scale
    # ingestion throughput.  The worker is single-process by
    # design (docker-compose.yml: ``python -m app.worker``); the
    # queue's ``SELECT … FOR UPDATE SKIP LOCKED`` claim would
    # tolerate multiple worker processes, but nothing in the deploy
    # actually starts more than one.  If a higher-throughput
    # ingestion path becomes load-bearing, scale via
    # ``docker compose up -d --scale worker=N`` after fixing the
    # database hot paths the second code review flagged (NEW C
    # dedup eager-load suppression — already shipped — and a
    # forthcoming pass on per-host vuln/scan correlation).

    # Ingestion job timeout (seconds). Jobs exceeding this are marked failed.
    INGESTION_JOB_TIMEOUT: int = int(os.getenv("INGESTION_JOB_TIMEOUT", "1800"))  # 30 minutes
    # How many times the orphan reaper will auto-requeue a job whose worker
    # died mid-parse (transient OOM/restart) before giving up and failing it.
    # The stored upload is reused, so this is a free retry — only crashes that
    # recur past the cap need a human. 0 disables auto-requeue (fail on first
    # orphan, the pre-v2.179 behavior).
    INGESTION_MAX_RETRIES: int = int(os.getenv("INGESTION_MAX_RETRIES", "2"))
    # Orphan detection window = this multiple of INGESTION_JOB_TIMEOUT of
    # heartbeat silence before a 'processing' job is treated as a dead worker's
    # leftover. A live parser heartbeats every few seconds, so even 1x is safe;
    # 1.5x keeps a comfortable margin while surfacing stuck jobs far sooner than
    # the prior 3x (which left a wedged upload invisible for ~90 min).
    INGESTION_ORPHAN_CUTOFF_MULTIPLIER: float = float(
        os.getenv("INGESTION_ORPHAN_CUTOFF_MULTIPLIER", "1.5")
    )

    # Nessus ingestion tuning
    NESSUS_COMMIT_BATCH_SIZE: int = int(os.getenv("NESSUS_COMMIT_BATCH_SIZE", "50"))
    NESSUS_PLUGIN_OUTPUT_MAX_CHARS: int = int(
        os.getenv("NESSUS_PLUGIN_OUTPUT_MAX_CHARS", str(32 * 1024))
    )
    
    # Supported file extensions for scan uploads
    ALLOWED_EXTENSIONS: List[str] = [
        ".xml",     # Nmap XML, Masscan XML, Nessus XML
        ".nessus",  # Nessus vulnerability scan files
        ".gnmap",   # Nmap grepable format
        ".json",    # Masscan JSON, Eyewitness JSON, NetExec JSON
        ".csv",     # Eyewitness CSV, DNS records CSV
        ".txt"      # Masscan list format, NetExec output
    ]

    @property
    def SQLALCHEMY_CONNECT_ARGS(self) -> Dict[str, Any]:
        """Connection arguments passed to SQLAlchemy engine."""
        if self.DATABASE_URL.startswith("sqlite"):
            return {}

        connect_args: Dict[str, Any] = {}
        if self.DATABASE_SSL_MODE and "sslmode=" not in self.DATABASE_URL:
            connect_args["sslmode"] = self.DATABASE_SSL_MODE
        return connect_args

settings = Settings()
