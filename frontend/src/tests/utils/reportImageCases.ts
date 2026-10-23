/**
 * Review 2026-10-01 M1 — ONE grammar on every side, and ONE table: the rows
 * live in backend/tests/fixtures/twins/report_image_references.json, which
 * backend/tests/test_report_image_placement.py runs against the server's
 * `referenced_ids` / `code_spans` and these tests run against the client's
 * reader and preview (backlog 2026-10-22, H-d: the two cannot drift without a
 * test failing).  Add a case there, never here.
 *
 * Read by reportImages.test.ts (the pattern, the code spans) and
 * SafeMarkdown.test.tsx (the preview).
 */
import { twinCases } from '../helpers/twinCases';

const table = twinCases<{
  references: Array<[string, number[]]>;
  code_spans: Array<[string, Array<[number, number]>]>;
}>('report_image_references.json');

export const REFERENCE_CASES = table.references;
export const CODE_SPAN_CASES = table.code_spans;
