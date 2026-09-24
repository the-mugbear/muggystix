# Report templates

Each folder here is one client report template. BlueStick lists every valid folder on the **Reports** page. Each project picks a default template, and each draft can switch to another at any time.

| Folder | For | Formats |
|---|---|---|
| `pentest` | The full penetration test report: every finding with its affected systems, evidence and recommendation. The default. | HTML, Word, QMD source |
| `executive-brief` | Leadership: the summary, findings at a glance and what to do, with no evidence or host lists. | Word, HTML |
| `remediation-worklist` | The people who fix things: one section per system with its findings, then how to fix each finding once. **The starter template — copy this one.** | HTML, Word, QMD source |

> Not to be confused with `documentation/report-templates/`, which holds Markdown files an **agent** fills in over MCP. That is a different mechanism, and BlueStick does not render those files.

## Adding a template

1. **Copy a folder.** Start from `remediation-worklist`. It has the smallest `template.json`, no images, no Word styles file and no post-processing script, and its `report.qmd` explains every part in comments.
   ```bash
   cp -r report-templates/remediation-worklist report-templates/my-template
   ```
2. **Name it.** The folder name must be lower-case letters, digits, `-` or `_`. Give `template.json` a new `title` and `description`: people choose a template by these, on the Reports page and on each draft.
3. **Edit `report.qmd`** and render it with the sample data until it reads right:
   ```bash
   cd report-templates/my-template && make        # needs Quarto and Python with jinja2
   ```
   To render without installing anything, use the report-worker image from the repository root:
   ```bash
   docker compose run --rm --no-deps --entrypoint "" \
     -v "$PWD/report-templates:/tpl" report-worker \
     python /app/app/services/quarto_render.py /tpl/my-template \
       --data /tpl/my-template/sample-data.json --out /tpl/my-template/_output
   ```
4. **Install it.** Put the folder in `report-templates/` on the server. The folder is mounted read-only into the backend and the report worker and is read on every request, so it is listed on the next page load, with no rebuild or restart. If a folder is present but **not offered**, a global administrator sees it on the Reports page with the reason, such as a bad folder name, a missing `template.json`, a manifest that doesn't parse, or an entry file that doesn't exist.
5. **Keep it.** `scripts/upgrade-instance.sh` carries the images a template declares under `assets` across upgrades. Your own template folder is part of the source tree: commit it, or copy it along with the rest.

Every issued report records a fingerprint of the template folder, so its history shows exactly which version produced it. Changing a template never changes a report that was already issued.

## The files

| File | Needed | What it is |
|---|---|---|
| `template.json` | yes | The manifest (below). |
| `report.qmd` | yes | The report: Quarto Markdown filled in by Jinja. The name is set by `entry`. |
| `sample-data.json` | for `make` and the tests | An example of the data a template receives. |
| `partials/*.qmd` | no | Reusable pieces, pulled in with `include`. |
| `reference.docx` | no | Word styles: fonts, headings, tables, title page, header and footer. Without it, Quarto's default Word styles are used. |
| `scripts/…` | no | A post-processing script for a format (`postprocess`). |
| `img/…`, `branding/…` | no | The template's own images and files, declared under `assets` and installed on the server, never committed. |
| `Makefile`, `.gitignore` | no | Local rendering conveniences. |

## `template.json`

```json
{
  "title": "Remediation worklist",
  "description": "What the template is for and who reads it.",
  "entry": "report.qmd",
  "formats": ["html", "docx", "qmd"],
  "postprocess": { "docx": "scripts/fix-docx-report.py" },
  "assets": [
    {
      "id": "logo",
      "path": "img/logo.png",
      "label": "Company logo",
      "description": "Where it appears and what size fits.",
      "required": false,
      "formats": ["html", "docx"]
    }
  ]
}
```

- **`title`, `description`**: shown when people choose a template. Say who the template is for.
- **`entry`**: the `.qmd` file in the folder to render (default `report.qmd`).
- **`formats`**: any of `html`, `docx` and `qmd` (a zip of the filled source with the data and filters). There is no PDF: export the Word file.
- **`postprocess`**: optional, one script per format, run on the rendered file (see `pentest/scripts/fix-docx-report.py`).
- **`assets`**: optional, the template's own images and files, each with a unique `id` and a `path` inside the folder. The Reports page lists each asset as installed or missing. A `required` asset that is missing blocks preview and issue. An asset with `"replaces": "reference.docx"` is used in place of that shipped file when it is installed. Inside the template, `asset("logo")` gives the path when the file is installed and an empty string otherwise.

## The data

A template receives one object. `sample-data.json` is a complete example.

| Key | What it holds |
|---|---|
| `report` | `kind` (`full` or `addendum`), `title`, `heading`, `number`, `draft`, `date`, `template`, `authors`, `baseline` (the report an addendum follows: `number`, `title`, `date`), `revision_of` |
| `project` | `name`, `start_date`, `end_date` |
| `engagement` | `client_name`, `classification`, `engagement_type`, `testers` (`name`, `role`, `email`), `distribution`, and written text: `system_description`, `applications`, `thick_clients`, `other_targets` |
| `executive_summary` | Written text |
| `scope` | `subnets` (`cidr`, `site`, `description`), `domains` (`domain`, `include_subdomains`) |
| `counts` | `critical`, `high`, `medium`, `low`, `info`, `total` |
| `severity_order`, `severity_labels` | `["critical", …, "info"]` and their display labels |
| `findings` | Worst first (severity, then CVSS score). Each has `ref` (F-01…), `title`, `severity`, `severity_label`, `status` (`confirmed`, `accepted_risk`, `remediated`), `status_note`, `cvss_score`, `cvss_vector`, `affected` (`address`, `hostname`, `name`, `port`, `state` — `Remediated` when that system is fixed), `affected_count`, `evidence`, `corroboration`, and written text: `description`, `impact`, `recommendation`, `steps_to_reproduce`, `references` |
| `delta` | Addenda only: `new_findings`, `findings_with_new_endpoints`, `withdrawn` (`ref`, `title`, `severity_label`, `reason`, `endpoints`). In an addendum, each finding's `change` is `new` or `new_hosts`, and `new_affected` lists the new systems. |

What a report includes: findings that are confirmed, accepted risk or remediated. False positives are dropped entirely. Open and retest findings are counted, never shown.

## Writing `report.qmd`

BlueStick fills the file with Jinja, using delimiters chosen so they never clash with Quarto:

| | Jinja usually | Here |
|---|---|---|
| Statements (`if`, `for`, `set`, `include`) | `{% … %}` | `<% … %>` |
| Values (printed) | `{{ … }}` | `<< … >>` |
| Comments | `{# … #}` | `<# … #>` |

Helpers:

- `md(f, "recommendation", todo="…")`: a finding's written Markdown. `md("executive_summary")` works the same for a top-level field. When the field is empty, the `todo` text is printed as a highlighted **TODO** instead.
- `todo("…")`: a highlighted, searchable "TODO: …" for anything the report still needs.
- `image(e)`: an evidence image (`e` from `f.evidence`).
- `asset("logo")`: the path of an installed template image, or an empty string.
- `plain(v)`: a date, number or reference printed unescaped. It refuses anything that would need escaping.

**The rules** that keep a report safe. The renderer enforces them, and `test_hostile_text_stays_text_in_every_format` checks every template in this folder against them:

1. **Every printed value is escaped.** A title of `*bold*` prints as those characters. So no data can become Markdown, a Quarto shortcode or HTML.
2. **Written text only through `md()`.** It is inserted after Quarto's own filters, with raw HTML, images, non-web links, headings and attributes removed. Never print written text as a value.
3. **Never print data in the YAML front matter.** Quarto expands shortcodes in metadata even when they are escaped. Name the data with `bluestick-meta` instead, as the shipped templates do for `title` and `subtitle`.
4. **Never print a value inside `` `code` `` or a code block.** Escapes are not undone there.
5. **Reusable parts are includes, not macros.** A macro's output is printed like a value and so escaped. Use `<% include "partials/_x.qmd" %>` with `<% with … %>` to pass it variables.
6. The template runs in Jinja's sandbox: no Python internals, and no includes outside the folder.

Things to handle in every template:

- **Addenda**: `report.kind == "addendum"`. Show what changed (`change`, `new_affected`, `delta.withdrawn`), not the whole report again.
- **Drafts**: `report.draft`. The shipped templates print a "Draft — not for distribution" callout.
- **Empty values**: a missing client name, no scope, no findings. Print a `todo()` or say so plainly; never leave a blank.

## Testing a template

- `make` renders `sample-data.json`. Try `make DATA=other.json` with a dataset saved from a real report.
- `backend/tests/test_report_templates_shipped.py` checks that every shipped template is offered with no problems, and what each one prints for a full report and an addendum. Add a test there for yours.
- `backend/tests/test_quarto_render.py` renders every folder here with hostile text in the title, the finding title, the summary and the written fields. Quarto only exists in the report-worker image, so run it there:
  ```bash
  docker compose run --rm --no-deps -v "$PWD/backend:/app" report-worker \
    sh -c "cd /tmp && python -m pytest /app/tests/test_quarto_render.py -q -p no:cacheprovider --rootdir=/app -c /app/pytest.ini --no-cov"
  ```
