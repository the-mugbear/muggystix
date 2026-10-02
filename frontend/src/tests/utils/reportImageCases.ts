/**
 * Review 2026-10-01 M1 — ONE grammar on every side.  The same table, row for
 * row, is REFERENCE_CASES in backend/tests/test_report_image_placement.py:
 * change both together.  The second half are spellings pandoc reads as an
 * image; they are not placements (the Preview shows none, the report prints
 * none there) until `normalise_references` has rewritten them on save.
 *
 * Read by reportImages.test.ts (the pattern) and SafeMarkdown.test.tsx (the
 * preview).
 */
export const REFERENCE_CASES: Array<[string, number[]]> = [
  ['Before ![The relayed session](evidence:57) after', [57]],
  ['![t](evidence:57 "a title")', [57]],
  ['![a \\] bracket](evidence:57)', [57]],
  ['![x](evidence:57){.c onerror=alert(1)}', [57]],
  ['![]( evidence:57 )', [57]],
  ['![a](<evidence:57>)', []],
  ["![a](evidence:57 'single quotes')", []],
  ['![a](evidence:57 (parentheses))', []],
  ['![two\nlines](evidence:57)', []],
  ['![a [nested] b](evidence:57)', []],
  ['![a][shot]\n\n[shot]: evidence:57', []],
  ['![shot][]\n\n[shot]: evidence:57', []],
  ['![shot]\n\n[shot]: evidence:57', []],
  ['![a](evidence:57 "t" extra)', []],
  ['![a](EVIDENCE:57)', []],
  // Review 2026-10-02 H5 — code is shown as typed, so a reference written
  // in an inline code span or a fenced code block places nothing; one
  // beside the code still does.
  ['`![Example](evidence:57)`', []],
  ['Write ``![Example](evidence:57)`` to place it.', []],
  ['```\n![Example](evidence:57)\n```', []],
  ['~~~markdown\n![Example](evidence:57)\n~~~\n\n![real](evidence:58)', [58]],
  ['```\n![Example](evidence:57)', []],
  ['`code` ![real](evidence:57) `more`', [57]],
];

/**
 * More of the same rule, on this side only (each answer was checked against
 * the server's `referenced_ids`): the shapes an author is likely to type.
 */
export const CODE_REFERENCE_CASES: Array<[string, number[]]> = [
  ['Run `id` first. ![The session](evidence:57)', [57]],
  ['``a ` tick ![x](evidence:57) ``', []],
  ['`![typed](evidence:57)` and placed: ![shown](evidence:57)', [57]],
  ['A lone ` tick, then ![shown](evidence:57)', [57]],
  ['```\ncode\n```\n\n![after the fence](evidence:57)', [57]],
];
