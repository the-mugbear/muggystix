# TODO

Forward-looking / deferred work. (`CHANGELOG.md` records what changed; this records
what's intentionally left for later.)

---

## Orphan inventory — 2026-08-10

A whole-repo sweep for code that was built and then stranded: client functions with no
caller, endpoints with no UI, columns nothing reads. Findings below are **verified**, not
raw detector output — three candidates were dropped after checking (a same-module helper
flagged as uncalled, plus host tags and host assignment, which *are* surfaced through bulk
variants).

**Detector caveat worth remembering:** "backend route has no frontend reference" is a
misleading test on its own. An orphaned client function still contains the URL literal, so
the route looks reached. Every route below was confirmed by checking whether the *calling
function* has a caller — not just whether the path appears in the frontend.

Clean on this sweep, for the record: no orphaned DB tables (71 DB / 70 model + alembic), no
never-imported React components, no uncalled backend service functions, only 2 TODO markers
in backend code.

### A. Broken — client calls an endpoint that does not exist

- [x] **DONE (2.242.0) — stub deleted.** ~~`getAnnotationHistory` → `GET /hosts/{id}/notes/{id}/history` — no such route.**
      The endpoint isn't in the app's 316 routes; the note-history feature was removed or
      never landed, and the client stub survived. Would 404 the moment anything wired it
      up.~~ Removed along with its `AnnotationStatusHistoryEntry` type; note
      status changes remain visible in the finding-comment threads (2.184.0).

### B. Backend feature is live, nothing in the UI reaches it

Each has a working, tested endpoint and no path for a user to get to it. These are product
decisions (surface it or remove it), not cleanup.

- [x] **DONE (2.243.0).** Tag management panel in Project Settings — rename, recolor,
      delete, with a delete confirm that states the host count it will affect.
- [x] **DONE (2.244.0) — deleted.** Confirmed superseded: `GET /workbench` batches all
      four from the same `operations_read_service` functions (incl. `new_scan_count`) and
      is what Operations calls. Tests ported to the service functions.
- [x] **DONE (2.243.0).** Delivery outbox in Project Settings — last 100 attempts with
      status / attempts / HTTP code / error text, status filter, retry on failed rows.
- [x] **DONE (2.243.0).** Audit log viewer in System Settings (admin-only, deployment-wide
      — login and user-admin events aren't project-scoped).
- [x] **DONE (2.243.0) — and the original claim was wrong.** DNS was never "entirely
      unsurfaced": per-host (`/hosts/{id}/dns-records`) and per-scan DNS already rendered.
      Only the three `/dns/*` endpoints were unreached. Resolved by adding a "Resolve now"
      action to the host DNS card (`/dns/lookup`), deleting the redundant `/dns/records`,
      and leaving zone transfer **deliberately API-only** — it targets the domain's
      authoritative NS, reads as active recon, and takes an unvalidated domain with no
      scope check or throttle. Recorded in the handler docstring so a later sweep doesn't
      re-flag it as an orphan.
- [ ] **`POST /agents/{id}/renew-key`** — no UI. Extends an *unscoped* agent key's TTL
      without rotating the secret (plan/recon keys have their own regenerate paths). Small
      button wherever agents are managed; worth confirming unscoped agent keys are
      actually used first.
- [x] **DONE (2.244.0).** Orphaned `createFinding` client removed; the route is KEPT and
      documented as deliberately API-only. Closing off manual creation would narrow the
      product without anyone asking.
- [x] **DONE (2.244.0) — and the answer was "retired product model", not "missing UI".**
      From `scopes.py`: *"As of v2.9.4 the user never names or manages a 'scope
      container' — a project has exactly one scope conceptually."* `POST /scopes/` and
      `PATCH /scopes/{id}` deleted with their clients; `getScope`/`getScopeHostMappings`
      clients removed (the host-mappings route kept as API-only). Also fixed the Scopes
      empty state, which told users to "Create one" — an action the UI cannot perform.
- [x] **DONE (2.244.0).** Both singular routes deleted — but only after checking: the
      bulk route only ADDS, so detach would have been lost had `PUT /subnets/{id}/labels`
      not existed. Tests pin both that the PUT detaches and that the singular routes
      stay gone.
- [x] **DONE (2.244.0) — deleted.** Aggregate distributions, no consumer, nothing acting
      on them.

### C. Dead client code — superseded by bulk variants

- [x] **DONE (2.242.0).** All seven client functions and both orphaned types removed, and
      the now-unreachable singular backend routes retired with them (`POST /hosts/tags`,
      `POST /hosts/{id}/tags`, `DELETE /hosts/{id}/tags/{tag_id}`,
      `POST|DELETE /hosts/{id}/assign`).

      **This is where the `host_assigned` webhook bug surfaced.** The singular assign route
      was the only place that dispatched the event, so retiring it would have silently
      deleted a feature users can subscribe to — one that had never worked, because the UI
      only ever called the bulk path. The dispatch moved to `/bulk/assign`. Worth
      remembering as a pattern: *the unreachable route was the only one doing part of the
      job.* Check what a dead route uniquely does before deleting it.

### D. Dead schema — columns nothing reads or writes

- [x] **DONE (2.242.0) — whole table dropped.** ~~`SecurityPolicy` — 14 of its columns are referenced nowhere in the app~~
      (`password_min_length`, `password_require_*`, `max_failed_login_attempts`,
      `lockout_duration_minutes`, `session_timeout_minutes`, `max_concurrent_sessions`,
      `password_expiry_days`, `audit_retention_days`, `require_audit_login`,
      `require_audit_data_access`, `updated_by_id`). The table advertises a configurable
      password/session policy that nothing enforces — the same shape as the `allowed_ips`
      column dropped in 2.240.4. Either implement enforcement or drop the columns; leaving
      them is a standing misrepresentation of what the system does. Dropping just the
      columns would have left an `id`+timestamps husk, so the table went too.
- [x] **DONE (2.242.0).** `NetworkAttribution.cloud_service` — left behind when the `cloud:` DSL filter was
      withdrawn (2026-08). No reader, no writer.
- [x] **DONE (2.242.0).** `User.last_activity_seen_at`, `UserSession.device_info`,
      `ImportedResultFile.imported_at` — never read.

### E. Partial writers — column exists, only some paths populate it

- [x] **PARTLY DONE (2.245.0) — and the original entry was wrong about the cause.**
      Serialization shipped (`cert_status` on host detail, rendered in ProvenanceCard),
      along with a query fix: the host-detail fetch filtered `cert_subject_org IS NOT
      NULL`, dropping every DV certificate — which has no organisation but does have an
      expiry.

      whatweb and eyewitness are **not** missing writers: neither format carries
      certificate data (`tls_info=None, # whatweb has no structured TLS block`). Null is
      correct there.

- [ ] **Parse nmap `ssl-cert` NSE output into the typed cert columns.** The real coverage
      gap. `nmap --script ssl-cert` is far more common in recon than httpx TLS probing,
      and its output is already stored as `Script` rows — just never parsed. Precedent
      exists: `_detect_smb_signing` extracts `Host.smb_signing` from NSE text the same way.
      **Open design question:** the cert columns live on `WebInterface`, which nmap never
      creates. Either synthesise a WebInterface row per TLS port (lights up the existing
      ProvenanceCard path for free, but widens what a "web interface" means — nmap can see
      TLS on 993/imaps, which is not a web interface) or give Port its own cert columns
      (truer semantics, but a second home for the same fact). Needs a call before building.

### F. Loaded gun

- [x] **DONE (2.242.0).** ~~`getReconSession(id, { includeHosts: true })`~~ — client option
      removed and the backend path capped at 2000 rows with a `hosts_truncated` flag,
      matching the agent-path treatment from 2.241.0.

---

## Risk scoring — removed, not hidden

**Status:** hidden from the UI 2026-06-06; **scaffolding since deleted**.

⚠️ The re-enable procedure previously documented here was stale — it pointed at code that
no longer exists. Verified 2026-08-10: `frontend/src/config/featureFlags.ts` does not
exist, `RISK_SCORING_ENABLED` appears nowhere in the repo, and `HostRiskAssessment`,
`risk_predicate`, and `_b_risk` are all absent from the backend.

Risk scoring is therefore **a rebuild, not a flag flip**. The original intent stands if it
is ever revisited: scoring weights should be admin-tunable, since the unpopulated
`HostRiskAssessment` table (every host scoring 0) is what made the first version useless.
