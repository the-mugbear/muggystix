---
paths:
  - "backend/app/services/client_report*.py"
  - "backend/app/services/quarto_render.py"
  - "backend/app/services/quarto_fields.lua"
  - "backend/app/services/report_*.py"
  - "backend/app/services/template_asset_store.py"
  - "backend/app/services/cvss_service.py"
  - "backend/app/services/systemic_insight_service.py"
  - "backend/app/db/models_reports.py"
  - "backend/app/api/v1/endpoints/client_reports.py"
  - "backend/app/api/v1/endpoints/reports.py"
  - "backend/app/api/v1/endpoints/export.py"
  - "backend/app/report_worker.py"
  - "report-templates/**"
  - "frontend/src/pages/Report*.tsx"
  - "frontend/src/components/reports/**"
  - "frontend/src/components/InventoryDownloadDialog.tsx"
  - "frontend/src/utils/reportImages.ts"
---

# Client reports (Quarto) and inventory downloads

`/projects/{id}/client-reports` (`client_reports.py`), page `/reports`. `client_report_service.py` (the dataset builder) and `quarto_render.py` (the renderer) are cohesive; leave them whole.

## Templates

- `report-templates/<name>/`: `template.json`, Jinja-filled `.qmd`, `sample-data.json`, optional `partials/`, `reference.docx`, `scripts/fix-docx-report.py`; `make` renders the sample. A change to what a template can do (key, helper, format, what needs source) updates `report-templates/README.md` and `pages/ReportTemplateGuide.tsx`.
- Ship: `pentest` (default), `executive-brief`, `remediation-worklist` (commented starter), and `contact-report` (`"kind": "contact"`, not a client template; `remediation.md`; extends `pentest`).
- `executive-brief`'s `reference.docx` and `scripts/fix-docx-report.py` are byte copies of `pentest`'s (`test_report_template_copies.py`): change both, or drop the pair from the test with the reason.
- Mounted read-only into backend and report worker from outside the `./backend` build context. Only the `report-worker` Dockerfile target has Quarto (pinned, sha256-checked); the others build `target: production`.
- `"extends": "<name>"` (`quarto_render.template_base` / `template_manifest` / `template_file` / `materialize`): own files over the base's; an omitted manifest key is the base's except `title`, `description`, `kind`. One level. Branding (images, uploads) never inherited. A missing, self-naming or chained base is a template problem.
- `report_template_service.template_problems` (`GET …/client-reports/templates/problems`, global admins) names folders that cannot be offered; `list_templates` skips them.
- Each shipped template is in the hostile-text test (parametrized over the folder) and `test_report_templates_shipped.py` (hazard — removable: derive its `SHIPPED` from the folder).

## User text never becomes `.qmd` source

Contract: `test_quarto_render.py::test_hostile_text_stays_text_in_every_format` (hostile command + output per template); Quarto tests run only in the report-worker image and `./scripts/check.sh` fails on their skip.
- Jinja sandbox, `<% %>` / `<< >>` / `<# #>`; every printed value is `escape_md` (also drops leading whitespace).
- Written Markdown is a placeholder (`md(f, "description")`) that `quarto_fields.lua` fills from `data.json` AFTER Quarto's filters, without raw HTML/blocks, images, non-web links, headings, attributes; the one image is `![…](evidence:<id>)` where `placed` lists it.
- Command / tool output only via `code(c, "field")` (a verbatim `CodeBlock`).
- No data in YAML front matter (shortcodes expand there even escaped); use `bluestick-meta`. Reusable parts are includes, not macros (macro output is escaped).
- A `---` block in written text is text (`-yaml_metadata_block`); the hostile test has a valid and a malformed one.
- Math off: reader `gfm-raw_html-tex_math_dollars-tex_math_gfm`, `clean()` returns Math to source, HTML gets `-M html-math-method:plain` (else MathJax from a CDN). A report makes no outbound request: an allowlist of what a rendered file may reference (`test_the_outbound_check_sees_what_it_must`).
- `quarto_fields.lua` reads fields with a trailing newline (else a table's last row drops); never `ipairs` over possibly-nil fields.
- The AI summary (`POST /reports/draft {report_id}`) reads `ClientReportService.content(report)` only (`report_draft_service.build_context`) (`test_report_review_2026_10_01.py`).

## What a report includes

- Confirmed, accepted-risk, remediated findings; false-positive endpoints dropped; open findings counted only.
- Issuing (project admin) numbers it and freezes `reports.snapshot` (dataset + cumulative `reported`). `reported` is read only via `client_report_service.reported_entries` / `_ledger`, never by loading `snapshot` (`test_review_2026_10_23b_reports.py`).
- Issuing freezes all a render reads under `uploads/client_reports/<project>/<report>/`: printed images (`evidence/`), the template with base and uploads (`freeze_report_template` → `template/<name>/`, `snapshot["template"]`; `_bluestick/fields.lua`, `fields_sha256`, `frozen_fields_filter`), the scope file (`scope/`); issued renders read only these (`test_review_2026_10_23_reports.py`). Anything new a render reads is frozen too (hazard — removable: render an issued report with live inputs removed). Older reports: fingerprint check / the release's filter.
- **Addenda** compare by finding AND endpoint with the baseline's `reported`; the one finding-level change is severity (`change: "severity_changed"`, `previous_severity` / `previous_severity_label`, `delta.findings_with_changed_severity`); also on new endpoints → once, `new_hosts`. Never title or status; no stored severity = unchanged.
- An addendum's baseline is fixed at creation (`POST` `baseline_report_id`, details via `settings_carried_from`); a stale baseline is refused at issue ("start a new addendum").
- **A large scope is never printed.** Over `scope_inline_max` / `scope_domains_inline_max` (25; `null` = none): totals + name + SHA-256 of a CSV (`report_scope.py`, `GET /client-reports/{id}/scope.csv`). Draft: `ClientReportService.live_scope` (`_scope_block`, no build); issued: stored, a SHA mismatch is a logged 500. Formula-escaped. Test every scope list against the cutoff (`test_report_scope.py`).
- `confirmations`: `finding`-outcome evidence on listed systems (≤ 10, capped) when `"evidence_records": true` (`ReportTemplate.evidence_records`, one reader; `pentest`). Frozen; not in `reported`.
- Warnings only: `summary.internal_references` (the proposal rule, `triage-tests-proposals.md`); `summary.uncaptioned_images` (`report_images.caption_missing` narrowed by `printed`; `finding_needs`' `uncaptioned_images` is template-independent).
- Report text (`description`…`cvss_score`): any analyst; each edit a history line (`finding_actions.record_edit`). A 3.x/2.0 vector sets the score (`cvss_service`).

## Reports pages and engagement details

- `Reports.issuedState`: "Latest" (`latest_issued_id`), "Superseded", else a dash; a draft's line names `overridesLine` and a non-default template; `/reports/defaults`, `/reports/templates` are pages. New report asks title + template and points at a same-kind draft, never refuses.
- "Before issuing" follows `can_edit`; counts are ONE function, `ReportChecklist.checklistCounts(summary)` (groups, badge, bar, `review-clear`; `ReportDetail.checklist.test.tsx`). Drafts' report-text images readable by all readers; issued: copies.
- `reports.settings` = overrides: text with text; `testers` / `distribution` present (`[]` = empty override); blank dropped. New draft `{}`; addendum / revision: what the baseline was ISSUED with (`settings_from_issued`).
- `report_defaults.resolve` is the ONE order (`apply_overrides`; `DETAIL_KEYS`, `LIST_KEYS`, `INSTALLATION_KEYS`): override → `report_profiles` → `report_defaults` (author, disclaimer) / `project_team` (`test_report_defaults.py`). `engagement` = resolved. No disclaimer: the template's.
- `PATCH …/{id}` `settings` = changed keys (`EngagementOverrides`, `exclude_unset`; null / blank follows, omitted keeps), never the whole map. Page: `detailModel.toSave`; `report_defaults.drop_followed` drops sent keys equal to the followed value, never `[]` (`test_report_workspace_overrides.py`).
- `ReportOut.settings` = overrides; single read adds `effective`, `followed`, `followed_from` (`project` / `installation` / `project_team` / null; `report_defaults.followed_with_sources`).
- `components/reports/workspace/`: details as TEXT, never a disabled field; Edit → Done / Cancel; Save draft the ONE save. `detailModel` the one reading of customised / pending / sent (`detailModel.test.ts`); `useDetailEdits`, `DetailSections`, `focusDetailField`. Unsaved changes hold Issue and every exit (`hooks/useDiscardGuard.useLinkGuard`); Preview saves first.
- **Push-up** (`UpdateDefaultsDialog`, `GET …/{id}/followers`) = `POST …/{id}/promote`, per key `expected` (draft lock) + `expected_project` (`_write_profile`'s `check`, profile lock): mismatch 409; one transaction (draft, then profile); `can_edit` (`ReportDetail.pushUp.test.tsx`); empty team 422.
- `/reports/defaults` sends `PATCH …/profile`, changed keys only. `_write_profile` is the ONE writer of `report_profiles` (hazard — removable: a test nothing else writes it); it and `report_defaults.save` `INSERT … ON CONFLICT DO NOTHING`, then lock (`test_report_defaults_first_write_race.py`).
- Executive summary = `report_profiles.executive_summary`. An addendum's is OWN (`report_defaults.OWN_IN_ADDENDUM`, `settings_carried_from(with_summary=False)`, promote 422). Top level (`md("executive_summary")`), not `engagement`: issued details = `ClientReportService.issued_details`. `ReportOut.executive_summary` single-read only.
- Following the system description places what the defaults place (`report_text_placements`: `"defaults"`).

## Images

- `services/report_images.py` is the one place (`report_placement` → `images` / `placed` / `evidence`). `report_images.REFERENCE` == `EVIDENCE_REFERENCE` (`frontend/src/utils/reportImages.ts`), one case table `backend/tests/fixtures/twins/report_image_references.json`. Browser scans linear (`utils/markdownScan.ts`, `evidenceReferenceAt`); measured and left alone: server `REFERENCE` quadratic on `![`.
- Read via `referenced_ids` / `referencedImageIds`, never `REFERENCE.finditer`: code is no placement (`report_images.iter_placements`, `code_spans` / `codeSpans`). `normalise_references` on save.
- Opt-in `note_attachments.include_in_report`; a finding's image only in its own sections.
- `refuse_if_placed` (delete, un-tick, note deletion) → 409. `check_references`: foreign 422 at propose, un-ticked 409 at accept; never tick for a proposal (`test_report_image_placement.py`).
- `note_attachments.caption` (uploader / project admin / global admin); ticked-unplaced images print under Evidence.
- `quarto_fields.lua` shows only what `placed` lists and builds and numbers every figure; `image(e)` is a placeholder (`number=` ignored); no caption in the `.qmd`.
- `client_report_service.finding_image_attachments(…, marked_only=)` is the ONE finding-image lookup (`report_images.attachments_of`, the drafter: `False`).
- Evidence: `evidence_text` (sixth `REPORT_TEXT_FIELDS`; not required or drafted), test results, unplaced images after their note's text (`evidence[].note`, `_note_preambles`); `summary.evidence_notes`; `_place_evidence` re-stamps `_path` (`test_report_evidence_section.py`).
- Report-own images: `note_attachments`, `annotation_id` NULL + `project_id` (`ck_note_attachment_owner`), `POST /client-reports/images`. `report_images.project_report_images` (`test_report_own_images.py`); placed only by `ENGAGEMENT_IMAGE_FIELDS` (`executive_summary`, `system_description`; `DetailSections.IMAGE_DETAILS`) as `engagement.images` / `engagement.placed` (`field_images`); `report_images.engagement_text_key`. Kinds never cross. `NoteAttachment` reads on `annotation_id` / `Annotation` expect NULL (hazard — removable: test every attachment read with one present).
- Placed by a draft or defaults: no delete (`refuse_if_report_text_places`), no un-tick. Placed only by an issued report: deletable; a revision / addendum restores report-text ones via `ClientReportService.settings_carried_from` from the sha-checked copy, `note_attachment_service.store_image_bytes` (ONE writer; hazard — removable: a test nothing else writes one), `commit=False` in the draft's transaction under `lock_issued_for_carry` (a failure leaves no row or file — `after_rollback`; a concurrent carry waits and reuses), then `report_images.rewrite_reference_ids` (placements only). No copy: the reference stays, `summary.unresolved_images` names it.

## What a report prints is measured

- `quarto_render.printed_parts` fills (Jinja only); `client_report_service._mark_printed` sets `images[].printed` / `printed_in`, empties unread `confirmations`, feeds `images_printed`, `images_trailing`, `images_not_printed`, `evidence_records`.
- `template.json` `images {fields, trailing}` is the author's statement the page quotes (`executive-brief` none; `remediation-worklist` recommendation only, no trailing); declaration == fill == figures (`test_report_templates_shipped.py`).
- Issuing copies, and requires the files of, only PRINTED images — never "ticked".
- A template that cannot fill THIS data refuses the issue (409, before numbering): `issue()` builds with `issuing=True`, so `_mark_printed(strict=True)` raises `RenderError`, turned into `ReportStateError`; ONE fill per issue (`test_issuing_fills_the_template_once`). `_mark_printed`'s `None` is a draft summary's only; Quarto's own failures show at render.
- A test's fake template must call `md()` / `image()` / loop `confirmations`, or nothing prints, counts or freezes.

## An issued report owns its copies

- `freeze_report_images` copies printed images to `evidence/` and writes `report_images` in the issue's transaction; the issue render reads only those, hash-checked; a missing file refuses the issue. Reports issued before copies read live attachments.
- `report_images.attachment_id` is NOT a foreign key (the snapshot's key; outlives the attachment).
- `discard_report_copies` (evidence/, template/, scope/; `discard_report_images` the evidence part) runs only under the report's lock, in `issue()` and draft deletion — never after a rolled-back issue (another request may have committed copies). Each freeze empties its folder first. Drafts only.

## Locks and processes

- Draft PATCH / DELETE `FOR UPDATE` (`_load_for_change`); `issue()` fingerprints the locked row (`test_report_review_2026_10_01.py`).
- Renders end the read transaction before Quarto (`quarto_render.render(before_quarto=db.rollback)`; `client_report_render._render`, `remediation_report.run_job`).
- AI drafting before the provider call (`ReportDraftService._end_read`; `test_review_2026_10_23b_reports.py`); any new LLM call too (hazard — removable: a test over every provider route).
- Quarto and the post-processor run in their own process group (`quarto_render._run_group`); a timeout kills the group (the `quarto` launcher does not `exec`).
- Generic `/reports/jobs/{id}…` 404 a page-owned job: `app/api/report_jobs.job_or_404` (doors pass `owner=`) is the ONE lookup, `job_file_response` the ONE file server; previews via `/client-reports/{id}/previews/{job}`, contact documents `/remediation/contact-report/{job}` (the switch's 404). Dismiss / retry / cancel: analyst or requester (`test_review_2026_10_23_reports.py`).
- `report_job_service.PAGE_OWNED_REPORT_TYPES` (`client`, `remediation`): no "report ready" notification, not in `/reports/jobs`.
- An issue render's failure write is fenced on its claim (`client_report_render._still_claimed`; `test_review_2026_10_23_reports.py`).
- The worker claims issue renders before previews and supersedes a requester's repeated preview (`_supersede_repeated_previews`). `on_progress` = `worker_loop.render_checkpoint`: heartbeat; on SIGTERM raises `WorkerStopping` (a BaseException, so no `except Exception` fails the job); `report_job_service._run_job` releases it to `queued` (`job_transitions.release`) (`test_report_worker_stop.py`).
- Preview freshness: `report_jobs.inputs_fingerprint` and `GET …/{id}/inputs` are both `client_report_render.inputs_fingerprint`; all a draft's render reads goes in, today's date does not (`test_report_preview_freshness.py`); a failed comparison is "could not verify". `live_evidence_resolver(db, project_id, dataset)` reads images a thousand per statement (`test_report_inputs_fingerprint_cost.py`).
- The report worker imports all model modules (hazard — removable: import `app.report_worker` alone and configure mappers).

## Template files (assets)

- Declared in `template.json` `assets`; `asset("id")` gives a path only when installed; a missing REQUIRED one is 409 for preview / issue / render. `upgrade-instance.sh` carries installed ones. `replaces` (`branding/reference.docx` → `reference.docx`) takes that file's place.
- Uploads (global admin) → `template_asset_store` (`uploads/template_assets/<template>/`): `<asset>.<sha16><ext>`, then the `.json` naming it (which makes it count); `save` / `remove` `flock` `.<asset id>.lock` (never deleted or listed); the route is `async` only to read; save + audit commit in `run_in_threadpool` (`test_report_template_uploads.py`).
- `quarto_render.is_template_name` / `is_asset_id` (fullmatch) / `is_skipped_part`, one rule each; asset routes 404 an inexact template path and use `template.name`.
- **Bytes are checked, never names:** PNG/JPEG header + pixel size; a macro-free `.docx` with no external relationship but a hyperlink and no header/footer `INCLUDEPICTURE` / `INCLUDETEXT` / `LINK`, parts parsed with defusedxml, never matched as text (`test_report_template_uploads.py`). SVG/GIF/WebP server-only (SVG = script). Guidance: `max_bytes`, `min_width`, `min_height`, `aspect`.
- The renderer places `asset_files` ({id: path}) in its private copy (`_place_overrides`, then `_apply_replacements`); the template folder stays read-only. `fingerprint` (files, base, uploads) is provenance, not the freeze; checked only for pre-copy reports.

## DOCX and layout

- Formatting lives in `reference.docx` styles; `fix-docx-report.py` only frames screenshots, keeps captions with tables, turns page breaks into page-breaks-before, and swaps drawings named `bluestick-<asset id>` (`logo`, `cover`) and the run `CLASSIFICATION` (`word/header*.xml` / `footer*.xml`, ← `engagement.classification`) — keep those names. `Table` style `jc` centres. No PDF.
- **A `{{< pagebreak >}}` never stays a paragraph of its own** (2.534.0, `page_breaks_before_their_paragraphs`): Quarto writes it as a paragraph holding only a page-break run, which, landing at the top of a page, breaks again — a blank page, and LibreOffice's measured page a page off Word's at every finding that wrapped differently. Each becomes `pageBreakBefore` on the paragraph after it (a no-op at the top of a page in both); one before a table or at the end stays. Run after the bookmarks are in their headings (`test_a_page_break_becomes_a_page_break_before_on_the_paragraph_after_it`).
- **A framed screenshot carries no list `numPr` / `ind`, nor does its `Image Caption`** (2.534.0): inside a list item Pandoc gives both the continuation numbering, whose indent applies INSIDE the exact-width frame cell. The framed paragraph keeps with next explicitly. Never lift the figure out of the list in the Lua: that ends the list and the next step restarts at 1 (`test_a_framed_figure_keeps_with_its_caption_and_carries_no_list_indent`).
- Table, Figure, Listing: three counts made by the Lua filter (one `Pandoc` pass), never in Jinja. A `Table:` paragraph after an author's table is its caption (`caption_tables`); `Listing:` after an author's code block → `bs-listing-caption` (`Table Caption` style); `code()` output is uncounted. Template tables are named by heading or `::: {custom-style="Table Label"}`. `legacy_caption` (template-numbered captions) stays indefinitely.
- `pentest/partials/_table_caption.qmd` (`::: {.bs-table-caption}`), used by no shipped template, is KEPT for extending templates; it leaves only with a `template_problems` entry for them.
- Captions print UNDER tables: `captions_under_tables` runs first, before fields are filled; numbering after. Word: `keep_table_captions_with_their_tables` overrides keep-with-next per paragraph and keeps the last row (and Source Code) with the caption. HTML: `table:has(+ caption)`, `.35em` / `1.25em`; preview `caption-bottom`.
- The editor writes empty `Table: ` / `Listing: ` lines (`TABLE_CAPTION_LINE`, `LISTING_CAPTION_LINE`), never a placeholder word; "Insert image" an empty reference so the stored caption prints. `utils/markdownEditing.tableCaptionOf` / `listingCaptionOf` twin Lua `caption_text` (hazard — removable: a shared case table in `backend/tests/fixtures/twins/`).
- Word links: `put_bookmarks_in_their_paragraphs` (no whitespace tails come along), then `word_safe_bookmark_names` (Word's naming rule; moves links and REF / PAGEREF; `_`-names stay), before the TOC; unverified in Word — in LibreOffice they jump right (UNO `.uno:OpenHyperlinkOnCursor`, 2026-10-23). Pandoc ≥ 3.12.1 does both itself; when Quarto ships it, both steps go.
- **The table of contents is written INTO its field in Word's shape** (`fill_table_of_contents`): first entry in the field's paragraph after begin / instrText / separate, END closing the last entry's paragraph, never an empty result paragraph — Pandoc's shape made LibreOffice put the entries OUTSIDE its index (empty grey index, duplicates on update; reported 2026-10-23). NO request to rebuild at opening: `w:dirty` is removed and `w:updateFields` is never written — Word rebuilt before it had a layout and printed "1" on every line, LibreOffice ignored it.
- **Page numbers come from a layout, never guessed.** Each entry with a bookmark carries a dotted right tab at `text_width` and a PAGEREF field (Word's shape, so "Update field" works) to a hidden `_Toc<n>` bookmark of its OWN, written around the heading's text (2.534.0, `_bookmark_around_text`, ids above Pandoc's) — never the heading's own bookmark, which the summary tables link: Word replaces the `_Toc` bookmarks when it rebuilds the table, and a reviewer saw the summary links die after an update while the two shared one; `measure_toc_pages` runs this same script under `/usr/bin/python3 --measure-toc` on a COPY in headless LibreOffice (report-worker image only), `index.update()`, and `write_toc_page_numbers` writes the numbers into OUR file — all or nothing, only when the measured entries pair with ours heading by heading. LibreOffice's save is never kept (the Word design stays ours). No LibreOffice, a failure or a timeout (`MEASURE_TIMEOUT_SECONDS`) leaves the entries unnumbered; the render never fails for it. LibreOffice substitutes Aptos, so a number can differ from Word's by a page; the draft page says to update the table once in Word (`test_quarto_render.py::test_the_rendered_sample_numbers_its_table_of_contents`, which skips — and so fails the gate — where `soffice` is not present).
- Corroboration is empty unless `"corroboration": true` (`ReportTemplate.corroboration`; `test_report_corroboration_opt_in.py`).
- pentest links findings by section number (`[-@sec-finding-<id>]`); `contact-report` keeps `#finding-<id>`.
- `bluestick-meta` sets `author` from `engagement.author_name`; HTML adds "Prepared by". Key/value tables 1:3 via a separator over 72 characters — and EVERY pipe table's separator is over 72, or Word sizes it to its contents (`test_every_pipe_table_separator_is_wide_enough_for_pandoc_to_size_its_columns`, every data shape).
- Word styles the render depends on (2.533.1, `test_code_headings_and_figure_captions_have_their_word_styles`): `Source Code` (ours wraps; without it Pandoc's default turns word wrap OFF) and `Verbatim Char` (Consolas — the run style every code run and token style is based on; empty, code prints in the body font), `Image Caption` with space after it, Heading 3–5 bold and sized below 1–2. Labels over a grid or a code block ("N systems are affected.", "Command:", "Output:") are `Table Label`, like the bold ones.
- Format `qmd` is the source zip; written text stays in data.json.

## Presentation mode

- Reads ONLY an issued (or superseded) report's frozen dataset (`ClientReportService.content`) and its frozen image copies — never a live finding, attachment or note; a draft is a 404. Auditor and above, on the client-reports floor.
- `services/presentation_slides.py` is the ONE slide list (pure; per report and host filter); `presentation_layout.py`'s `STAGE` is the ONE statement of the stage's sizes (sent to the browser). Only `printed` images are slides; a missing copy is "not available". Endpoint pages count the filtered list's own length.
- A finding's `status_note` and an endpoint's State column appear only when the report's template prints them: `template.json` `status_notes {findings, endpoints}` (`ReportTemplate.status_notes`; omitted = neither), read from the FROZEN copy (`presentation_service.status_notes`; a copy without the key → the installed template of that name). Declaration == fill for every shipped template (`test_report_templates_shipped.py::test_the_status_declaration_is_what_the_template_prints`). The Playwright fixture is built with both shown (the widest table).
- `services/presentation_service.py`: only a move, Take control and End raise `revision`, each one conditional UPDATE (a move: `driver_id` AND `driver_client_id`, at the revision seen, live); presence (`presentation_viewers`) never does; a Display's acknowledgment is stored only if newer. Answers never carry a tab id (`test_presentation_mode.py`).
- Pagination tests: `test_presentation_slides.py` (budgets, splits, nothing cut); the Playwright fixture is pinned by `test_presentation_e2e_fixture.py`.
- **Agendas** (2.531.0): items are anchors; `presentation_slides.slide_anchors` is THE rule for an item's pages (a section's placed images and `hosts` endpoint pages included) — the browser only matches `slide.anchors`. Every save is a version (`save_agenda`, row lock + `expected_version`); the agenda state is part of the position, written only by `move` (`check_agenda`, agenda row FOR SHARE); a delete (FOR UPDATE) is refused while a live presentation pins it and clears ended ones' reference (FK NO ACTION). `test_presentation_mode.py` (E3 block).
- **Cost per heartbeat** (review 2026-10-24, 2.532.1): `state()` is read by every tab every second — watchers with their people in one statement, the presentation's people in one, the agenda in one join (`test_the_answer_costs_the_same_for_two_watchers_or_twenty`); `frozen_images` is remembered per report for `FROZEN_IMAGES_TTL_SECONDS` (it keys the slide cache on every move and acknowledgment). "Live" for an agenda delete is `_recently_active` too (`_live_pinning`). A layout report's key must be a slide of the report (422).
- **Review 2026-10-24b/c** (2.533.0): `state()` reads only viewer rows within `DISPLAY_LISTED_SECONDS` (in SQL — a tab id is per page load, so the rows are history), and an end (`end`, `_expire_if_idle`, which now audits `presentation_ended` with no actor) deletes the presentation's viewer rows (`_forget_viewers`). The slide cache is touched only under `_CACHE_LOCK` (sync routes share it across threadpool threads; a build runs outside it). An agenda rename is a version (`save_agenda`: the version is the agenda's one concurrency token). `LAYOUT_REPORTS_PER_HOUR` per person per report (`font` is client text). A cell taller than a slide is cut by binary search (`_words_that_fit`; old == new in `test_presentation_slides.py`).
- **Telling the team** (2.532.0, decided 2026-10-24): Start (creating, not joining) notifies active auditor+ members but the starter (`notify_presentation_started`, `type: presentation`, `source_type: report_presentation` → `/reports/<id>/present`); `GET /presentations/live` (`live_for`) is the bell's glow, membership-based, archived excluded, filtered by `_recently_active` — the ONE idle rule (`_expire_if_idle` is its negation). `test_presentation_mode.py`.
- **Spotlight** (2.531.0): the move's predicate at the CURRENT revision, own `spotlight_seq`, never the revision; answered only while it matches the revision with time left (`SPOTLIGHT_MS`). `rendered_agenda_mode` (plan E1) is not built: nothing a Display draws depends on the mode.

## Host inventory downloads

- Hosts → Download inventory: streamed CSV (`GET /reports/hosts/csv`, `ReportGenerator.iter_inventory_csv`) and a JSON job (`POST /reports/jobs?format=json`, `write_json_report`; `report_type=inventory` omits roll-ups); `ReportGenerator` is only these. `components/InventoryDownloadDialog.tsx` + `utils/inventoryFilters.ts` name every active filter.
- "Active Findings" / "Critical Findings" and `dossier_summary.active_findings` are `host_detail_service.active_finding_counts` (`severity="critical"`), never endpoint rows; the CSV only counts observations and notes (`_inventory_row_counts`), hosts in id order (`test_review_2026_10_23b_reports.py`).
- No export is capped; `report_jobs.truncated` is unused (kept until someone asks for the migration); a retired format fails saying so.
- A route that queries while streaming calls `disable_statement_timeout(db)` and is listed in `test_review_2026_10_01_integration.py`.
- The Posture briefing (`/reports/posture.html`) is `posture_briefing.render_posture_briefing` over `compute_posture` (same keys, `posture_focus`), never a second roll-up; printable, no script or outbound reference, escaped (`test_posture_briefing.py`).
- AUDITOR: client reports, `/reports/*` exports, `hosts/tool-ready`, `names/export`, the Posture briefing.

## Decided — do not ask again

- `executive-brief` keeps copies, not `"extends": "pentest"`. The image preview stays client-side.
- The executive summary is project-level (`c9e4a7b2d6f1` dropped `reports.executive_summary`) and places own images, never a finding's; details follow the project unless overridden (`f3a9c1e7d5b2`).
- Addendum: its own summary (may be revisited); no re-point; severity the one finding change.
- Push-up exists. A note's text prints before its images (over text-only and per-image opt-in).
- Only author tables captioned, captions under, none from templates; listings counted apart. Corroboration opt-in.
- pentest: header CVSS score | Vector; no Ref, Status, Port or Note columns (data keeps `status_note`, `port`, `state`; `contact-report`, `executive-brief` print theirs); no Evidence section with nothing in it (no TODO), no empty References; the author is on the title page.
- Presentation mode: issued (and superseded) reports only, never a draft (decided 2026-10-23, "for now"); auditor and above, viewers never; one driver at a time, Take control in one click; no share link for non-members; a one-second poll, not WebSockets / SSE, until the lag is felt in use.
- Presentation mode shows a finding's status and an endpoint's state only where the report's template prints them (decided 2026-10-24, option c — over "always" and "hide when tracking is off").
- Generic job routes never serve a page-owned job. One Posture briefing, the story /posture tells.

## Removed on purpose

- The HTML host report, agent package, Markdown bundle, any host "report" (people get a client report or a contact's list).
- `REPORT_MAX_HOSTS` / `REPORT_MAX_INMEMORY_HOSTS`, `GET /reports/limits`, the `truncated` flag, `X-Report-Truncated`.
- PDF; `#tbl` / `#fig` blocks (layout tables in DOCX); header-parsing Lua helpers; the Jinja figure / table counters.
- `ExportService` (plan execution report).
- The systemic briefing (`/reports/systemic.html`, `report_templates.py`, `?site=`), Segments' per-site briefing.
- Whole-profile `PUT …/profile`; "Set for the project"; the `edited` list; `PATCH baseline_report_id`.
