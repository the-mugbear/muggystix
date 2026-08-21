# Agent surface consolidation — one surface, the user's own permissions

The agent API has its own authorization model. It should not.

Today a key is scoped to one of four workflows (plan generation, execution,
recon, assist), and that scoping is enforced by four guards across **61
endpoints**. Those same endpoints perform **zero** project-role checks. The
agent surface and the user surface answer "may you do this?" in two unrelated
ways.

This document records the decision to collapse that into one agent surface
whose key **acts as the user who started the session**, what survives the
collapse and why, what gets deleted, and the order the work has to happen in so
nothing widens by accident.

---

## The decision

**An agent key carries the permissions of the operator who started its
session.** Authorization stops being an agent-specific concept and becomes the
RBAC the product already has: admin / analyst / auditor / viewer, plus project
membership. A viewer's agent is read-only because *the viewer* is read-only.

The reasoning is not "less code". It is that the workflow boundary never did
what its design intent claimed.

### Why the workflow boundary is obsolete

It was introduced to stop an agent going beyond its ask — an analyst might want
help with recon and planning but not want an agent to start testing. That
property was never achievable. Commands run on the operator's machine and the
server sees only what is reported; `CLAUDE.md` already concedes that the client
sandbox is the real boundary. An agent holding a recon key that decides to
exploit what it discovered simply does so, and BlueStick learns about it only if
the agent chooses to say so.

What the boundary actually delivers is narrower, and both properties are about
the **record**, not the action:

* **Read containment** — a recon key cannot enumerate test plans. A data
  exposure control, not a safety control.
* **Record integrity** — recon cannot write test results; execution cannot
  fabricate recon findings.

Neither justifies four key types, four session tables, five scope columns on
`api_keys`, and 61 guards.

### The model was actively causing bugs

v2.90.3 fixed a privilege escalation: any project member — **including
viewers** — could mint an agent key and use it via `/agent/test-plans/*` to
draft and populate plans, bypassing the analyst-role gate on the user-side
routes. The fix was to require the analyst role to *create the key*.

That bug class exists **only because the agent surface has a separate auth
model**. When the key carries the operator's role, a viewer's key is a viewer's
key everywhere and permanently. The escalation stops being gated and becomes
unrepresentable.

### Capability grants are not the answer either

`capabilities` / `capability_constraint` (used only by assist, 5 endpoints) are
a more precise expression of the same idea, and an earlier draft of this plan
proposed extending them across all 61 endpoints. That was wrong: it builds a
*second* authorization system beside the one the product already owns, and the
vocabulary would have to encode phase distinctions we are deleting.

The tell that the old model is grandfathered rather than chosen, in `deps.py`:

```python
if workflow != "assist":
    return LEGACY_WRITE_CAPABILITIES, None
```

The three original workflows do not use the modern mechanism at all.

---

## What survives, and why

Three things are load-bearing and must not be lost in the collapse. Two of them
were previously mis-filed as workflow scaffolding.

### 1. The plan approval gate — never grantable

A plan must be **human-approved** before execution begins, and completing a test
requires either a passing sanity check or an explicit `override_reason`.

This is the only control in the system that constrains **consequence** rather
than record, and it is not a key type — it is a state machine on `TestPlan`. It
must stay a plan-state check. If an operator could tick a box to let an agent
execute an unapproved plan, the collapse would have removed the one thing worth
keeping.

### 2. Target declaration — coordination, not authorization

A session declares what it is working on. This exists to stop **duplicate
effort**: two analysts unknowingly running recon over the same /24.

Enforcement cannot recover that waste — by the time BlueStick sees an ingest,
the scan already ran on someone's machine. What prevents the duplicate is user B
**seeing** that user A's agent is already working that range. So the mechanism is
*declare at session start, and show it on Agent Runs*.

A correction to an earlier draft: `AgentSession.scope_id` and `plan_id` were
marked for deletion as workflow scaffolding. They are not — **they are this
declaration**, and they survive with a clearer purpose.

Host-based scoping is incoherent for recon: recon's whole job is to **act on a
scope and populate host entries from what it finds**. The hosts do not exist to
be assigned until it has run. So the target is the scope, and host-level
targeting only applies to phases that operate on known inventory:

| Work shape | Target | Already exists as |
|---|---|---|
| Discovery (recon) | scope / subnet set | `scope_id` |
| Testing known inventory | the plan, which names its hosts | `plan_id` |
| Review / assist | project-wide, no target | (neither set) |

**So there is no target model to build.** `scope_id` and `plan_id` already carry
it. The work in Phase 3 is *surfacing* the declared target on Agent Runs so a
second analyst can see the range is taken, and recording what a session actually
touched against it.

**Out-of-scope ingest is recorded, not rejected.** A soft warning nobody can act
on is noise, and rejection recreates the friction this change removes. The
session's declared target plus what it actually touched is the audit answer.

### 3. Key renewal for long-running work — currently missing

Recon over a whole project can exceed 24 hours. The current state is worse than
it looks:

* `AGENT_KEY_TTL_HOURS` defaults to **24h**, capped at `AGENT_KEY_MAX_TTL_HOURS`
  (168h).
* The only renewal path left anywhere is `test_plans.py` `/{plan_id}/rotate-key`.
* **Recon and assist have no renewal at all.**
* `POST /agents/{id}/renew-key` was deleted in v2.295.0 with the `/agents`
  router. Correct in isolation — it renewed only *unscoped* keys, which no
  longer exist — but it removed the last in-place **renew**, leaving only
  **rotation**.

Rotation is the wrong primitive for a running agent: it issues a new token, so a
30-hour job must be re-bootstrapped mid-flight.

**Idle-based sliding expiry is also wrong**, and it is the tempting answer. It
fails exactly this case: an agent running a multi-hour scan makes *no* API calls
during it, so an idle timer kills the key mid-scan.

**Design:** bind key lifetime to the **session** — valid while the session is
active, with a generous absolute cap and an explicit renew endpoint. Revocation
becomes the control (ending a session already kills its key immediately), and
the existing staleness reaper handles sessions someone walked away from.

`key_expires_at` is already surfaced through `/agent/identity` and the assist
start payload, so the agent can see its own deadline. The prompt should instruct
it to **renew before starting anything it expects to outlast the key**.

#### The failure mode that actually happens, and why prevention is not enough

An operator starts a recon session. The agent launches nmap / masscan / Nessus
and **blocks**, waiting hours. The key expires while it waits. The agent
discovers this **at upload** — after the scanning is done, at the exact moment
the work is about to be delivered.

Prevention cannot be made reliable here, for two independent reasons:

* **A blocked agent cannot call anything.** While it sits in a foreground tool
  invocation it issues no requests, so no heartbeat, keep-alive or
  activity-based extension can fire. This is the same reason idle-based sliding
  expiry fails — restated, and it also defeats the staleness reaper's usual
  definition of "alive".
* **Scan duration is not predictable.** "Renew before a long operation" helps,
  but the agent cannot know that a `-p-` sweep across a /16 will take nine hours
  rather than one, and even a renewed key can be outlasted.

So the design rule is:

> **Expiry must never be terminal while the session is active. It must be
> recoverable using the expired key itself.**

The cost of getting this wrong is not an inconvenience — it is hours of scanning
discarded because a credential lapsed while the tool it authorised was running.

**Mechanism**

1. `renew` accepts an **expired** key, provided its session is still active and
   the expiry falls inside a grace window. It returns the *same* token with a
   new deadline, so the pending upload can simply be retried.
2. `401` becomes **machine-distinguishable**: recoverable (expired, session
   live, renewable — carrying the renew path) versus terminal (revoked, session
   ended, agent deactivated). Today it is a flat
   `401 "Agent API key expired"`, which gives an agent holding hours of output
   no way to tell whether retrying is worth anything.
3. The prompt teaches the recovery loop explicitly: on a recoverable 401, renew,
   then **retry the upload** — never discard collected output, never re-run the
   scan.
4. Optional prevention on top: an agent that is about to block may declare the
   expected duration and have its deadline extended ahead of time. Useful, but
   it is the belt, not the braces.

**The trade-off, stated plainly.** If an expired key can renew itself, expiry
stops being a revocation control; **ending the session becomes the control.**
That is already immediate and operator-driven, and it fits the trust model this
document adopts — but it should be a conscious choice, not a side effect. The
grace window bounds how long an abandoned key stays renewable, and every renewal
is audited.

---

## What gets deleted

| Thing | Detail |
|---|---|
| Workflow guards | `require_plan_scope`, `require_recon_scope`, `require_assist_scope`, `deny_scoped_keys` — and the `key_workflow` derivation that feeds them |
| Capability machinery | `resolve_capabilities`, `require_capability`, `enforce_capability_row_scope`, `LEGACY_WRITE_CAPABILITIES` |
| Columns | `agent_sessions.capabilities`, `capability_constraint`; `api_keys.test_plan_id`, `scope_id`, `recon_session_id`, `assist_session_id` |
| Session tables | Three of four lose their reason to exist — see below |
| Frontend | Three key-minting dialogs collapse into one session-start dialog |
| Support burden | "Which key do I need", and the 403s that teach it |

Replaced by: **the same role dependencies the JWT surface already uses**,
applied to the agent routers.

### The table duplication, measured

Four session tables, from the live schema:

| Table | Columns | Genuinely unique to it |
|---|---|---|
| `agent_sessions` | 21 | `workflow`, `plan_id`, `capabilities`, `capability_constraint` |
| `recon_sessions` | 21 | `hosts_discovered`, `ports_discovered`, `scans_ingested`, `uploads_submitted` |
| `execution_sessions` | 19 | `bundle_id`, `mode`, `test_plan_id` |
| `assist_sessions` | 17 | `purpose`, `last_activity_at`, `ended_at` |

**Twelve columns exist in all four** — `agent_id`, `started_by_id`, `status`,
`started_at`, the four environment-probe columns, `generated_by_model`/`_tool`,
`prompt_version`. Each workflow carries **two to four** genuinely distinct
fields on top of twelve duplicated ones. `assist.ended_at` is not even distinct
— it is `completed_at` under another name, which is why the timeline has to map
it.

The drift this causes is not theoretical:

* Assist was **absent from Agent Runs entirely** until v2.303.0, because "the
  list of workflows" lived in five places and one was not updated.
* `plan_generation` session status is **frozen at `"active"` forever** on the
  base row, because the truth lives on `TestPlan.status` and nothing syncs it.

### `workflow` survives as a label

It stays useful for the timeline, the prompts, and the record — and for scoping
the MCP tool catalog, which matters for context budget: a session seeing all
**48 tools (~40 KB, ~10k tokens)** pays that on every request, versus **27 tools
(~22 KB)** for a scoped set.

This is not a downgrade. At the MCP layer `workflow` is *already* only a label:
`tools/list` scoping is documented as presentation only — "an unlisted tool
called anyway still hits the endpoint's guard." The operator's declared intent
scopes the catalog. It simply stops pretending to be security.

---

## The `plan_generation` asymmetry

`plan_generation` is the one kind that is not a session. The service reads
`TestPlan` rows directly, so:

* rows are keyed by **`TestPlan.id`**, not `AgentSession.id` — switching the
  reader changes every id the UI links on;
* status is **derived live** from `TestPlan.status` via
  `_plan_generation_status()`, and nothing copies it to the base row;
* the backfill migration `b8e1f37a92c4` creates base rows for execution, recon
  and assist — **but not plan_generation**, so historical plans have none.

`/test-plans/generate` already creates a real `plan_generation` `AgentSession`.
It is half-built; the reader ignores it.

**A "manually created plan" is not a thing this product has.** An earlier draft
of this document claimed switching the reader would drop hand-written plans from
Agent Runs. That was wrong. There are exactly two callers of
`TestPlanService.create_plan`: `/test-plans/generate`, and an orphaned
`POST /test-plans/` that the frontend **never calls** — every UI path to a plan
goes through `/generate`. Bundle import does not create plans either. So every
plan a user can actually produce already has a `plan_generation` session, and
the reader switch drops nothing user-visible.

What remains is narrower and purely mechanical: **plans predating the R5 expand
have no base row**, and the backfill skips them. That needs one migration, not a
product decision.

The orphaned `POST /test-plans/` endpoint (analyst-gated, `agent_id=None`) can
stay as deliberately API-only — same treatment as `createFinding` in `TODO.md`.
It is then the only way to produce a plan with no session, and such a plan
simply will not appear on Agent Runs. That is the correct answer: a plan created
by curl with no agent involved is not an agent run.

---

## Sequence

Ordering is not negotiable. Removing the workflow guards **widens** what any key
can reach, so authorization must be in place first, and the deletions come last.

### Phase 1 — Make the key act as its operator ✅ **Shipped in 2.305.0**
Applied as **one router-level dependency** (`enforce_agent_operator_access`)
rather than 19 per-route edits — so it covers the whole surface and cannot be
forgotten on a new endpoint. Both gates run: a request must satisfy the
workflow guard *and* the operator's role.

* Reads require current project membership; writes additionally require
  `ANALYST`, except the six **session-metadata writes** (key renewal, the three
  environment probes, feedback, tool suggestions) which record something about
  the session rather than project data.
* Operator resolved from the session's `started_by_id`, **falling back to
  `Agent.owner_id`**. Same person in practice — an `Agent` is unique per
  (user, project) — but `started_by_id` is `ON DELETE SET NULL` and absent on
  pre-binding keys, so reading only the session would deny keys whose operator
  is perfectly identifiable.
* Global admins bypass, matching `require_project_role`.
* **Key renewal is mounted outside this gate**, on its own router. The gate
  authenticates normally, which rejects an expired key — and accepting an
  expired key is that route's entire purpose. Safe because renewal grants no
  authority: the renewed key still passes the gate on every real request, so an
  operator who lost membership can renew a key that can then do nothing.

**Nothing narrowed.** Every session-start endpoint already required `ANALYST`,
so no viewer or auditor keys exist. What changed is that the role now *stays*
true: a demotion, a removed membership, or a deactivated account reaches keys
already in the field, immediately. v2.304.0 made keys renewable, which had
widened that window rather than closing it.

### Phase 2 — Session-bound keys and renewal ✅ **Shipped in 2.304.0** (prompt 1.57.0)
**Decided: renewal, not rotation.**

Delivered: `POST /agent/session/renew` (no path param — the key identifies its
own session), accepting an already-expired key while the session is active and
under `AGENT_SESSION_MAX_LIFETIME_HOURS`; a structured 401 splitting recoverable
from terminal; `renew_path` + `renewable_until` on `/agent/identity`; and the
recovery loop in all four workflow prompts. Revoked keys, ended sessions and
past-lifetime sessions are all refused.

**Deferred:** replacing the operator-facing `/{plan_id}/rotate-key`. It is
UI-wired, and the agent-facing self-renewal is what the failure mode required.

* Add renew on the session; same token, later deadline. The agent does not
  re-bootstrap, which is the whole point for a job that outlives its key.
* Bind key lifetime to session activity with an absolute cap.
* Teach the prompt to check `key_expires_at` and renew *before* long operations.
* **Replace `/{plan_id}/rotate-key` with renew.** It is the last key endpoint
  standing, and its documented purpose — *"the original 24h key expired but the
  user wants to keep working on the same plan"* — **is** the renewal case. It
  only used rotation because renewal did not exist for plan keys. Rotation has
  exactly one honest use left (the secret is believed compromised), which is a
  revoke-and-restart, not a mid-run continuation.
* **Renewal must accept an expired key** while its session is active — see the
  failure-mode analysis above. An agent blocked on a multi-hour scan finds out
  its key lapsed only when it tries to upload, and by then the work is done.
* **Split the 401** into recoverable vs terminal so the agent can tell whether
  retrying is worth anything.
* Independently valuable: fixes a live gap for recon runs over 24h, and can land
  before any other phase.

### Phase 3 — Target declaration ✅ **Shipped in 2.306.0 / 5.187.0**
There was no target model to build — `scope_id` / `plan_id` already carried it.
The gap was that the timeline showed **ids**, and "Scope #3" cannot tell a
second analyst that a range is already being scanned, which is the entire
reason a session declares a target.

* Sessions now carry a `target_label`: the scope name plus its CIDRs (capped at
  3, then "+N more") for recon, the plan title for plan work, and nothing for
  assist — which is project-wide by design, so an empty target is the honest
  answer rather than a fabricated one.
* Resolved in **two batched queries** per page, not one per row.
* Surfaced on both Agent Runs and Project Activity, with the id as fallback for
  a deleted scope or a deployment mid-upgrade.

**Deferred:** recording out-of-scope ingest against the session. It is an audit
refinement — visibility is what prevents the duplicated work, and that is now
in place. Worth doing when the ingest path is next touched.

*Measured while testing:* the timeline resolves agent and user names once per
**distinct** object via the identity map, so its query count is bounded by how
many people work a project rather than by page size — 17 rows in 8 queries on
real data. A test pins that adding rows which share an agent adds no queries.

### Phase 4 — Single session-start flow
* One dialog replacing three, declaring intent (label) + target.
* Keep minting the existing key kinds underneath so nothing breaks yet.
* Resolve the `plan_generation` question above.

### Phase 5 — Delete the capability system ✅ **Shipped in 2.309.0** (prompt 1.58.0)
Resolved by a decision rather than a drain: **read-only assist was abandoned
entirely.** 99% of users are analysts, read-only was the *default* rather than a
choice anyone made, and every assist session in the deployment was already
ended — so there was nothing to port, version, or wait out. All three drain
options below became unnecessary.

Deleted: `AgentCapability`, `AgentCapabilityConstraint`,
`LEGACY_WRITE_CAPABILITIES`, `ASSIST_GRANTABLE_CAPABILITIES`,
`resolve_capabilities`, `require_capability`, `enforce_capability_row_scope`,
the two `agent_sessions` columns (migration `f1a6c92d4b70`), the `can_write_assigned`
start parameter, the dialog checkbox, the authority badges, and the MCP
`tools/list` capability filter.

**Two widenings, both deliberate and worth stating plainly:**
* An assist session started by an analyst can now write, where it was read-only
  unless the operator opted in.
* Writes are no longer narrowed to the operator's *assigned* hosts — an analyst
  can edit any host in their project through the UI, so their agent can too.

**One regression this caused, caught by an existing test:** the
`write:execution` capability had been doing double duty — gating authority *and*
enforcing the cross-workflow boundary, since assist sessions never carried it.
Deleting it silently let an assist key write an execution session's environment.
The boundary is now stated directly in the handler.

*(Historical: the drain strategies below were the plan while read-only assist
was still a feature. Kept because the analysis — particularly why the exposure
was 168h rather than hours — is the reasoning that made abandoning it the
obvious call.)*

#### ⚠️ Phase 5 would WIDEN live assist keys — this needs solving first

Flagged by external review, and it is the most dangerous thing in this plan.

Assist keys are the one credential that is genuinely restricted today: they
default to **no capabilities**, and may be further narrowed to the operator's
assigned hosts (`AgentCapabilityConstraint.ASSIGNED`). They are also started by
**analysts** — so under the Phase 1 gate their operator passes the ANALYST
check.

Delete capability enforcement, and a live read-only assist key stops being
read-only. It would pass the router gate on its operator's analyst role and gain
**project-wide write**. That is the opposite of a fail-closed flip: for this one
key type it fails *open*.

The earlier claim that "short TTLs mean the blast radius is hours" was also
wrong, and Phase 2 is why: keys are renewable up to
`AGENT_SESSION_MAX_LIFETIME_HOURS`, so the real exposure is **up to 168 hours**.

Phase 5 therefore needs a drain strategy before it can land. Options, roughly in
order of preference:

1. **Drain**: refuse to start new capability-less sessions, wait for live assist
   sessions to end or hit their lifetime cap, then flip. Bounded by 168h.
2. **Version the keys**: stamp a model version on the session at mint time and
   keep enforcing capabilities for pre-flip keys only.
3. **Flip and revoke**: end every live assist session as part of the deploy.
   Honest and immediate, but it interrupts work in progress.

Whichever is chosen, **the read-only property of assist has to survive the
consolidation** — an operator who deliberately started a read-only session must
not have it silently upgraded by a refactor.

### Phase 4 (partial) ✅ **Shipped in 2.308.0** — role floor + per-route read roles
Landed in the only safe order: **read roles first, then the floor.** Lowering
the floor while every GET was member-accessible would have handed a lower-tier
operator's agent an export their own session is refused.

* `AGENT_READ_ROLE_OVERRIDES` gives bulk exports the `AUDITOR` floor their JWT
  equivalents already carry (`export.py` and `reports.py` gate their whole
  routers on it): the project dossier, the host dumps, the recon target lists,
  and evidence downloads. Everything else needs membership only — a viewer can
  see hosts and scans in the UI, so their agent may too.
* **Assist session-start lowered `ANALYST` → `AUDITOR`**, which is what finally
  makes "auditors get a read-only agent" real. Recon, plan generation and
  execution stay at `ANALYST`: they exist to change project state.
* A **role × route matrix test** covers the four decisions the gate actually
  makes — ordinary read, bulk export, project write, session metadata — against
  each role. Two structural tests cover what a sample cannot: every override
  names a real route, and no export-shaped read route is left on the default.
  *That second one immediately caught `/assist/hosts.ndjson`, which I had
  missed.*

**Still owed by Phase 4:** the single session-start dialog (three collapse into
one), and the plan-generation backfill migration — deferred because its only
consumer is the timeline reader switch, which is itself blocked on the three
issues recorded above.

### Phase 4 must also decide the minimum role to start a session
"Auditors get a read-only agent" is settled, but **nothing implements it**: all
four session-start endpoints still require `ANALYST`, assist included. Until the
unified dialog lowers that floor, the auditor/viewer path is theory. Phase 4
therefore owns:

* the minimum role for starting a session (and whether it differs by intent), and
* a viewer/auditor **route matrix test** — every agent route × every role,
  asserting the expected allow/deny.

### The end state needs per-route READ roles, not just read-vs-write
The Phase 1 gate treats every `GET` as available to any current member. That is
correct *today* because no viewer or auditor keys exist, but it does not survive
Phase 4.

Concretely: `/assist/report-context.ndjson` is an uncapped export of project
data, while the equivalent JWT export surface requires **AUDITOR**. Once viewer
keys are possible and the workflow guards are gone, a viewer's agent would have
data egress its own JWT session is denied. The end state needs a minimum *read*
role per route, not a single membership check.

### Phase 6 — Audited, and the guard removal is **cancelled**
The precondition from Phase 5 was an audit of what each workflow check is
actually load-bearing for, because `write:execution` turned out to be silently
holding up the cross-workflow boundary. That audit says: **keep the guards.**

Three findings, all of which post-date the plan that proposed removing them:

1. **`require_plan_scope` is not primarily a workflow check.** Its distinctive
   job is per-plan scoping — a key minted for plan A is refused on plan B
   (`key_plan_id != plan_id`). That has nothing to do with workflows and would
   be lost with the guard.
2. **The guards are load-bearing functionally, not only as gates.**
   `require_assist_scope` stashes `scoped_agent_project_id`, which
   `_load_assist_session` re-checks; recon handlers resolve their session from
   `scoped_scope_id`. Removing the guards is not a deletion — it is a rewrite of
   how every handler finds its own session.
3. **Record integrity now rests on the guards alone.** Phase 5 deleted the
   capability system, and with it the second check that kept an assist key out
   of execution writes. The operator-role gate does not help here: every analyst
   passes it. So the workflow guard is now the *only* thing stopping a recon
   agent from submitting fabricated test results, or an assist agent from
   uploading scan data that invents inventory. That is a control over the
   **record**, which is the thing this product exists to keep trustworthy.

And the benefit the plan claimed — "`workflow` becomes a label" — was already
banked: at the MCP layer `tools/list` scoping is presentation only, which is
where the label pays for itself in context budget. There is nothing left to
collect by deleting ~60 lines of guard that cost one dict lookup per request.

### The table collapse — narrowed to the failure mode
The twelve columns duplicated across the four session tables are real
duplication, but the *cost* was never storage — it was **drift**, and drift has
a cheaper fix than a schema migration:

* Assist was absent from the timeline for months because "the list of workflows"
  lived in five places and one was missed. Fixed in 2.303.0.
* `plan_generation`'s base-row status is frozen at `active` because the truth
  lives on `TestPlan.status`. Not read from the base row, so inert.

A full collapse means rewriting every reader of `status` / `environment` /
`generated_by_*` across four tables — a large mechanical refactor whose failure
mode is exactly what this phase already demonstrated twice: removing something
that was quietly carrying a second load.

**So the drift is closed with a guard test instead** (`test_workflow_registration_is_complete.py`):
every `AgentSessionWorkflow` value must appear in the service's kind set and in
the API's literal. That is the bug that actually happened, and it now cannot
recur silently. Revisit the schema collapse if a *second* symptom appears; until
then it is cost without a matching benefit.

Phases 1–4 are reversible and independently useful. Phase 5 is the commitment.

---

## The one real trade-off

A leaked key becomes as powerful as its operator, where today a recon key is
bounded.

This is a credential-handling question, not a trust question. Agent keys live in
terminals and MCP client configs on disk. The mitigations already exist: short
TTL, session-bound and revoked when the session ends, full audit in
`agent_api_calls`, and rate limiting that is now atomic across workers
(v2.300.0).

It should not change the decision — today's scoped keys are pasted into exactly
the same places, so the difference is degree, not kind. But the framing belongs
in the session dialog, stated plainly rather than buried:

> **An agent key is a password with an expiry. Treat it like one.**

---

## Settled

* **Renewal, not rotation** (Phase 2). Rotation survives only as a
  revoke-and-restart for a compromised secret, if at all.
* **`plan_generation` semantics.** No decision needed — manually created plans
  are not functionality this product has, so nothing user-visible is lost by
  reading the session row. One backfill migration for pre-R5 plans.
* **Recon targets a scope, not hosts.** Recon acts on a scope and populates host
  entries from what it finds; there are no hosts to assign until it runs. No
  target model to build — `scope_id` / `plan_id` already carry it.

* **Auditors get a read-only agent.** The automatic consequence of role-based
  auth, and the desired one — an auditor's agent is read-only because the
  auditor is, everywhere and permanently, with no agent-specific rule to keep in
  sync.
* **One lifetime knob, not two.** "Renewal grace window" and "absolute cap"
  collapse into the same number once lifetime is session-bound: a key may be
  renewed at any point — expired or not — while its session is active and under
  its maximum lifetime. Past that, expiry is genuinely terminal and the operator
  starts a new session. **Maximum session lifetime: 168h**, matching today's
  `AGENT_KEY_MAX_TTL_HOURS` — not a new value, a new meaning.

## Where this ended

The consolidation is **done**, and it ended somewhere its first draft did not
predict — which is the honest outcome of auditing rather than executing.

**Landed:** one authorization model (the operator's project role, per request),
session-bound keys with recoverable expiry, the target declaration made visible,
per-route read roles, an auditor floor that makes read-only agents real, and the
capability system deleted outright.

**Deliberately not landed:** the workflow guards, and the table collapse. The
plan called for both; the audit said neither pays for itself, and one of them
would have removed a control that Phase 5 had just made load-bearing.

The through-line: **the agent surface no longer has its own authorization
model** — the thing this document set out to fix. `workflow` remains a real
boundary over the *record*, which is a different job from deciding who may act,
and it was only ever conflated with authorization because the same string
happened to gate both.

### If someone picks this up later

* The schema duplication is still there (12 columns × 4 tables). It is cost
  without a current symptom; the drift it caused is now guarded by
  `test_workflow_registration_is_complete.py`. Revisit on a second symptom.
* `api_keys` still carries four legacy scope columns beside `agent_session_id`.
  Dropping them needs `scoped_assist_session_id` / `scoped_scope_id` rewired to
  resolve from the session, since `get_current_agent` reads the columns
  directly.
* `plan_generation` remains the odd kind out: keyed by `TestPlan.id`, status
  derived live, no base row for historical plans. Fixing it is a backfill plus a
  decision about where plan status lives — not a refactor.
