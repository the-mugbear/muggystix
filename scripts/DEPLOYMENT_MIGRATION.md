# Deployment Script Migration Note

The old per-scenario deployment scripts were removed. `./scripts/deploy.sh` is the one entry
point: an interactive menu with seven options (see [README.md](README.md) in this folder).

| Removed script | Use now |
|----------------|---------|
| `docker-compose up -d` (by hand) | `./scripts/deploy.sh` → **1** Start / Rebuild (takes a pre-deploy DB backup first) |
| `setup-network.sh` | **2** First-time setup (detects the host IP, generates `.env` + SSL certs, starts) — or **3** Reconfigure IP for an existing install |
| `force-clean-rebuild.sh` | **4** Nuclear clean (destroys ALL data) |
| `deploy-fresh.sh` | **1** — every rebuild already busts the frontend build cache; there is no separate "fresh" mode |
| `deploy-test.sh` | Nothing — there is no parallel test instance |

The remaining options are **5** Security status, **6** Back up `.env` + SSL to the parent folder
and **7** Roll back to the previous build.

## How deployment works now

All traffic goes through nginx on port **443** (`https://<host>`); the backend's `:8000` is not
published to the host, and the API docs are at `https://<host>/docs`. On first boot the backend
creates the `admin` account: its password is `DEFAULT_ADMIN_PASSWORD` when set, otherwise a
generated one written to `./uploads/initial-admin-password.txt` (mode 0600, never logged); a
password change — and TOTP enrolment, with the default `REQUIRE_2FA=true` — is forced on first
login. For diagnostics run `./scripts/collect-logs.sh`, which writes an anonymised bundle safe to
share.
