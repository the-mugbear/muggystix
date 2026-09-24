/**
 * Markdown editing helpers (5.293.0) — the pure half of `MarkdownField`: the
 * toolbar's edits on a textarea's value + selection, and the table rules the
 * client report applies (Pandoc's GitHub Markdown reader,
 * `backend/app/services/quarto_fields.lua`), shared with `SafeMarkdown` so
 * the preview agrees with the report.
 */

/** A textarea's text and selection; every edit returns a new one. */
export interface Edit {
  value: string;
  start: number;
  end: number;
}

/** `**bold**`, `_italic_`, `` `code` ``: wrap the selection (or a placeholder,
 *  selected so typing replaces it). */
export const wrapSelection = (e: Edit, marker: string, placeholder: string): Edit => {
  const selected = e.value.slice(e.start, e.end) || placeholder;
  const value = e.value.slice(0, e.start) + marker + selected + marker + e.value.slice(e.end);
  const start = e.start + marker.length;
  return { value, start, end: start + selected.length };
};

/** `[text](https://)` — the URL selected, since it is what is left to type. */
export const insertLink = (e: Edit): Edit => {
  const text = e.value.slice(e.start, e.end) || 'link text';
  const url = 'https://';
  const value = `${e.value.slice(0, e.start)}[${text}](${url})${e.value.slice(e.end)}`;
  const start = e.start + text.length + 3;
  return { value, start, end: start + url.length };
};

/** Start each selected line with `- ` or `1. ` (numbered in order). */
export const prefixLines = (e: Edit, ordered: boolean): Edit => {
  const lineStart = e.value.lastIndexOf('\n', e.start - 1) + 1;
  const nextBreak = e.value.indexOf('\n', e.end > e.start ? e.end - 1 : e.end);
  const lineEnd = nextBreak === -1 ? e.value.length : nextBreak;
  const lines = e.value.slice(lineStart, lineEnd).split('\n');
  const body = lines.map((l, n) => `${ordered ? `${n + 1}.` : '-'} ${l}`).join('\n');
  const value = e.value.slice(0, lineStart) + body + e.value.slice(lineEnd);
  return { value, start: lineStart, end: lineStart + body.length };
};

/** Insert a block on lines of its own with a blank line either side — a
 *  table or code block written straight after text is not one in the report. */
export const insertBlock = (e: Edit, block: string, select?: string): Edit => {
  const before = e.value.slice(0, e.start);
  const after = e.value.slice(e.end);
  const lead = before === '' ? '' : before.endsWith('\n\n') ? '' : before.endsWith('\n') ? '\n' : '\n\n';
  const trail = after === '' ? '' : after.startsWith('\n\n') ? '' : after.startsWith('\n') ? '\n' : '\n\n';
  const value = before + lead + block + trail + after;
  const blockAt = before.length + lead.length;
  const at = select ? block.indexOf(select) : -1;
  return at >= 0
    ? { value, start: blockAt + at, end: blockAt + at + select!.length }
    : { value, start: blockAt + block.length, end: blockAt + block.length };
};

export const TABLE_TEMPLATE = '| Column | Column |\n| ------ | ------ |\n| Value  | Value  |';
export const CODE_TEMPLATE = '```\ncommand or output\n```';

// --- the report's table rules ------------------------------------------------

/** The dashes line under a table's header: `| --- | :-: | --: |`.  It must
 *  hold a pipe (a bare `---` under text is a heading underline). */
const DELIMITER = /^ {0,3}\|?[ \t]*:?-+:?[ \t]*(?:\|[ \t]*:?-+:?[ \t]*)*\|?[ \t]*$/;

export type CellAlign = 'left' | 'center' | 'right' | null;

/** A row's cells: outer pipes dropped, split on pipes that are not escaped,
 *  `\|` printed as a pipe. */
export const splitTableRow = (line: string): string[] => {
  let s = line.trim();
  if (s.startsWith('|')) s = s.slice(1);
  if (s.endsWith('|') && !s.endsWith('\\|')) s = s.slice(0, -1);
  const cells: string[] = [];
  let cell = '';
  for (let i = 0; i < s.length; i += 1) {
    if (s[i] === '\\' && s[i + 1] === '|') { cell += '|'; i += 1; continue; }
    if (s[i] === '|') { cells.push(cell.trim()); cell = ''; continue; }
    cell += s[i];
  }
  cells.push(cell.trim());
  return cells;
};

const hasPipe = (line: string) => /(^|[^\\])\|/.test(line);

/** The column alignments when `delimiter` is a table's dashes line under a
 *  header of the same width — else null (the report prints both as text). */
export const tableAlignments = (header: string, delimiter: string): CellAlign[] | null => {
  if (!hasPipe(delimiter) || !DELIMITER.test(delimiter) || !hasPipe(header)) return null;
  const cols = splitTableRow(delimiter);
  if (cols.length !== splitTableRow(header).length) return null;
  return cols.map((c) => {
    const left = c.startsWith(':');
    const right = c.endsWith(':');
    return left && right ? 'center' : right ? 'right' : left ? 'left' : null;
  });
};

/** A body row continues the table while it is not blank and holds a pipe. */
export const isTableRow = (line: string): boolean => line.trim() !== '' && hasPipe(line);

/** Line numbers (1-based) of tables written straight after a line of text:
 *  the report joins those into the paragraph above instead of drawing a
 *  table, so the editor says to leave a blank line. */
export const tablesAfterText = (text: string): number[] => {
  const lines = text.replace(/\r\n?/g, '\n').split('\n');
  const found: number[] = [];
  let inFence = false;
  for (let i = 1; i < lines.length - 1; i += 1) {
    const prev = lines[i - 1];
    const fence = /^ {0,3}(`{3,}|~{3,})/.test(prev);
    if (fence) inFence = !inFence;
    // After a closing fence or a heading, a table starts cleanly.
    if (inFence || fence || /^ {0,3}#{1,6}(\s|$)/.test(prev)) continue;
    if (prev.trim() === '' || isTableRow(prev)) continue;
    if (tableAlignments(lines[i], lines[i + 1])) found.push(i + 1);
  }
  return found;
};
