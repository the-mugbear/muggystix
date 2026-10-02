# Report templates

Each folder here is one client report template. BlueStick lists every valid folder on the **Reports** page. Each project picks a default template, and each draft can switch to another at any time.

| Folder | For | Formats |
|---|---|---|
| `pentest` | The full penetration test report: every finding with its affected systems, how it was confirmed, evidence and recommendation. The default. | HTML, Word, QMD source |
| `executive-brief` | Leadership: the summary, findings at a glance and what to do, with no evidence or host lists. | Word, HTML |
| `remediation-worklist` | The people who fix things: one section per system with its findings, then how to fix each finding once. **The starter template — copy this one.** | HTML, Word, QMD source |

> Not to be confused with `documentation/report-templates/`, which holds Markdown files an **agent** fills in over MCP. That is a different mechanism, and BlueStick does not render those files.

## Adding a template

1. **Copy a folder.** Start from `remediation-worklist`. It has the smallest `template.json`, no images, no Word styles file and no post-processing script, and its `report.qmd` explains every part in comments.
   ```bash
   cp -r report-templates/remediation-worklist report-templates/my-template
   ```
2. **Name it.** The folder name must be lower-case letters, digits, `-` or `_`, start with a letter or digit, and be at most 64 characters (folders starting with `.` or `_` are ignored). Give `template.json` a new `title` and `description`: people choose a template by these, on the Reports page and on each draft.
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
5. **Keep it.** `scripts/upgrade-instance.sh` carries the images a template declares under `assets` across upgrades, both those installed in the folder and those uploaded on the Reports page (they live in `uploads/`). Your own template folder is part of the source tree: commit it, or copy it along with the rest.

Every issued report records a fingerprint of the template folder, so its history shows exactly which version produced it. Changing a template never changes a report that was already issued.

## The files

| File | Needed | What it is |
|---|---|---|
| `template.json` | yes | The manifest (below). |
| `report.qmd` | yes | The report: Quarto Markdown filled in by Jinja. The name is set by `entry`. |
| `sample-data.json` | for `make` and the tests | An example of the data a template receives. |
| `sample-evidence/*.png` | no | The images `sample-data.json` refers to, so `make` shows figures. Not part of a rendered report or its source zip. |
| `partials/*.qmd` | no | Reusable pieces, pulled in with `include`. |
| `reference.docx` | no | Word styles: fonts, headings, tables, title page, header and footer. Without it, Quarto's default Word styles are used. |
| `filters/*.lua` | no | The template's own Pandoc Lua filters, listed under `filters:` in the front matter after `_bluestick/fields.lua` (see `pentest/filters/spacers.lua`). |
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
  "images": { "fields": ["recommendation"], "trailing": false },
  "postprocess": { "docx": "scripts/fix-docx-report.py" },
  "assets": [
    {
      "id": "logo",
      "path": "img/logo.png",
      "label": "Company logo",
      "description": "Where it appears and what size fits.",
      "required": false,
      "formats": ["html", "docx"],
      "max_bytes": 2097152,
      "min_width": 400,
      "min_height": 100,
      "aspect": "3.4:1"
    }
  ]
}
```

- **`title`, `description`**: shown when people choose a template. Say who the template is for.
- **`entry`**: the `.qmd` file in the folder to render (default `report.qmd`).
- **`formats`**: any of `html`, `docx` and `qmd` (a zip of the filled source with the data and filters). There is no PDF: export the Word file.
- **`postprocess`**: optional, one script per format, run on the rendered file (see `pentest/scripts/fix-docx-report.py`).
- **`scope_inline_max`, `scope_domains_inline_max`** (v2.441.0): the most networks and domains the report lists itself (default 25 each). A project over either limit is not listed in the report. The template gets `scope.external` and prints the totals and the name and SHA-256 of a separate scope file, a CSV the operator downloads from the report's page and sends with it (see `pentest/partials/_scope_external.qmd`). Thousands of networks otherwise made a table that ran for pages. Set `null` for a template that prints no scope at all (`remediation-worklist`): it never names a file, and issuing never asks for one.
- **`evidence_records`** (default `false`): set `true` for a template that prints how each finding was confirmed. Only then does a finding carry `confirmations` (see The data): a template that does not ask gets none, and none is frozen into a report issued with it. `pentest` sets it; `executive-brief` leaves evidence out by design, and `remediation-worklist` is a list of fixes, so both set `false`.
- **`images`**: which evidence images your template prints. State it once, here; the report's page quotes it to authors ("This template prints no evidence images", "it prints only images placed in the recommendation").
  - `fields`: the written fields whose placed images it prints: `"all"`, or a list from `description`, `impact`, `recommendation`, `references`, `steps_to_reproduce` (`[]` for none). A field counts when the template prints it with `md(f, "field")` and without `images=False`.
  - `trailing`: `true` when it prints the images no field places (a loop over `f.evidence` with `image(e)`), else `false`.
  - Both keys are required when `images` is given; anything else keeps the template from being offered, with the reason. Left out, the template is taken to print everything (`"all"`, `true`), which is right only for a template like `pentest`.
  - `pentest`: `{"fields": "all", "trailing": true}`. `executive-brief`: `{"fields": [], "trailing": false}`. `remediation-worklist`: `{"fields": ["recommendation"], "trailing": false}`.
  - The declaration is checked against the `.qmd` for the shipped templates (see Testing a template); check your own the same way. What a given report prints is not read from this key: BlueStick fills the template with that report's data and notes what it asks for (see Images below), so a template that prints a finding's sections only under some condition is counted correctly.
- **`assets`**: optional, the template's own images and files, each with a unique `id` (lower-case, starting with a letter, at most 32 characters) and a `path` inside the folder. `label`, `description` and an optional `note` are shown on the Reports page, which lists each asset as installed or missing; `formats` says where it appears (`html`, `docx`; default both). A path must be an image (png, jpg, jpeg, svg, gif, webp) unless the asset `replaces` a file of the same type (`.docx` allowed). A `required` asset that is missing blocks preview and issue. An asset with `"replaces": "reference.docx"` is used in place of that shipped file when it is installed. Inside the template, `asset("logo")` gives the path when the file is installed and an empty string otherwise; `asset()` of an id that is not declared fails the render.
- **Uploading assets** (v2.431.0): a global administrator can also upload a PNG, JPEG or `.docx` asset from **Template files** on the Reports page instead of installing it on the server. The upload is stored in `uploads/template_assets/<template>/`, never in your folder. It wins over a server-installed file, applies to every project, and counts in the template fingerprint, so issued reports keep theirs. SVG, GIF and WebP assets can only be installed on the server: an SVG would carry script into the HTML report. The upload's type must match the `path` extension (`logo.png` takes a PNG). Optional guidance per asset, shown on the page and enforced on upload:
  - `max_bytes`: largest file accepted (default 5 MB for an image, 10 MB for a Word file).
  - `min_width` / `min_height`: fewest pixels accepted; set them from the printed size, about 250–300 px per inch of the box the image fills.
  - `aspect`: the shape of the place it fills, as `"W:H"`. A different shape is accepted with a warning, since it will be scaled to fit.

  A Word file must be a real `.docx` (no macros, not a `.dotx`) with its styles part.

## The data

A template receives one object. `sample-data.json` is a complete example.

| Key | What it holds |
|---|---|
| `schema` | The dataset version (`1`) |
| `report` | `id`, `kind` (`full` or `addendum`), `title`, `heading`, `number`, `draft`, `date`, `issued_at`, `template`, `authors`, `baseline` (the report an addendum follows: `number`, `title`, `date`), `revision_of` |
| `project` | `name`, `start_date`, `end_date` |
| `engagement` | `client_name`, `classification`, `engagement_type`, `testers` (`user_id`, `name`, `role`, `email`), `distribution`, and written text: `system_description`, `applications`, `thick_clients`, `other_targets` |
| `executive_summary` | Written text |
| `scope` | `subnets` (`cidr`, `site`, `description`), `domains` (`domain`, `include_subdomains`). Since v2.441.0 also: `totals` (`networks`, `ipv4_addresses`, `ipv6_networks`, `domains`, `sites`), `by_site` (`site`, `networks`, `addresses`; at most 15 rows plus "Other sites (n)"), `inline_max` / `domains_inline_max` (the cutoffs), `subnets_inline` / `domains_inline` (whether to list each), `external` (either is not listed), and `file` (`name`, `sha256`, `bytes`; `null` when nothing is external). Reports issued before v2.441.0 carry only the two lists, so test with `scope.get("totals")`. Numbers print with separators via `"{:,}".format(n)`. |
| `counts` | `critical`, `high`, `medium`, `low`, `info`, `total` |
| `severity_order`, `severity_labels` | `["critical", …, "info"]` and their display labels |
| `findings` | Worst first (severity, then CVSS score). Each has `id`, `_path` (its data path, which `md()` uses), `ref` (F-01…), `title`, `severity`, `severity_label`, `status` (`confirmed`, `accepted_risk`, `remediated`), `status_note`, `cvss_score`, `cvss_vector`, `affected` (`address`, `hostname`, `name`, `port`, `state` — `Remediated` when that system is fixed), `affected_count`, `images`, `placed` and `evidence` (see Images below), `confirmations` and `confirmations_omitted` (below), `corroboration`, and written text: `description`, `impact`, `recommendation`, `steps_to_reproduce`, `references`. In an addendum also `previous_severity` / `previous_severity_label` when the finding was re-rated since the baseline (else `null`). |
| `delta` | Addenda only: `new_findings`, `findings_with_new_endpoints`, `findings_with_changed_severity`, `withdrawn` (`ref`, `title`, `severity_label`, `reason`, `endpoints`). In an addendum, each finding's `change` is `new`, `new_hosts` or `severity_changed`, and `new_affected` lists the new systems. |

What a report includes: findings that are confirmed, accepted risk or remediated. False positives are dropped entirely. Open and retest findings are counted, never shown.

**What an addendum shows.** A finding the baseline report did not have (`new`); a reported finding on systems the baseline did not list (`new_hosts`, the new ones in `new_affected`); a reported finding whose severity is not the one the baseline gave it (`severity_changed`); and withdrawals. A finding that was re-rated AND is on further systems appears once, as `new_hosts`. Every re-rated finding carries `previous_severity` (`medium`) and `previous_severity_label` (`Medium`), so print "was Medium, now Critical" from those and `severity_label`. `delta.findings_with_changed_severity` counts all of them. Only severity is compared: a changed title or status is never a change an addendum reports, and nothing is compared by date. An addendum issued before this existed has neither key, so read them with `f.get("previous_severity_label")` and count re-rated findings from the list, as the shipped templates do. Say "nothing has changed" only when there is no new finding, no new system, no re-rated finding and no withdrawal.

**How a finding was confirmed** (`confirmations`, only with `"evidence_records": true`). The test results linked to the finding whose outcome is `finding`: there is no per-result "in report" mark, so a result that showed the issue is printed and one that did not (no finding, inconclusive, failed, informational) never is. Only results on a system the report lists for that finding are taken. Each entry has `_path`, `id`, `tool`, `host` (the system's address), `summary`, `command`, `output`, `output_truncated`, `executed_at`, `date`, `by` (the full name of the person who ran it, or of the operator whose agent did — never the session) and `by_agent`. At most 10 per finding, oldest first; `confirmations_omitted` is how many more there are. The command is cut to 600 characters and the output to 30 lines or 1,500 characters, with terminal colour codes and control characters removed. Issuing freezes them with the rest of the data. Reports issued before this have no such keys: read them with `f.get("confirmations") or []`. See `pentest/partials/_confirmations.qmd`.

A finding carries its `confirmations` only where the report prints them: when the template does not loop over a finding's list (pentest's addendum shows a finding the client already has as a heading and a table), that finding's list is emptied in the data, so neither the report's count of printed test results nor what an issue freezes includes them. Asking only whether there are any, or how many (`f.confirmations|length`), is not printing them.

## Writing `report.qmd`

BlueStick fills the file with Jinja, using delimiters chosen so they never clash with Quarto:

| | Jinja usually | Here |
|---|---|---|
| Statements (`if`, `for`, `set`, `include`) | `{% … %}` | `<% … %>` |
| Values (printed) | `{{ … }}` | `<< … >>` |
| Comments | `{# … #}` | `<# … #>` |

The front matter must set `engine: markdown` and list `filters:` with `quarto` and then `_bluestick/fields.lua` (your own filters after it). BlueStick copies that filter into the work folder but does not add it for you; it fills every `md()` field, `todo()` highlight and `bluestick-meta` key, so without it none of them appear.

Set `html-math-method: plain` under `format: html:` as the shipped templates do. A report never typesets a formula, so it never loads a math library: Quarto's default is MathJax from a CDN, which would be a request from the reader's browser and a script in the report. BlueStick passes the option on every HTML render whatever the front matter says; the line in the template keeps the Quarto source zip the same when someone renders it by hand.

Helpers:

- `md(f, "recommendation", todo="…")`: a finding's written Markdown. `md("executive_summary")` works the same for a top-level field. When the field is empty, the `todo` text is printed as a highlighted **TODO** instead.
- `todo("…")`: a highlighted, searchable "TODO: …" for anything the report still needs.
- `md(f, "description", images=False)` leaves out the images an author placed in that field (see Images below); `image_width="5in"` sets their width (default `6in`).
- `image(e, width="6in")`: an evidence image (`e` from `f.evidence`) as a figure. Like `md()` it prints only a placeholder: the filter builds the figure, taking the caption from `data.json`. Every figure is captioned "Figure N: caption", numbered by the filter in the order a reader meets them (see Images below). A template written when Jinja counted the figures (`image(e, number=counter.figure)`) keeps rendering: `number` is accepted and ignored. Table captions are still numbered by the template: see pentest's `counter` namespace in `report.qmd` and `partials/_table_caption.qmd`.
- `code(c, "command")`: a command line or a tool's output, verbatim (`c` from `f.confirmations`; `"command"` or `"output"`). Like `md()` it prints only a placeholder: the filter builds one code block from the string in `data.json`, which is never parsed, so a fence, a backtick or a shortcode in it is text. Nothing is printed for an empty value.
- `asset("logo")`: the path of an installed template image, or an empty string.
- `plain(v)`: a date, number or reference printed unescaped. It refuses anything that would need escaping.

**Images.** An image attached to a finding goes in the report when it is ticked "In report". Its author writes its caption on the finding's page, and may place it inside one of that finding's written sections by writing `![caption](evidence:<id>)` in the Markdown. The editor's Insert image button writes it with an EMPTY caption — `![](evidence:<id>)` — so the caption written on the image prints, and stays the one caption to maintain; text typed between the brackets overrides it for that placement. That one form is the placement: on one line, the target exactly `evidence:<id>`, optionally followed by a title in double quotes. Other spellings Markdown allows for the same image (`(<evidence:57>)`, a title in single quotes or parentheses, an alt text over two lines or with brackets in it, a reference-style image) place nothing: the Preview shows their alt text, exactly as the report prints it. They do not stay that way in a finding's text: when the section is saved — by a person, or by accepting an agent's proposal — `report_images.normalise_references` rewrites them to the one form, so the image is placed. (Text inside a code block or code span is left as typed.) What a template gets, per finding:

| Key | What it holds |
|---|---|
| `images` | Every image marked for the report: `attachment_id`, `file` (`evidence/57.png`), `caption` (the author's, else the file name), `placed_in` (the fields that place it), and what THIS report does with it: `printed` (`true` / `false`) and `printed_in` (the fields that print it; empty for one printed in the trailing block). |
| `placed` | Per field, the images its text places: `{"description": {"57": {"attachment_id", "file", "caption"}}}`. `md()` reads it; a template never needs to. |
| `evidence` | The images no field places, for a trailing evidence block: loop over it with `image(e)`. Empty when every image is placed. |

- A placed image prints inside its `md()` field as a figure. In a paragraph it splits the paragraph; in a list item it stays in the item; from a table cell it moves to just after the table (a figure in a cell is not framed in Word). The text in the brackets is the caption for that place; left empty, the image's own caption prints. The same image may be placed more than once; each place is its own figure.
- A reference to anything else (another finding's image, one that is not ticked, an id that does not exist, a web address) prints as its alt text, like any other image in written text.
- Figures are numbered "Figure 1, 2, 3…" through the report in the order a reader meets them, placed and trailing alike. The filter does it, because Jinja never sees a placed image.
- A template that prints a field but no evidence passes `images=False` (`executive-brief` does). A template with no trailing block (`remediation-worklist`) prints only placed images, in the fields it prints. Declare both in `template.json` → `images`.
- **A ticked image your template does not print is printed nowhere**, and the report's page says so before anyone issues: "3 in the text, 1 under Evidence", "This template prints no evidence images (4 ticked)", "2 ticked images are not printed by this template: it prints only images placed in the recommendation". BlueStick measures it per report: it fills the template with the report's data (Jinja only) and notes every `md()`, `image()` and `code()` the template asks for and every finding whose `confirmations` it loops over. So the count follows your template's own logic: an image on a finding the template lists in one table row (an addendum's already-reported finding, the worklist's accepted risks) is not printed, and is counted so.
- **Issuing copies only printed images** into the report's own storage, and needs only their files: a missing file refuses the issue only when the report would print that image. In the data an unprinted image keeps its entries with `"printed": false`; the renderer does not look for its file.
- Top-level text (`md("executive_summary")`) takes no images.
- A report issued before images could be placed has `evidence` alone. Read the others with `f.get("images")`. One issued before `printed` existed has no such key: every image it lists is needed, as before.
- `make` uses the template's `sample-evidence/` folder for the sample's images when it has one (`pentest` does): files named as in the data (`1.png` for `evidence/1.png`). Pass `--evidence DIR` to the renderer for another folder. Without the files, a draft render leaves the images out.

**The rules** that keep a report safe. The renderer enforces them, and `test_hostile_text_stays_text_in_every_format` checks every template in this folder against them:

1. **Every printed value is escaped.** A title of `*bold*` prints as those characters. So no data can become Markdown, a Quarto shortcode or HTML.
2. **Written text only through `md()`.** It is inserted after Quarto's own filters, with raw HTML, images, non-web links, headings and attributes removed. It is read as GitHub Markdown without math: `$HOME/bin:$PATH` prints as those characters, and `$…$`, `$$…$$` or a `math` code block is never a formula. A placeholder an author types (`::: {.bs-md key="…"}`, `{.bs-code …}`, `{.bs-figure …}`) is text too: that reader has no fenced divs or attributes. Never print written text as a value. The one image that survives is a reference to one of that finding's own images marked "In report" (`![caption](evidence:57)`), and only when the data's `placed` map lists it for that field. The filter decides from the data, never from the text.
3. **Never print data in the YAML front matter.** Quarto expands shortcodes in metadata even when they are escaped. Name the data with `bluestick-meta` instead, as the shipped templates do for `title` and `subtitle`.
4. **Never print a value inside `` `code` `` or a code block.** Escapes are not undone there. A command line or tool output goes through `code()`, the one way to print either.
5. **Reusable parts are includes, not macros.** A macro's output is printed like a value and so escaped. Use `<% include "partials/_x.qmd" %>` with `<% with … %>` to pass it variables.
6. The template runs in Jinja's sandbox: no Python internals, and no includes outside the folder.

Things to handle in every template:

- **Addenda**: `report.kind == "addendum"`. Show what changed (`change`, `new_affected`, `previous_severity_label`, `delta.withdrawn`), not the whole report again.
- **Drafts**: `report.draft`. The shipped templates print a "Draft — not for distribution" callout.
- **Empty values**: a missing client name, no scope, no findings. Print a `todo()` or say so plainly; never leave a blank.

## Testing a template

- `make` renders `sample-data.json`. Try `make DATA=other.json` with a dataset saved from a real report. Keep such datasets out of the repository: a saved report is client data, and `.gitignore` covers `sample-data-*.json` and `data-*.json` in a template folder. The scope-over-the-cutoff case is built in `test_report_templates_shipped.py` (`_with_scope`), not shipped as a file.
- `backend/tests/test_report_templates_shipped.py` checks that every shipped template is offered with no problems, and what each one prints for a full report and an addendum. Add a test there for yours. `test_report_templates_escaping.py` and `test_report_template_assets.py` cover escaping and the `assets` manifest.
- `backend/tests/test_quarto_render.py` renders every folder here with hostile text in the title, the finding title, the summary, the written fields (hostile image references included), an image's caption and a test result's command and output. The hostile text includes formulas in every spelling and author-typed placeholders: the HTML must hold no formula and no MathJax or KaTeX loader anywhere, head included, the Word file no equation, and no placeholder may be filled. It also pins how many figures each shipped template prints for that data (the `expected` map in `test_hostile_text_stays_text_in_every_format`); a template that is not in the map is held to everything else.
- `test_a_template_prints_the_images_it_declares_and_the_fill_measures_them` (same file) renders every folder here with an image placed in every written field of the first finding and one placed nowhere (`report_template_service.image_probe_dataset`), and requires three things to agree: the figures printed, `template.json` → `images`, and what BlueStick measures from a fill. `test_report_templates_shipped.py` checks the declaration without Quarto (`image_declaration_problem`). Quarto only exists in the report-worker image, and the tests that read this folder need it mounted and named, so run them as `scripts/check.sh` does:
  ```bash
  docker compose run --rm --no-deps -v "$PWD/backend:/app" -v "$PWD/backend/tests:/app/tests" \
    -v "$PWD/report-templates:/app/report-templates:ro" \
    -e REPORT_TEMPLATES_DIR=/app/report-templates -e BLUESTICK_SKIP_DB_INIT=1 report-worker \
    sh -c "cd /tmp && python -m pytest /app/tests/test_quarto_render.py /app/tests/test_report_templates_shipped.py -q -p no:cacheprovider --rootdir=/app -c /app/pytest.ini --no-cov -rs"
  ```
