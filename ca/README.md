# A local CA for BlueStick

BlueStick ships with a **self-signed** certificate. On a closed network that
causes three recurring problems:

- **Every client must be told to trust it, separately.** Node-based agents
  (Claude Code, VS Code) ignore the system store, Python tools carry their own
  bundle, Codex reads its own directory.
- **Strict clients refuse it even when trusted.** It is marked `CA:TRUE` yet
  used as a server certificate, which Rust `rustls`-based clients reject.
- **It changes.** A fresh install or a new IP generates a new one, and every
  client has to trust it again.

A local CA fixes all three. You create **one root certificate** and install it
on each analyst machine **once**. BlueStick then serves a normal server
certificate signed by that root, and you can reissue it (renewal, new address)
without touching any client.

No CA server or extra container is needed: the CA is a key pair and a
certificate that `ca/local-ca.sh` creates and uses with `openssl`.

## Contents

- [Machines and what lives where](#machines-and-what-lives-where)
- [Step 1 — Configure (admin workstation)](#step-1--configure-admin-workstation)
- [Step 2 — Create the root CA (admin workstation)](#step-2--create-the-root-ca-admin-workstation)
- [Step 3 — Issue BlueStick's certificate (admin workstation)](#step-3--issue-bluesticks-certificate-admin-workstation)
- [Step 4 — Copy it to the BlueStick host](#step-4--copy-it-to-the-bluestick-host)
- [Step 5 — Install it (BlueStick host)](#step-5--install-it-bluestick-host)
- [Step 6 — Trust the root (each analyst machine)](#step-6--trust-the-root-each-analyst-machine)
- [Renewing, changing address, replacing the root](#renewing-changing-address-replacing-the-root)
- [Troubleshooting](#troubleshooting)

## Machines and what lives where

| Machine | Runs | Holds |
|---|---|---|
| **Admin workstation** (one, trusted, ideally offline) | `check`, `root`, `server` | `ca/out/root/rootCA.key`, the root key: the most sensitive file in the whole setup |
| **BlueStick host** (remote) | `install` | BlueStick's server certificate and key in `ssl/certs/`. **Never the root key** |
| **Analyst machines** | `trust-help`, `verify` | `rootCA.crt` only (public) |

Anyone holding `rootCA.key` and its passphrase can issue certificates every
analyst machine trusts, within the ranges you allow in step 1. Keep it off the
BlueStick host. Keep it on an encrypted disk or USB stick, backed up. Store the
passphrase separately from it.

You need OpenSSL 1.1.1 or newer and bash (Linux, macOS or WSL) on the admin
workstation and the BlueStick host.

## Step 1 — Configure (admin workstation)

Copy the `ca/` folder (it is part of the BlueStick source tree) to the admin
workstation, then:

```bash
cp ca/ca.conf.example ca/ca.conf
$EDITOR ca/ca.conf
./ca/local-ca.sh check
```

**Every value in `ca.conf` must be set by you.** The template ships with
`CHANGE_ME` placeholders, and `local-ca.sh` refuses to do anything while one
remains. It also refuses:

- values containing "example";
- documentation-only addresses (`192.0.2.x`, `198.51.100.x`, `203.0.113.x`);
- networks broader than `/8`;
- lifetimes out of range;
- server names or addresses outside the ranges the root allows. A server
  name counts as inside a domain when it IS the domain or ends in `.domain`
  (`bluestick.lab.internal` is inside `lab.internal`, not inside
  `bluestick.internal`); the message names the permitted domains and
  suggests a name that fits, or the domain that would cover yours.

The root values cannot be changed once the root is issued, so choose them
deliberately:

| Setting | What it is | How to choose |
|---|---|---|
| `ROOT_COMMON_NAME` | The name analysts see in their trust store | Identify the team and the engagement or year, e.g. `Acme Red Team Lab Root CA 2026` |
| `ROOT_ORGANIZATION` | Your team or company | |
| `ROOT_VALIDITY_DAYS` | Root lifetime (1–3650) | Only as long as the engagement(s) need, e.g. `730` |
| `PERMITTED_IPV4_CIDRS` | The only addresses the root may vouch for | The **narrowest** networks that hold the BlueStick host(s), e.g. `10.20.0.0/16`. Not the client's whole estate |
| `PERMITTED_DNS_DOMAINS` | The only DNS names the root may vouch for | Your internal domain, no leading dot, e.g. `lab.internal`. It covers its subdomains |
| `SERVER_DNS_NAMES` | Names BlueStick is reached by | e.g. `bluestick.lab.internal`. A name survives an IP change; add it to a hosts file or internal DNS |
| `SERVER_IPS` | Addresses BlueStick is reached by | **Must include `HOST_IP`** from the deployment's `.env`; `install` refuses otherwise |
| `SERVER_VALIDITY_DAYS` | Server certificate lifetime | 397 or less (clients reject longer) |

The **permitted ranges are the point**. They are written into the root as
X.509 *name constraints*. A certificate the root signs for anything outside
them is rejected by clients. We tested this with curl, Node and Python: all
refuse ("permitted subtree violation"). So even a stolen root key cannot be
used to impersonate an outside site to your analysts' machines.

`check` prints what will be issued. Read it before step 2.

## Step 2 — Create the root CA (admin workstation)

```bash
./ca/local-ca.sh root
```

You are asked for a **passphrase** for the root key (twice). The script writes:

- `ca/out/root/rootCA.key`: encrypted with that passphrase, mode 600;
- `ca/out/root/rootCA.crt`: the certificate analysts will trust.

It prints the root's **SHA-256 fingerprint**. Record it: analysts compare
against it before trusting the file (step 6).

The script refuses to overwrite an existing root. It warns if it looks like
it is running on a BlueStick deployment, because the root does not belong
there. `ca/.gitignore` keeps `ca.conf` and everything in `ca/out/` out of git.

**Back up `ca/out/root/`** (both files) somewhere safe now. Without the key and
its passphrase you cannot renew BlueStick's certificate. You would have to
create a new root and re-install it on every analyst machine.

## Step 3 — Issue BlueStick's certificate (admin workstation)

```bash
./ca/local-ca.sh server
```

It asks for the root passphrase, then writes `ca/out/server/`:

- `networkmapper.crt`: the server certificate, with `CA:FALSE`, marked for
  serving TLS, and your names and addresses;
- `networkmapper.key`: its key (unencrypted: nginx must read it at start);
- `rootCA.crt`: a copy of the root, used by `install` to check the chain.

The script checks the new certificate against the root before finishing,
name constraints included. Running `server` again moves the previous
certificate into `ca/out/server/previous-<time>/`.

## Step 4 — Copy it to the BlueStick host

Copy **only `ca/out/server/`**, never `ca/out/root/`. Over the internal network:

```bash
scp -r ca/out/server admin@<bluestick-host>:/tmp/bluestick-cert
```

On an air-gapped host, use removable media. Wipe the server key from the media
afterwards.

The BlueStick host already has `local-ca.sh`, since `ca/` ships with the
source tree. If its copy is older than this guide, copy `ca/local-ca.sh` across
too.

## Step 5 — Install it (BlueStick host)

On the BlueStick host, from the deployment folder (the one with
`docker-compose.yml` and `.env`, e.g. `/srv/bluestick`):

```bash
cd /srv/bluestick
./ca/local-ca.sh install /tmp/bluestick-cert --dry-run   # checks only, changes nothing
./ca/local-ca.sh install /tmp/bluestick-cert
shred -u /tmp/bluestick-cert/networkmapper.key            # the copy you brought over
```

Before touching anything, `install` checks:

- the key belongs to the certificate;
- the certificate is CA-signed, verifies against `rootCA.crt` and has not expired;
- it names `HOST_IP` from `.env` (clients connect to that address);
- no root key was copied along (it refuses if one was).

Then it:

1. backs up the current `ssl/certs/` to `ssl/certs.backup-<time>/`;
2. installs the new certificate (644) and key (600) into `ssl/certs/`;
3. restarts the `frontend` (nginx) and `backend` containers. They mount the
   certificate as single files and keep the old one until they restart. A
   restart is enough; no rebuild is needed;
4. connects to `https://HOST_IP` and confirms the new certificate is being
   served.

It prints the rollback command. To undo:

```bash
cp -p ssl/certs.backup-<time>/* ssl/certs/ && docker compose restart frontend backend
```

Notes for this host from now on:

- `deploy.sh` option 3 (Reconfigure IP) keeps a CA-issued certificate. It
  refuses, changing nothing, when the certificate does not name the new
  address: reissue first (see "New address" below). Before 2026-09-29 it
  replaced any certificate with a self-signed one; don't use it on an older
  deployment.
- `deploy.sh` option 1 keeps an existing certificate. `upgrade-instance.sh`
  carries `ssl/certs/` into a new source tree, and option 6 backs it up with
  `.env`.
- The database is unaffected. Postgres copied a certificate into its own data
  directory when it was first created, and uses it only on the internal
  Docker network.

## Step 6 — Trust the root (each analyst machine)

Give analysts `rootCA.crt` (public) and **tell them its fingerprint through a
separate channel**, e.g. in person or in the engagement notes. They check it
before trusting the file. `trust-help` prints the file's fingerprint and the
exact commands for their machine:

```bash
./ca/local-ca.sh trust-help rootCA.crt
```

In short:

| Where | Command |
|---|---|
| Debian / Ubuntu / Kali | `sudo cp rootCA.crt /usr/local/share/ca-certificates/bluestick-root.crt && sudo update-ca-certificates` |
| RHEL / Fedora | `sudo cp rootCA.crt /etc/pki/ca-trust/source/anchors/bluestick-root.crt && sudo update-ca-trust` |
| macOS | `sudo security add-trusted-cert -d -r trustRoot -k /Library/Keychains/System.keychain rootCA.crt` |
| Windows (elevated) | `certutil -addstore -f Root rootCA.crt` |
| Firefox | Settings › Privacy & Security › Certificates › View Certificates › Authorities › Import |

Agents that ignore the system store need one line each in the shell profile:

```bash
export NODE_EXTRA_CA_CERTS=/path/to/rootCA.crt                  # Claude Code, VS Code (Node)
export SSL_CERT_DIR=/etc/ssl/certs                              # Codex (Linux, after the system step)
export REQUESTS_CA_BUNDLE=/etc/ssl/certs/ca-certificates.crt    # Python tools
```

If the retired `scripts/trust-cert.sh` set these before, point them at the root instead.
The self-signed certificate they named is no longer served.

Then check (no `-k`, no "insecure" flags anywhere):

```bash
./ca/local-ca.sh verify https://<bluestick-host> rootCA.crt
curl https://<bluestick-host>/health
```

On Windows, after the `certutil` step: `curl.exe https://<bluestick-host>/health`.

Install the root **only on your team's machines**, never on assets you are
assessing.

## Renewing, changing address, replacing the root

**Renew** (before the server certificate expires; `install` warns within 30
days): on the admin workstation run `./ca/local-ca.sh server`, then repeat steps
4 and 5. Analyst machines need nothing.

**New address:**

1. Add the new address to `SERVER_IPS` in `ca.conf`, **keeping the current
   one**, because `install` checks the certificate names the deployment's
   current `HOST_IP`.
2. Run `server`, then repeat steps 4 and 5.
3. On the BlueStick host, run `./scripts/deploy.sh` option 3 with the new
   address. It keeps the certificate because the certificate names that
   address. Then run option 1.
4. Optionally, reissue later without the old address.

Analyst machines need nothing. The exception is an address outside the root's
permitted ranges, which needs a new root; `check` tells you. A new **name**
only needs `SERVER_DNS_NAMES`, `server`, and steps 4–5.

**Replace the root** (it expired, its key was lost or exposed, or the ranges
must change):

1. Move `ca/out/root/` aside.
2. Edit `ca.conf`.
3. Run `root` and `server`, then repeat steps 4–6.
4. On every analyst machine, remove the old root from the system store.

There is no revocation list. On a small closed network, replacing the root is
the revocation.

## Troubleshooting

| Symptom | Cause and fix |
|---|---|
| `check` lists problems | Each line names the setting and why; fix `ca.conf`. No placeholder or example value is accepted |
| `install`: "does not name HOST_IP" | Add the `.env` `HOST_IP` to `SERVER_IPS`, re-run `server`, copy again |
| `install`: "could not confirm … serves the new certificate" | `docker compose ps`, `docker compose logs frontend`; nginx refuses to start on a mismatched key/cert. Roll back with the printed command |
| An agent still refuses, the browser works | That agent ignores the system store: set its variable (step 6) and restart the agent |
| `verify` or a client says "permitted subtree violation" | The server certificate names something outside the root's ranges. `server` prevents this; a certificate issued by other means would cause it |
| `unable to get local issuer certificate` | The machine does not trust the root yet (step 6), or trusts an old one |
| nginx or the connect page still shows the old certificate | Compose mounts the certificate as single files, and a container keeps the file it started with until it restarts. `docker compose restart frontend backend` (`install` does this) |
