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
- [ ] **Four dashboard endpoints with no consumer:** `/dashboard/my-tasks`,
      `/my-attention`, `/team-review`, `/new-scans-since`. Likely stranded by the
      Operations reshape. Confirm they aren't the better source for what /operations now
      shows, then wire or delete.
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
- [ ] **`POST /agents/{id}/renew-key`** — no UI, so an expired agent key has no
      self-service recovery.
- [ ] **`POST /findings`** (`createFinding`) — findings arrive only by promotion; manual
      creation is unreachable. Probably correct as a product decision — if so, delete the
      client function and consider closing the route.
- [ ] **Scope create/update/detail** — `createScope`, `updateScope`, `getScope`,
      `getScopeHostMappings` are all uncalled. `ScopeDetail` was retired in 4.50.0
      (`/scopes/:id` now redirects) and these are its leftovers. Scopes appear to be
      created only via subnet upload; confirm that's intended.
- [ ] **Subnet label attach/detach** — `attachSubnetLabel` / `detachSubnetLabel` and both
      `/scopes/subnets/{id}/labels/{id}` routes are unreachable from the UI.
- [ ] **`/dashboard/os-stats`, `/dashboard/port-stats`** — analytics endpoints with no
      consumer. Check against the no-vanity-metrics rule before wiring: if nothing acts on
      them, delete instead.

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

- [ ] **`WebInterface.cert_not_after` / `cert_self_signed`** are written only by the httpx
      path; whatweb and eyewitness imports leave them null, and neither is serialized to
      the host detail response. This is why the ProvenanceCard renders empty on most hosts
      even though its plumbing was fixed in 2.240.x. Fix the writers, then serialize.

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
