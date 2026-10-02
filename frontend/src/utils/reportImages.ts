/**
 * Images in a finding's report text — the client-free rules, shared by the
 * editor, the preview (`SafeMarkdown`) and the finding page.
 *
 * A finding's image, ticked "In report", may be PLACED inside one of that
 * finding's written sections with an ordinary Markdown image whose target is
 * `evidence:<attachment id>`:
 *
 *     ![The relayed session](evidence:57)
 *
 * The alt text, when present, is the caption for that placement; left empty,
 * the image's stored caption prints.  A ticked image no section places prints
 * under Evidence.
 *
 * The server's twin is `backend/app/services/report_images.py` (`REFERENCE`)
 * — change both together.  The server decides what prints; this only shows
 * the author what it will decide.
 */

/**
 * A placement in Markdown — THE grammar, the same pattern as the server's
 * `REFERENCE`: `![alt](evidence:<id>)` on one line, with an optional title
 * in double quotes.  Nothing wider places an image: `(<evidence:57>)`, a
 * title in single quotes, an alt text over two lines or a reference-style
 * image are other spellings Markdown allows, and the server rewrites them to
 * this form when the section is saved (`normalise_references`).  Until then
 * the report prints no image there, so the preview shows none either.
 */
export const EVIDENCE_REFERENCE = /!\[((?:[^\]\\\n]|\\.)*)\]\(\s*evidence:(\d{1,12})(?:\s+"[^"\n]*")?\s*\)/g;

const EVIDENCE_REFERENCE_AT = new RegExp(EVIDENCE_REFERENCE.source, 'y');

// What Python's `str.splitlines` ends a line at, and what `str.strip` removes
// — spelled out, because `_code_spans` is written with those two and JS's
// `\s` / line terminators are slightly different sets.
// (Code points above 0xFF are built from their numbers: an escape written in
// this file would be easy to lose to an editor that expands it.)
const chars = (...codes: number[]): string => String.fromCharCode(...codes);
const LS_PS = chars(0x2028, 0x2029);
const LINE_END = `\\n\\r\\v\\f\\x1c-\\x1e\\x85${LS_PS}`;
const LINE = new RegExp(`[^${LINE_END}]*(?:\\r\\n|[${LINE_END}]|$)`, 'g');
const NOT_BLANK = new RegExp(
  `[^\\t\\n\\v\\f\\r\\x1c-\\x1f \\x85\\xa0${chars(0x1680, 0x2000)}-${chars(0x200a)}${LS_PS}${chars(0x202f, 0x205f, 0x3000)}]`,
);
const FENCE = /^ {0,3}(`{3,}|~{3,})/;
const TICKS = /`+/g;

/**
 * `[start, end)` of every fenced code block and inline code span in `text`,
 * sorted — the twin of the server's `_code_spans` (report_images.py), rule
 * for rule; change both together:
 *
 *   - a fence opens on a line starting (after at most three spaces) with
 *     three or more backticks or tildes, and closes on a line that starts
 *     with at least as many of the same character and has nothing after
 *     them; an unclosed fence runs to the end of the text;
 *   - inline code is a run of backticks up to the NEXT run of exactly the
 *     same length, looked for across consecutive lines outside fences; a run
 *     with no partner is ordinary text.
 */
export const codeSpans = (text: string): Array<[number, number]> => {
  const spans: Array<[number, number]> = [];
  const prose: Array<[number, number]> = [];
  let offset = 0;
  let fence: string | null = null;
  let start = 0;
  for (const [line] of text.matchAll(LINE)) {
    if (line === '') break;                     // the empty match at the end
    const opened = FENCE.exec(line);
    if (fence === null) {
      if (opened) {
        fence = opened[1];
        start = offset;
      } else {
        prose.push([offset, offset + line.length]);
      }
    } else if (
      opened && opened[1][0] === fence[0] && opened[1].length >= fence.length
      && !NOT_BLANK.test(line.slice(opened[0].length))
    ) {
      spans.push([start, offset + line.length]);
      fence = null;
    }
    offset += line.length;
  }
  if (fence !== null) spans.push([start, text.length]);
  // Runs of prose lines are joined first: a span may cross a line break.
  const merged: Array<[number, number]> = [];
  for (const [a, b] of prose) {
    const last = merged[merged.length - 1];
    if (last && last[1] === a) last[1] = b;
    else merged.push([a, b]);
  }
  for (const [a, b] of merged) {
    const runs = [...text.slice(a, b).matchAll(TICKS)]
      .map((m) => ({ start: a + (m.index ?? 0), end: a + (m.index ?? 0) + m[0].length }));
    let i = 0;
    while (i < runs.length) {
      const width = runs[i].end - runs[i].start;
      let close = -1;
      for (let j = i + 1; j < runs.length; j += 1) {
        if (runs[j].end - runs[j].start === width) { close = j; break; }
      }
      if (close === -1) {
        i += 1;
      } else {
        spans.push([runs[i].start, runs[close].end]);
        i = close + 1;
      }
    }
  }
  return spans.sort((x, y) => x[0] - y[0] || x[1] - y[1]);
};

// The spans of the text last asked about: the preview asks once per `![` of
// the same string.
let spansOf: { text: string; spans: Array<[number, number]> } | null = null;

/** Whether position `at` of `text` is inside code — where a reference is
 *  shown as typed and places nothing (the server's `iter_placements`). */
const inCode = (text: string, at: number): boolean => {
  if (!text.includes('`') && !text.includes('~~~')) return false;
  if (spansOf?.text !== text) spansOf = { text, spans: codeSpans(text) };
  return spansOf.spans.some(([a, b]) => a <= at && at < b);
};

/**
 * The placement that starts exactly at `at` in `text`, else null — what the
 * preview asks before it shows an image, so it shows one for precisely the
 * references the server counts (`referencedImageIds` finds the same ones).
 * A reference written inside code is not a placement.
 */
export const evidenceReferenceAt = (
  text: string, at: number,
): { id: number; alt: string; end: number } | null => {
  EVIDENCE_REFERENCE_AT.lastIndex = at;
  const m = EVIDENCE_REFERENCE_AT.exec(text);
  if (!m || inCode(text, at)) return null;
  return { id: Number(m[2]), alt: m[1], end: at + m[0].length };
};

/** The target of an image that names one of the finding's images, else null. */
export const evidenceIdOf = (target: string): number | null => {
  const m = /^evidence:(\d{1,12})$/.exec(target.trim());
  return m ? Number(m[1]) : null;
};

/** The attachment ids a section's Markdown PLACES, each once, in order.  A
 *  reference inside a fenced code block or an inline code span is printed as
 *  typed — the report makes no figure of it — so it is not one (the server's
 *  `referenced_ids`; review 2026-10-02 H5). */
export const referencedImageIds = (text: string | null | undefined): number[] => {
  const seen: number[] = [];
  const source = text ?? '';
  for (const m of source.matchAll(EVIDENCE_REFERENCE)) {
    if (inCode(source, m.index ?? 0)) continue;
    const id = Number(m[2]);
    if (!seen.includes(id)) seen.push(id);
  }
  return seen;
};

/** A caption made safe to sit inside `![…]`: one line, no brackets (they
 *  would end the alt text early), and short enough to read in the source. */
export const captionAsAlt = (caption: string | null | undefined, max = 120): string => {
  const text = (caption ?? '').replace(/[[\]\\]/g, '').replace(/\s+/g, ' ').trim();
  return text.length > max ? `${text.slice(0, max - 1).trimEnd()}…` : text;
};

/** The Markdown that places image `id`. */
export const imageReference = (id: number, caption?: string | null): string =>
  `![${captionAsAlt(caption)}](evidence:${id})`;

export const REPORT_FIELD_LABELS: Record<string, string> = {
  description: 'Description',
  impact: 'Impact',
  recommendation: 'Recommendation',
  steps_to_reproduce: 'Steps to reproduce',
  references: 'References',
};

const fieldNames = (fields: readonly string[]): string =>
  fields.map((f) => REPORT_FIELD_LABELS[f] ?? f).join(', ');

export interface ImagePlacement {
  in_report: boolean;
  printable: boolean;
  placed_in: readonly string[];
}

/** One line saying where an image prints — or why it will not. */
export const placementLine = (image: ImagePlacement): { text: string; tone: 'plain' | 'warning' } => {
  const placed = image.placed_in ?? [];
  if (!image.printable) {
    return { text: 'WebP — the report cannot print it (attach a PNG or JPEG)', tone: 'warning' };
  }
  if (!image.in_report) {
    return placed.length
      ? { text: `Referenced in ${fieldNames(placed)}, but not ticked “In report” — it will not print`, tone: 'warning' }
      : { text: 'Not in the report', tone: 'plain' };
  }
  return placed.length
    ? { text: `In: ${fieldNames(placed)}`, tone: 'plain' }
    : { text: 'Not placed — prints under Evidence', tone: 'plain' };
};

/** An image the editor may insert: one of the finding's, ticked "In report". */
export interface InsertableImage {
  id: number;
  caption: string | null;
  filename: string;
}

/** What a report-text editor needs to place the finding's images
 *  (`useFindingImages` returns it). */
export interface MarkdownImages {
  placeable: InsertableImage[];
  /** Object URLs loaded so far, by attachment id (thumbnails). */
  urls: Record<number, string>;
  resolver: EvidenceResolver;
}

/** What the preview needs to show a referenced image. */
export interface EvidenceLookup {
  /** The stored caption, when the id is one of this finding's ticked,
   *  printable images; `null` when the reference places nothing. */
  caption: string | null;
  /** An object URL for the bytes once they are loaded. */
  src?: string;
}

/** Given to `SafeMarkdown` on a finding's page.  Without one, an `evidence:`
 *  reference is shown as a short text marker and nothing is loaded. */
export interface EvidenceResolver {
  /** `null`: not an image this section may show. */
  lookup: (id: number) => EvidenceLookup | null;
  /** Ask for the bytes of an image `lookup` knows (idempotent). */
  ensure: (id: number) => void;
}
