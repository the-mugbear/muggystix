# Report templates — examples

These are **starting points to copy onto your own machine**, not something
BlueStick hosts or serves. The workflow is:

1. Copy a template into the directory you run your agent from:
   ```bash
   cp client-summary.md ~/work/acme-engagement/report.md
   cd ~/work/acme-engagement && claude    # or codex, etc.
   ```
2. Start an AI Assist session in BlueStick and connect your client to it.
3. Ask the agent to fill it in. It reads `report.md` off disk itself — the same
   working directory it already reads and writes — pulls the project data
   through the assist tools, and writes the finished document next to it.

BlueStick's part is the **data**: `assist_count_hosts` for every number,
`assist_list_hosts` with a `q=` query to isolate a set, `assist_get_host_findings`
for the evidence behind a claim, and the `report-context.ndjson` download when a
report spans more hosts than is sensible to fetch one at a time.

It does not store the finished report. That keeps it on the same footing as
every other artefact an agent produces: it lives in your working directory,
under your control, and what BlueStick retains is the audit trail of the calls
that produced it.

## Editing these

Placeholders are `{{like_this}}`. The HTML comments are instructions for the
agent — which tool supplies which section, and what not to invent. They are
meant to be **deleted** as the document is filled in; a comment left in is a
comment the client reads.

Write your own freely. Nothing here is a schema — the agent reads whatever
Markdown you give it.
