# Security policy

BlueStick is self-hosted software that holds the results of security assessments: scan output, hosts,
credentials tools found, findings and client reports. A weakness in it exposes a client's network to whoever
reaches the installation. Reports of such weaknesses are welcome and are handled in private.

## Reporting a vulnerability

**Do not open a public issue for a security problem.** Use GitHub's private vulnerability reporting on this
repository (**Security → Report a vulnerability**), which reaches the maintainers alone.

Include what lets someone reproduce it: the versions (**About BlueStick** in the user menu, or
`platform_version.json` of the deployed tree), the role of the account used (global admin or member; project
admin, analyst, auditor or viewer; or an agent key), the request or file that triggers it, and what you
observed. A scan file that triggers a parser problem is the most useful attachment: strip it to the smallest
input that still shows the behaviour and remove any real client data first.

This is a community-maintained project with no paid security team and no bounty. Expect an acknowledgment
when a maintainer reads the report and a fix in a release rather than a backport; the release's line under
**Reference → Release notes** in the app and `backend/app/data/release_notes.json` says what changed, without
details that would help an attacker before installations have upgraded.

## What counts

Anything that crosses a boundary the design promises to hold:

- **Authentication and sessions** — sign-in, 2FA, session renewal and revocation, password change ending
  every sign-in and agent key, the sign-in rate limits.
- **Authorization across the two role axes** — a global `MEMBER` acting as `ADMIN`; a project viewer or
  auditor performing an analyst's or admin's write; a member of one project reading another's data; a route
  under `/projects/{project_id}` that does not check membership.
- **The agent surface** — an agent key acting beyond its operator's project role, a key surviving its
  operator's departure or password change, the MCP layer making an authorization decision of its own, or a
  published safety claim (`/.well-known/networkmapper.json`) the server does not enforce.
- **Hostile input** — a scan file, a pasted note, a finding's report text or an uploaded template file that
  executes code, escapes its container, reaches another user's browser (XSS), becomes Quarto or `.qmd`
  source in a client report, or makes a worker hang or exhaust memory with a small input.
- **Outbound requests the design forbids** — the server resolving names, scanning, sending email or
  fetching a CDN resource into a report. The only outbound connections are the operator's configured
  integrations (webhooks, LLM providers), and those must never reach assessment targets.
- **Data leaving the installation** — a secret (LLM provider key, scanner credential, `SECRET_KEY`) in a
  response, a log, an agent's instructions or the `collect-logs.sh` bundle, which is meant to be anonymised.
- **Deployment and storage** — the `uploads/` tree, the database's TLS, backups, `deploy.sh` doing work as
  root or opening a folder to every account.

## What does not count

- Credentials a scanner found on the assessed network being shown unmasked to analysts: that is the
  product's purpose (an installation runs on an isolated assessment network).
- Behaviour that needs a compromised host, a compromised database or physical access.
- The absence of hardening the operator chooses to skip: `REQUIRE_2FA=false`, the self-signed certificate
  instead of the local CA (`ca/README.md`), an installation exposed to the internet, which it is not designed
  for.
- Findings from an automated scanner with no demonstrated impact.

## Supported versions

Only the latest release receives fixes. There are no long-term branches; upgrade notes for every release
that changes data or refuses an upgrade are in `README.md` ("Upgrading across") and the release notes.

## Hardening an installation

`./scripts/deploy.sh` option 5 (Security status) reports the installation's permissions, certificate and
settings; `README.md` and `scripts/README.md` describe each. Defaults worth knowing: 2FA is required for every
account, the first administrator's password is written to `uploads/initial-admin-password.txt` and never
logged, `uploads/` belongs to the containers alone, the database runs with TLS when a certificate is present,
and every agent key expires and is bound to one project session.
