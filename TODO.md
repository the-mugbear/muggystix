# TODO

Forward-looking / deferred work. (`CHANGELOG.md` records what changed; this records
what's intentionally left for later.)

---

## AI Assist tool surface — plan, 2026-08-19

The assist tool set is now derived from the analyst's job rather than added
question by question: **[documentation/ASSIST_TOOLS.md](documentation/ASSIST_TOOLS.md)**.

**P1 shipped in 2.294.0** (prompt 1.55.0) — as three tools, not four:

- [x] **`assist_get_patterns`** — systemic insights (blind spots, segment
  outliers, condition spread, per-family root cause). The "which subnet is
  worse than the others" analysis already existed in
  `systemic_insight_service`; assist simply could not reach it.
- [x] **`assist_get_finding`** — one finding with its evidence note, comment
  thread and attachment references, plus `GET /assist/attachments/{id}` so the
  agent can actually fetch the screenshots with its key (the operator-facing
  download needs a JWT).
- [x] **`assist_get_posture`** — headline condition + signals + remediation
  flow, from `posture_service`.
- [x] ~~**`assist_get_attention`**~~ — **dropped, not deferred.**
  `compute_posture` already folds `compute_project_attention` and
  `compute_site_attention` in, so this would have been the same numbers under a
  second name — the "tools the agent has to combine" failure the plan's own
  review rule exists to prevent.

Still queued:

- [ ] **P2** — subnet insights (per-subnet EOL / TLS / SMB-signing / weak-auth
  detail), ingestion issues (so "no data" can be told from "the upload didn't
  parse"), web-interface screenshot references.
- [ ] **P3 (build, don't wrap)** — time-series. "What changed since last week"
  has no implementation for humans either; the existing insight services are
  deliberately cross-sectional because engagements run 6–8 weeks.

Rule of thumb recorded there: a new tool must be a distinct *question shape*.
`q=` filters, file-shaped downloads, and rollups an existing service computes
are not tools.

---

## Deferred from the MCP / assist work — 2026-08-19

Each of these was found while building something else, judged real, and left
alone deliberately. Recorded here so the reasoning survives the conversation
that produced it.

### Test-harness

- [ ] **`setupTests.ts` mocks `useParams` globally to `{ id: '1' }`.** Any page keyed
  on its own route param therefore reads as "no param supplied" in tests and
  silently falls through to its other branch — `AssistSessions` needed a local
  override to test its detail view at all. Route-param behaviour is effectively
  untested app-wide, and a page test can pass for the wrong reason. Fixing it
  means auditing every suite that currently depends on the fixed `{ id: '1' }`,
  which is why it isn't folded into a feature commit.
- [ ] **Backend tests share one Postgres.** Parallel or concurrent runs interfere;
  an external reviewer hit it too and their isolated rerun passed. Wants a
  per-run database (or a transaction-per-test harness), not a retry.

### Schema

- [ ] **`assist_sessions.status` is free-text `String(20)` with no CHECK.** `"actve"`
  is storable. Status semantics became load-bearing in 2.283.0 (the derived
  active/ended rule), so this is the moment it earns a constraint.
- [ ] **Stored vs. derived session status.** The column is *eventually* correct —
  the hourly sweep converges it, the API derives the truth per request. Anything
  querying `assist_sessions.status` directly (a future report, an ad-hoc SQL
  check) gets a different answer from the API for up to an hour. The model needs
  a comment saying the API is authoritative; the endpoints already say it, but
  the next person to write a query won't read them.

### Frontend

- [ ] **Relative-time consolidation is done for the ten timestamp formatters**
  (`utils/relativeTime.ts`, v5.179.0), but four surfaces were deliberately left
  out because they answer different questions — calendar-day bucketing
  (`MyActivityCard`), day/month scan age (`ProvenanceCard`), server-provided day
  counts (`SecurityPosture` / `PortfolioDashboard` / `Operations`), and a
  countdown (`TestPlanLayout.formatTimeLeft`). Listed in the util's docstring so
  they don't read as misses. No action expected — this entry exists to stop the
  next sweep "finishing the job" and breaking them.

### Product

- [ ] **Assist is absent from the unified `/agent-activity` timeline**, which covers
  recon, plan generation and execution only. Widening it means touching the
  shared session-kind service (`agent_sessions.py`, `SessionKindLiteral`), which
  is a different change from giving assist its own page — that shipped as
  `/assist-sessions` in 2.284.0.

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
