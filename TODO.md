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

- [ ] **`getAnnotationHistory` → `GET /hosts/{id}/notes/{id}/history` — no such route.**
      The endpoint isn't in the app's 316 routes; the note-history feature was removed or
      never landed, and the client stub survived. Would 404 the moment anything wired it
      up. Decide: build the endpoint, or delete the stub and its
      `AnnotationStatusHistoryEntry` type.

### B. Backend feature is live, nothing in the UI reaches it

Each has a working, tested endpoint and no path for a user to get to it. These are product
decisions (surface it or remove it), not cleanup.

- [ ] **Tags can be created but never renamed or deleted.** `bulkTagHosts` creates tags by
      name, so the list only grows — a typo is permanent. `PATCH /hosts/tags/{id}` and
      `DELETE /hosts/tags/{id}` exist and are unreachable. Wants a small tag-management UI.
- [ ] **Four dashboard endpoints with no consumer:** `/dashboard/my-tasks`,
      `/my-attention`, `/team-review`, `/new-scans-since`. Likely stranded by the
      Operations reshape. Confirm they aren't the better source for what /operations now
      shows, then wire or delete.
- [ ] **`GET /webhooks/deliveries`** — the outbox has no UI. (Previously raised as B4 in
      the 2026-06 review; still open. The delivery-claim work in 2.240.x makes the outbox
      more trustworthy, not more visible.)
- [ ] **`GET /audit/logs`** — audit logging is a documented feature in CLAUDE.md with no
      way to read it outside the API.
- [ ] **DNS enrichment is entirely unsurfaced** — `/dns/records`, `/dns/lookup/{hostname}`,
      `/dns/zone-transfer/{domain}`. CLAUDE.md documents DNSService as a core service.
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

### C. Dead client code — superseded by bulk variants (propose deleting)

The singular forms were replaced by bulk operations and left behind. Deletion is safe but
**needs sign-off before removal**, per standing preference on destructive changes.

- [ ] `assignHost` / `unassignHost` → superseded by `bulkAssignHosts`
- [ ] `assignHostTags` / `removeHostTag` → superseded by `bulkTagHosts`
- [ ] `createHostTag` → superseded by `bulkTagHosts` creating tags by name
- [ ] `conditionHasDrilldown` (insights) — uncalled; sibling `conditionHostsHref` is used

Note the asymmetry: the singular *backend* routes (`POST /hosts/{id}/tags`,
`POST /hosts/{id}/assign`, …) are also unreached once these go. Decide per route whether to
keep them as agent/API surface or retire them with the client code.

### D. Dead schema — columns nothing reads or writes

- [ ] **`SecurityPolicy` — 14 of its columns are referenced nowhere in the app**
      (`password_min_length`, `password_require_*`, `max_failed_login_attempts`,
      `lockout_duration_minutes`, `session_timeout_minutes`, `max_concurrent_sessions`,
      `password_expiry_days`, `audit_retention_days`, `require_audit_login`,
      `require_audit_data_access`, `updated_by_id`). The table advertises a configurable
      password/session policy that nothing enforces — the same shape as the `allowed_ips`
      column dropped in 2.240.4. Either implement enforcement or drop the columns; leaving
      them is a standing misrepresentation of what the system does.
- [ ] `NetworkAttribution.cloud_service` — left behind when the `cloud:` DSL filter was
      withdrawn (2026-08). No reader, no writer.
- [ ] `User.last_activity_seen_at`, `UserSession.device_info`,
      `ImportedResultFile.imported_at` — never read.

### E. Partial writers — column exists, only some paths populate it

- [ ] **`WebInterface.cert_not_after` / `cert_self_signed`** are written only by the httpx
      path; whatweb and eyewitness imports leave them null, and neither is serialized to
      the host detail response. This is why the ProvenanceCard renders empty on most hosts
      even though its plumbing was fixed in 2.240.x. Fix the writers, then serialize.

### F. Loaded gun

- [ ] **`getReconSession(id, { includeHosts: true })`** — no caller, and the opt-in path it
      triggers is still uncapped. Harmless today; returns ~19 MB the moment anything calls
      it on a large session. Either cap it like the agent path (2.241.0) or remove the
      option.

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
