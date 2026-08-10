# Database Storage and Encryption Security Audit

**Application:** BlueStick / NetworkMapper  
**Audit date:** 2026-06-11  
**Status:** Draft for review and remediation  
**Scope:** Database models, application cryptography, Docker persistence, PostgreSQL configuration, uploads, and database backups

## Executive Summary

BlueStick stores PostgreSQL data in a persistent Docker named volume. The database
container is not disposable storage, but the volume is not encrypted by Docker or
PostgreSQL. Most assessment data is intentionally stored as plaintext so the
application can search, correlate, and report on it.

Application-level Fernet encryption is limited to external credentials:

- LLM provider API keys
- Scanner and integration secrets
- Webhook signing secrets

User passwords are bcrypt hashes, and agent API keys are stored as SHA-256 hashes.
These are one-way protections rather than encryption.

The most important risks found are:

1. The application connects using a PostgreSQL superuser and this deployment falls
   back to the documented `nmapuser` / `nmappass` credentials.
2. JWT signing and credential encryption currently share the same effective secret.
3. That secret is unnecessarily injected into the internet-facing frontend
   container.
4. Database backups and raw scanner uploads are unencrypted and readable by other
   local host users.
5. Raw commands and scanner evidence are stored as plaintext and may include
   credentials or other sensitive engagement data.

The PostgreSQL port is not published to the host, live backend connections use TLS,
and PostgreSQL password authentication uses SCRAM-SHA-256. These are meaningful
existing protections, but they do not mitigate a compromised application container,
Docker daemon, or host.

## Audit Method

The review included:

- SQLAlchemy models and Alembic migrations
- Credential encryption and password/API-key hashing code
- Docker Compose services, environment injection, networks, and volumes
- PostgreSQL SSL and authentication configuration
- Backup and upload storage paths and host permissions
- Read-only inspection of the running containers and database

No application files, database rows, or runtime configuration were modified during
the audit. Secret values were not printed or recorded.

## Data Protection Inventory

| Data or field | Protection | Notes |
|---|---|---|
| `llm_providers.api_key_encrypted` | Fernet encryption | Authenticated symmetric encryption |
| `integration_credentials.secret_encrypted` | Fernet encryption | Primary scanner/integration secret |
| `integration_credentials.secret2_encrypted` | Fernet encryption | Secondary credential where required |
| `webhook_configs.secret_encrypted` | Fernet encryption | HMAC signing secret |
| `users.hashed_password` | bcrypt hash | One-way password protection |
| `api_keys.key_hash` | SHA-256 hash | Appropriate for generated, high-entropy keys |
| `user_sessions.token_jti` | Plaintext identifier | JWT itself is not stored |
| Host, port, vulnerability, finding, and plan data | Plaintext | Searchable application data |
| Commands and scanner output | Plaintext | May contain sensitive evidence |
| Raw uploaded scan files | Plaintext files | Stored outside PostgreSQL |
| PostgreSQL volume and backups | No application encryption | Depends on host/storage controls |

### Encryption Implementation

Fernet uses a 32-byte key derived with HKDF-SHA256 from
`CREDENTIAL_ENCRYPTION_KEY`, with a compatibility fallback to `SECRET_KEY`.

Relevant locations:

- `backend/app/services/llm_provider_service.py:44-117`
- `backend/app/core/config.py:59-75`
- `backend/app/db/models_llm.py:53`
- `backend/app/db/models_integrations.py:66-72`
- `backend/app/db/models_project.py:127-148`

Live verification found:

- 4 of 4 user password values matched the bcrypt storage format.
- 14 of 14 API-key values matched the expected 64-character SHA-256 format.
- No encrypted LLM, integration, or webhook secrets were populated at audit time.

## Findings

### DB-01: Application Uses a PostgreSQL Superuser

**Severity:** High  
**Status:** Open  
**Owner:** TBD  
**Target date:** TBD

The live `nmapuser` role has all of the following attributes:

- `SUPERUSER`
- `CREATEROLE`
- `CREATEDB`
- `REPLICATION`
- `BYPASSRLS`

A backend compromise, SQL injection, or leaked database connection string therefore
provides control over the entire PostgreSQL cluster rather than only the BlueStick
schema.

The deployment also leaves `POSTGRES_PASSWORD` unset. Docker Compose consequently
uses the documented fallback password:

```yaml
POSTGRES_USER: ${POSTGRES_USER:-nmapuser}
POSTGRES_PASSWORD: ${POSTGRES_PASSWORD:-nmappass}
```

**Evidence:** `docker-compose.yml:35-40`

**Required remediation:**

1. Generate and configure a strong random PostgreSQL password.
2. Create a non-superuser runtime role limited to the application database and
   required schema operations.
3. Use a separate privileged migration or administration role.
4. Remove production-compatible default credentials or fail startup when defaults
   are used outside an explicit development profile.

**Verification criteria:**

- Runtime database role reports `rolsuper = false`.
- Runtime role cannot create databases, roles, or extensions.
- `POSTGRES_PASSWORD` is explicitly configured and is not the documented fallback.
- Application startup and migrations still succeed using separated roles.

---

### DB-02: JWT Signing and Credential Encryption Share One Effective Secret

**Severity:** High  
**Status:** Open  
**Owner:** TBD  
**Target date:** TBD

In the audited deployment, `JWT_SECRET_KEY` and `CREDENTIAL_ENCRYPTION_KEY` are
unset. Both therefore fall back to `SECRET_KEY`.

Compromise of this single value permits an attacker to:

- Forge user JWTs
- Decrypt stored LLM, scanner, integration, and webhook credentials
- Disrupt sessions and stored credentials through uncoordinated rotation

```python
JWT_SECRET_KEY = os.getenv("JWT_SECRET_KEY", "") or os.getenv("SECRET_KEY", "")
CREDENTIAL_ENCRYPTION_KEY = (
    os.getenv("CREDENTIAL_ENCRYPTION_KEY", "")
    or os.getenv("SECRET_KEY", "")
)
```

**Evidence:** `backend/app/core/config.py:59-75`

**Required remediation:**

1. Generate independent high-entropy values for `JWT_SECRET_KEY` and
   `CREDENTIAL_ENCRYPTION_KEY`.
2. Document backup and recovery requirements for the credential-encryption key.
3. Before rotating a populated encryption key, implement key versioning or a
   decrypt-and-re-encrypt migration.
4. Remove the legacy encryption fallback once deployments have migrated.

**Verification criteria:**

- All three configured secret values are independent.
- Restarting the stack preserves access to stored encrypted credentials.
- JWT-key rotation does not affect encrypted integration credentials.

---

### DB-03: Application Secret Is Injected Into the Frontend Container

**Severity:** High  
**Status:** Open  
**Owner:** TBD  
**Target date:** TBD

The frontend service receives the complete `.env` file:

```yaml
frontend:
  env_file:
    - .env
```

Live container inspection confirmed that `SECRET_KEY` is present in the frontend,
backend, and worker environments. Nginx does not require the JWT signing or
credential-encryption secret.

Compromise of the internet-facing frontend container can therefore expose the
effective JWT and credential-encryption key even without direct access to the
backend container.

**Evidence:** `docker-compose.yml:154-181`

**Required remediation:**

1. Remove `env_file: .env` from the frontend service.
2. Pass only explicitly required, non-secret frontend variables.
3. Review the worker environment and remove keys it does not require.
4. Prefer Docker secrets or mounted secret files over broad environment injection
   for production deployments.

**Verification criteria:**

- Frontend container environment contains no application, database, or credential
  secrets.
- Frontend still starts and serves the configured API URL.
- Each service receives only the minimum configuration required for its function.

---

### DB-04: Database Backups and Raw Uploads Are Unencrypted and Broadly Readable

**Severity:** High  
**Status:** Open  
**Owner:** TBD  
**Target date:** TBD

Observed host permissions:

| Path | Mode |
|---|---|
| `.env` | `0600` |
| `backups/` | `0775` |
| Existing database dump files | `0664` |
| `uploads/` | `0755` |
| Existing uploaded files | `0644` |

The backup script writes an ordinary, unencrypted PostgreSQL custom-format dump and
does not set a restrictive `umask`.

```bash
mkdir -p "$BACKUP_DIR"
docker compose exec -T db pg_dump -U "$PG_USER" -Fc "$PG_DB" > "$out"
```

Any local user who can traverse the project directory can read the existing
database dumps and scanner files. Those files contain plaintext engagement data
even when the narrow encrypted credential fields remain protected.

**Evidence:**

- `scripts/backup-db.sh:43-50`
- `scripts/backup-db.sh:93-124`
- `docker-compose.yml:90-95`
- `docker-compose.yml:148-150`

**Required remediation:**

1. Set `umask 077` at the beginning of backup and sensitive file-management scripts.
2. Set backup and upload directories to `0700`.
3. Set backup and upload files to `0600`.
4. Encrypt backups using age, GPG, encrypted object storage, or an equivalent
   managed backup system.
5. Define backup retention, off-host storage, restoration testing, and secure
   disposal procedures.

**Verification criteria:**

- A newly created backup is encrypted and mode `0600`.
- A non-owner local account cannot read backups or uploads.
- A documented restore test succeeds using the protected backup.

---

### DB-05: Pentest Commands and Evidence Are Stored as Plaintext

**Severity:** Medium  
**Status:** Open  
**Owner:** TBD  
**Target date:** TBD

The platform stores security-assessment evidence in directly readable columns,
including:

- Executed commands
- Raw test output
- Nmap host and port script output
- NetExec usernames, shares, users, groups, policies, and raw output
- File previews and parser errors
- Host notes and resolution summaries
- Host IPs, DNS records, ports, vulnerabilities, and web page text
- Environment probes, source IPs, and request audit data

Examples:

```python
command_run = Column(Text)
raw_output = Column(Text)
findings_summary = Column(Text)
```

**Evidence:**

- `backend/app/db/models_agent.py:670-707`
- `backend/app/db/models.py:117-151`
- `backend/app/db/models_confidence.py:124-163`
- `backend/app/db/models.py:500-529`

This storage is operationally understandable, but scanner output can contain
password hashes, access tokens, authorization headers, credentials supplied on a
command line, and sensitive client topology.

**Required remediation:**

1. Classify the entire database and upload directory as sensitive engagement data.
2. Add configurable retention and purge controls for raw evidence.
3. Redact common credential formats before storing command and scanner output where
   this does not destroy required evidence.
4. Document that operators should avoid placing credentials directly in commands.
5. Consider application-level encryption for selected high-risk evidence fields if
   database administrators should not have routine access to their plaintext.

**Verification criteria:**

- Retention and purge behavior is documented and tested.
- Known token/password patterns are covered by storage-boundary tests.
- Reports and normal analysis still preserve necessary forensic evidence.

---

### DB-06: No Volume-Level or Database-Wide Encryption

**Severity:** Medium  
**Status:** Open  
**Owner:** TBD  
**Target date:** TBD

PostgreSQL data is persisted in a standard Docker local volume:

```yaml
volumes:
  postgres_data:
    driver: local
```

Live inspection identified the volume as `networkmapper_postgres_data`. No
PostgreSQL transparent encryption, `pgcrypto`-based broad field encryption,
encrypted Docker volume driver, or backup encryption is configured.

A copied Docker volume, stolen unencrypted disk, privileged host account, or Docker
daemon compromise exposes most application data.

**Evidence:** `docker-compose.yml:196-199`

**Required remediation:**

1. Require host full-disk encryption for single-host deployments, or use encrypted
   block storage for the Docker data root.
2. Encrypt off-host snapshots and backups with independently managed keys.
3. Document that Docker volume isolation is not encryption.
4. Consider higher-granularity field encryption only where the search and reporting
   impact is justified.

**Verification criteria:**

- Deployment documentation identifies the required encrypted storage layer.
- Backup and snapshot encryption can be demonstrated.
- Recovery procedures include encryption-key recovery.

---

### DB-07: Arbitrary `extra_config` Objects Are Stored in Plaintext

**Severity:** Medium  
**Status:** Open  
**Owner:** TBD  
**Target date:** TBD

LLM and integration APIs accept arbitrary dictionaries and serialize them to
plaintext `Text` columns:

```python
extra_config=json.dumps(extra_config) if extra_config else None
```

The current UI uses these fields for non-secret settings. Other API clients can,
however, place authorization headers, tokens, or passwords in them.

**Evidence:**

- `backend/app/services/llm_provider_service.py:150-172`
- `backend/app/services/integration_service.py:71-95`
- `backend/app/api/v1/endpoints/llm_providers.py:75-91`
- `backend/app/api/v1/endpoints/integrations.py:106-126`

**Required remediation:**

1. Define and validate allowed `extra_config` keys per provider and integration.
2. Reject known secret-shaped keys such as `password`, `token`, `secret`, and
   `authorization`.
3. If arbitrary extension fields must remain supported, encrypt the complete
   configuration object.

**Verification criteria:**

- Secret-shaped keys are rejected or encrypted.
- Existing valid configuration remains compatible.
- API tests cover nested secret values.

---

### DB-08: Database TLS Allows Downgrade and Does Not Verify Identity

**Severity:** Medium  
**Status:** Open  
**Owner:** TBD  
**Target date:** TBD

PostgreSQL SSL is enabled, the backend's live database connection uses SSL, and
non-local PostgreSQL authentication uses SCRAM-SHA-256. The PostgreSQL port is not
published to the host. These controls substantially reduce ordinary network
exposure.

The deployed `DATABASE_SSL_MODE` is nevertheless `prefer`. This mode can fall back
to plaintext and does not verify the database server certificate.

```yaml
DATABASE_SSL_MODE: ${DATABASE_SSL_MODE:-prefer}
```

**Evidence:**

- `docker-compose.yml:80-81`
- `docker-compose.yml:140-142`
- `backend/app/core/config.py:145-154`
- `scripts/postgres/init-ssl.sh:1-40`

**Required remediation:**

1. Use `verify-full` with an explicit trusted CA where practical.
2. Use at least `require` when certificate verification cannot yet be deployed.
3. Add a startup or health check that confirms the active connection uses SSL.

**Verification criteria:**

- Application refuses a non-TLS PostgreSQL connection.
- With `verify-full`, a mismatched or untrusted database certificate is rejected.

## Existing Positive Controls

The following controls were present and functioning:

- PostgreSQL port `5432` is not published to the host.
- Backend and database communicate over an isolated Docker bridge network.
- The live backend-to-PostgreSQL connection uses TLS.
- PostgreSQL network authentication uses SCRAM-SHA-256.
- Human passwords are bcrypt hashes.
- Generated agent API keys are stored as SHA-256 hashes rather than plaintext.
- Credential API responses expose only `has_secret` booleans.
- Audit request bodies pass through field- and value-based secret redaction.
- `.env` is mode `0600` and excluded from Git.
- The TLS private key is mode `0600`.
- The initial administrator password marker was absent at audit time.
- The backend process runs as a non-root container user.

## Docker Threat Model

| Compromise scenario | Expected exposure |
|---|---|
| Database dump or volume only | Most assessment data is readable. Fernet credentials remain encrypted; bcrypt passwords and high-entropy API keys are not directly recoverable. |
| Raw upload directory only | Original scanner output and screenshots are readable and may contain secrets or sensitive topology. |
| Backend container | Complete database access and credential decryption because the container has the DB URL and encryption key. Current superuser DB role increases impact. |
| Frontend container | Currently exposes `SECRET_KEY`; this can enable JWT forgery and credential decryption when paired with database access. |
| Worker container | Database access and application secret are available; impact is similar to backend compromise. |
| Docker daemon or host root | Complete compromise: volumes, bind mounts, environment variables, process memory, and network traffic are accessible. |
| Physical disk theft | Exposure depends on host full-disk or block-storage encryption, which is outside this repository and was not verified. |

## Recommended Remediation Order

| Priority | Work item | Finding |
|---|---|---|
| 1 | Replace default database credentials | DB-01 |
| 1 | Introduce a least-privilege runtime database role | DB-01 |
| 1 | Remove `.env` from the frontend service | DB-03 |
| 1 | Configure separate JWT and credential-encryption keys | DB-02 |
| 2 | Restrict backup/upload permissions and encrypt backups | DB-04 |
| 2 | Require verified or non-downgradable database TLS | DB-08 |
| 2 | Add raw-evidence retention and redaction policy | DB-05 |
| 3 | Validate or encrypt arbitrary `extra_config` data | DB-07 |
| 3 | Document encrypted storage requirements | DB-06 |

## Review Sign-Off

| Role | Name | Decision | Date |
|---|---|---|---|
| Application owner |  |  |  |
| Security reviewer |  |  |  |
| Infrastructure owner |  |  |  |

## Remediation Notes

Use this section to record implementation decisions, accepted risks, pull requests,
deployment changes, and verification results.