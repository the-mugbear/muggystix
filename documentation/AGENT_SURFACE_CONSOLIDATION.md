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

One constraint rules out the obvious alternative: **recon cannot be scoped by
host assignment, because the hosts do not exist yet.** Recon discovers them. So:

| Work shape | Target |
|---|---|
| Discovery (recon) | scope / subnet set — `scope_id` |
| Testing against known inventory | host set, or the plan's entries — `plan_id` |
| Review / assist | project-wide, no target |

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
it to **renew before starting anything it expects to outlast the key** — which is
the only approach that survives a long silent scan.

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
  and assist — **but not plan_generation**, so historical plans have none;
* it lists **every** plan, including ones a human wrote by hand with no agent
  involved.

`/test-plans/generate` already creates a real `plan_generation` `AgentSession`.
It is half-built; the reader ignores it.

**Open question for the owner:** switching to read that session row makes
plan-generation symmetric with the other three, and **manually-created plans
stop appearing on Agent Runs**. That is arguably correct — a human-authored plan
is not an agent run — but it changes what the page means. Decision required
before Phase 4.

---

## Sequence

Ordering is not negotiable. Removing the workflow guards **widens** what any key
can reach, so authorization must be in place first, and the deletions come last.

### Phase 1 — Make the key act as its operator *(additive, no behaviour change)*
* Resolve the operator's project role at agent-key auth. `key_operator_id` is
  already set at auth time; it just is not used for authorization.
* Apply the existing role dependencies to the agent routers **alongside** the
  workflow guards. Both active: a request must satisfy both.
* Reversible. Nothing widens; some things may narrow — which is the point, and
  is what Phase 1's tests must characterise.

### Phase 2 — Session-bound keys and renewal
* Add renew (not rotate) on the session; same token, later deadline.
* Bind key lifetime to session activity with an absolute cap.
* Teach the prompt to check `key_expires_at` and renew *before* long operations.
* Independently valuable: fixes a live gap for recon runs over 24h.

### Phase 3 — Target declaration
* Generalise `scope_id` / host-set as the session's declared target.
* Surface it on Agent Runs so a second analyst can see the range is taken.
* Record out-of-scope ingest against the session; do not reject.

### Phase 4 — Single session-start flow
* One dialog replacing three, declaring intent (label) + target.
* Keep minting the existing key kinds underneath so nothing breaks yet.
* Resolve the `plan_generation` question above.

### Phase 5 — The fail-closed flip *(the sharp edge)*
* Delete `LEGACY_WRITE_CAPABILITIES` and the capability machinery.
* **Breaks every legacy key the moment it lands.** Short TTLs mean the blast
  radius is hours, not weeks, but this is the step to land deliberately and
  announce.

### Phase 6 — Delete the split
* Remove the workflow guards; `workflow` becomes a label.
* Collapse the twelve shared columns; per-workflow tables keep only their 2–4
  real fields.
* Drop the four legacy scope columns from `api_keys`.

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

## Open questions

1. **`plan_generation` semantics** — should manually-created plans disappear
   from Agent Runs? (Blocks Phase 4.)
2. **Auditors** — read-only by role, so their agent is read-only automatically.
   Intended, or should auditors get no agent at all?
3. **Absolute key cap** — with session-bound lifetime, what is the maximum a
   session may live before it must be restarted? 168h is today's cap.
