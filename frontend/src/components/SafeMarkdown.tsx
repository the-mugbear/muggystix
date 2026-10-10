/**
 * SafeMarkdown (v5.290.0) — written Markdown shown the way the client report
 * prints it, under the report's rules (`backend/app/services/quarto_fields.lua`):
 *
 *   - raw HTML is never HTML: it stays literal text (the report reads GitHub
 *     Markdown WITHOUT raw HTML);
 *   - images are reduced to their alt text — nothing is ever loaded.  The
 *     one exception is the report's own: `![caption](evidence:57)`, a
 *     reference to an image of THIS finding ticked "In report".  With an
 *     `evidence` resolver (a finding's page) it is shown as that image, from
 *     an object URL the resolver fetched for an id on the finding's list —
 *     never from the reference's text — and a reference the resolver does
 *     not know is a visible "image not available" note;
 *   - links keep only http / https / mailto targets (anything else is its
 *     text) and open in a new tab with rel="noopener noreferrer";
 *   - headings become bold paragraphs (the report's structure is the
 *     template's, not the author's);
 *   - a single newline is a space, as in the report; a blank line starts a
 *     new paragraph.
 *
 * It builds React elements and never sets innerHTML, so nothing an author
 * writes can become markup.  A deliberately small subset of GitHub Markdown:
 * paragraphs, headings, lists (nested by indentation), block quotes, fenced
 * and indented code, rules, pipe tables (5.293.0), and inline code / bold /
 * italic / strikethrough / links / autolinks / bare web URLs / hard breaks.
 * Anything else stays text.
 */
import React, { useEffect } from 'react';

import type { EvidenceImages } from '../utils/evidenceImages';
import { CellAlign, isTableRow, splitTableRow, tableAlignments } from '../utils/markdownEditing';
import { findMentionSpans } from '../utils/mentions';
import { EvidenceResolver, evidenceReferenceAt } from '../utils/reportImages';

const SAFE_SCHEMES = new Set(['http', 'https', 'mailto']);

/** The target when it is a web or mail link, else null (the text stays). */
const safeHref =(raw: string): string | null => {
  const target = raw.trim();
  const m = /^([a-zA-Z][a-zA-Z0-9+.-]*):/.exec(target);
  if (!m || !SAFE_SCHEMES.has(m[1].toLowerCase())) return null;
  // No control characters or whitespace inside a URL.
  if (/[\u0000- \u007f]/.test(target)) return null;
  return target;
};

// ---------------------------------------------------------------------------
// Inline
// ---------------------------------------------------------------------------

const PUNCT = /[!"#$%&'()*+,\-./:;<=>?@[\\\]^_`{|}~]/;

const linkClass = 'text-info underline underline-offset-2 [overflow-wrap:anywhere]';

const renderLink = (href: string | null, children: React.ReactNode, key: string) =>
  href ? (
    <a key={key} href={href} target="_blank" rel="noopener noreferrer" className={linkClass}>{children}</a>
  ) : (
    <React.Fragment key={key}>{children}</React.Fragment>
  );

/** Index of the `]` closing the `[` at `open`, honouring nesting and escapes. */
const closingBracket = (s: string, open: number): number => {
  let depth = 0;
  for (let i = open; i < s.length; i += 1) {
    const c = s[i];
    if (c === '\\') { i += 1; continue; }
    if (c === '[') depth += 1;
    else if (c === ']') { depth -= 1; if (depth === 0) return i; }
  }
  return -1;
};

/** `(url "title")` right after a `]`: the url and the index after `)`. */
const linkDestination = (s: string, at: number): { url: string; end: number } | null => {
  if (s[at] !== '(') return null;
  let depth = 0;
  for (let i = at; i < s.length; i += 1) {
    const c = s[i];
    if (c === '\\') { i += 1; continue; }
    if (c === '(') depth += 1;
    else if (c === ')') {
      depth -= 1;
      if (depth === 0) {
        const inner = s.slice(at + 1, i).trim();
        let url = inner.split(/\s+/)[0] ?? '';
        if (url.startsWith('<') && url.endsWith('>')) url = url.slice(1, -1);
        return { url, end: i + 1 };
      }
    }
  }
  return null;
};

/** Plain text of an inline span (an image's alt text). */
const plain = (s: string): string => s.replace(/\\([!-/:-@[-`{-~])/g, '$1').replace(/[*_~`]/g, '');

const EMPHASIS: Array<{ open: string; tag: 'strong' | 'em' | 'del' }> = [
  { open: '**', tag: 'strong' },
  { open: '__', tag: 'strong' },
  { open: '~~', tag: 'del' },
  { open: '*', tag: 'em' },
  { open: '_', tag: 'em' },
];

const isWordChar = (c: string | undefined) => !!c && /[\p{L}\p{N}]/u.test(c);

/** Where an emphasis run opened at `i` closes, or -1. */
const closingDelimiter = (s: string, i: number, delim: string): number => {
  const start = i + delim.length;
  if (start >= s.length || /\s/.test(s[start])) return -1;
  // `_` inside a word (snake_case) is not emphasis.
  if (delim[0] === '_' && isWordChar(s[i - 1])) return -1;
  let j = start;
  while (j < s.length) {
    const c = s[j];
    if (c === '\\') { j += 2; continue; }
    if (c === '`') {
      // Skip a code span: its content is not markup.
      const run = /^`+/.exec(s.slice(j))![0];
      const close = s.indexOf(run, j + run.length);
      if (close !== -1) { j = close + run.length; continue; }
    }
    if (s.startsWith(delim, j) && j > start && !/\s/.test(s[j - 1])) {
      // A single `*` must not close on half of a `**`.
      if (delim.length === 1 && s[j + 1] === delim) { j += 2; continue; }
      if (delim[0] === '_' && isWordChar(s[j + delim.length])) { j += 1; continue; }
      return j;
    }
    j += 1;
  }
  return -1;
};

const BARE_URL = /^(?:https?:\/\/|mailto:)[^\s<>]+/i;

/**
 * A reference to one of the finding's images, as the report will print it:
 * the picture with its caption.  The bytes come from the resolver (an object
 * URL fetched through the authenticated attachment route for an id on the
 * finding's own list) — the reference's text is never a URL.
 */
const evidenceNoteClass = 'my-xxs inline-block max-w-full rounded-control border border-dashed border-border px-xs py-xxs text-caption text-warning [overflow-wrap:anywhere]';
// The same box while the list is being read and while the bytes are: the
// text below it does not jump when the picture arrives.
const evidencePlaceholderClass = 'flex h-24 items-center rounded-control border border-border bg-muted px-sm text-caption text-muted-foreground';
const evidenceRetryClass = 'ml-xs rounded-sm underline underline-offset-2 hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring';

/**
 * The resolver answers in three ways (review 2026-10-01 S4), and only one of
 * them is "this is not one of the finding's images": while the finding's
 * image list is still being read the reference is NOT KNOWN YET (a neutral
 * placeholder), and when the list could not be read that is what is said,
 * with a retry.  The same for the bytes of a known image: loading, shown, or
 * could not be loaded.  A plain `EvidenceResolver` (no `listStatus`) is a
 * list that is known.
 */
const EvidenceImage: React.FC<{ id: number; alt: string; evidence: EvidenceImages }> = ({ id, alt, evidence }) => {
  const found = evidence.lookup(id);
  const known = found !== null;
  useEffect(() => {
    if (known) evidence.ensure(id);
  }, [known, id, evidence]);
  if (!found) {
    const listStatus = evidence.listStatus ?? 'ready';
    if (listStatus === 'loading') {
      return (
        <span className="my-xs block min-w-0" data-testid={`evidence-pending-${id}`} aria-busy="true">
          <span className={evidencePlaceholderClass}>Loading image…</span>
        </span>
      );
    }
    if (listStatus === 'failed') {
      return (
        <span role="note" data-testid={`evidence-list-failed-${id}`} className={evidenceNoteClass}>
          This finding’s images could not be loaded, so image {id} cannot be shown here. The report is not affected.
          {evidence.retryList && (
            <button type="button" className={evidenceRetryClass} onClick={() => evidence.retryList?.()}>Retry</button>
          )}
        </span>
      );
    }
    return (
      <span role="note" data-testid={`evidence-missing-${id}`} className={evidenceNoteClass}>
        Image not available: evidence:{id} is not one of this finding’s “In report” images, so the report prints
        {alt ? ` “${alt}” as text` : ' nothing here'}.
      </span>
    );
  }
  const caption = alt || found.caption || '';
  const bytesFailed = !found.src && (evidence.failed?.(id) ?? false);
  return (
    <span className="my-xs block min-w-0" data-testid={`evidence-image-${id}`}>
      {found.src ? (
        <img src={found.src} alt={caption} className="block h-auto max-h-96 max-w-full rounded-control border border-border" />
      ) : bytesFailed ? (
        <span role="note" data-testid={`evidence-bytes-failed-${id}`} className={`${evidencePlaceholderClass} text-warning`}>
          <span className="min-w-0">
            Image {id} could not be loaded. The report is not affected.
            {evidence.retry && (
              <button type="button" className={evidenceRetryClass} onClick={() => evidence.retry?.(id)}>Retry</button>
            )}
          </span>
        </span>
      ) : (
        <span className={evidencePlaceholderClass}>
          Loading image {id}…
        </span>
      )}
      <span className="mt-xxs line-clamp-3 block text-caption text-muted-foreground [overflow-wrap:anywhere]" title={caption || undefined}>
        Figure: {caption || <span className="italic">no caption — the report prints the file name</span>}
      </span>
    </span>
  );
};

/** How one text is rendered — the same for every block and run inside it. */
interface How {
  evidence?: EvidenceResolver;
  lineBreaks?: boolean;
  mentions?: readonly string[];
}

/**
 * Plain text with its @mentions of project members marked, so a reader sees
 * who was notified.  Only the given usernames are marked (the rule the server
 * notifies by — utils/mentions.ts); any other `@word` stays text.  The one
 * marker: `MentionText` (plain bodies) and discussion Markdown both use it.
 */
export const markMentions = (body: string, usernames: readonly string[], keyPrefix = 'm'): React.ReactNode[] => {
  const spans = findMentionSpans(body, usernames);
  if (spans.length === 0) return [body];
  const parts: React.ReactNode[] = [];
  let cursor = 0;
  for (const s of spans) {
    if (s.start > cursor) parts.push(body.slice(cursor, s.start));
    parts.push(
      <span key={`${keyPrefix}-${s.start}`} className="rounded-sm bg-primary/10 px-0.5 font-medium text-primary" title={`Mentions ${s.username}`}>
        {body.slice(s.start, s.end)}
      </span>,
    );
    cursor = s.end;
  }
  if (cursor < body.length) parts.push(body.slice(cursor));
  return parts;
};

const renderInline =(s: string, keyPrefix = 'i', how: How = {}): React.ReactNode[] => {
  const { evidence } = how;
  const out: React.ReactNode[] = [];
  let text = '';
  let k = 0;
  const key = () => `${keyPrefix}-${k++}`;
  const flush = () => {
    if (!text) return;
    // A run of plain text is where a mention can be: never inside code or a link's target.
    if (how.mentions?.length) out.push(...markMentions(text, how.mentions, key()));
    else out.push(text);
    text = '';
  };

  let i = 0;
  while (i < s.length) {
    const c = s[i];

    // Hard break: backslash or two+ spaces before a newline — or, in
    // discussion text, any newline (the writer pressed Enter).
    if (c === '\n') {
      if (how.lineBreaks || /( {2,}|\\)$/.test(text)) {
        text = text.replace(/( {2,}|\\)$/, '');
        flush();
        out.push(<br key={key()} />);
      } else {
        text = text.replace(/ +$/, '');
        text += ' ';
      }
      i += 1;
      while (s[i] === ' ') i += 1;
      continue;
    }

    if (c === '\\' && i + 1 < s.length && PUNCT.test(s[i + 1])) {
      text += s[i + 1];
      i += 2;
      continue;
    }

    if (c === '`') {
      const run = /^`+/.exec(s.slice(i))![0];
      const close = s.indexOf(run, i + run.length);
      if (close !== -1) {
        flush();
        const body = s.slice(i + run.length, close).replace(/\n/g, ' ');
        const trimmed = /^ .* $/.test(body) && body.trim() ? body.slice(1, -1) : body;
        out.push(
          <code key={key()} className="rounded bg-muted px-xxs font-mono text-caption [overflow-wrap:anywhere]">{trimmed}</code>,
        );
        i = close + run.length;
        continue;
      }
      text += run;
      i += run.length;
      continue;
    }

    // Image: the alt text only (nothing is loaded) — except a reference to
    // one of the finding's own images (`evidence:<id>`), which the report
    // prints as a figure.  Which ids those are is the resolver's to say.
    // What counts as such a reference is ONE grammar, the server's
    // (`evidenceReferenceAt` — review 2026-10-01 M1): another spelling of the
    // same image (`(<evidence:57>)`, a title in single quotes, an alt text
    // over two lines) places nothing in the report, so it shows as its alt
    // text here too, like any other image.
    if (c === '!' && s[i + 1] === '[') {
      const placed = evidenceReferenceAt(s, i);
      if (placed) {
        const alt = plain(placed.alt).trim();
        flush();
        out.push(evidence
          ? <EvidenceImage key={key()} id={placed.id} alt={alt} evidence={evidence} />
          : <span key={key()} className="text-muted-foreground">[image {placed.id}{alt ? `: ${alt}` : ''}]</span>);
        i = placed.end;
        continue;
      }
      const close = closingBracket(s, i + 1);
      const dest = close !== -1 ? linkDestination(s, close + 1) : null;
      if (dest) {
        text += plain(s.slice(i + 2, close));
        i = dest.end;
        continue;
      }
    }

    if (c === '[') {
      const close = closingBracket(s, i);
      const dest = close !== -1 ? linkDestination(s, close + 1) : null;
      if (dest) {
        flush();
        const label = renderInline(s.slice(i + 1, close), `${keyPrefix}-${k}`, how);
        out.push(renderLink(safeHref(dest.url), label, key()));
        i = dest.end;
        continue;
      }
    }

    // Autolink <https://…>; any other `<` is literal text (raw HTML is not HTML).
    if (c === '<') {
      const m = /^<([a-zA-Z][a-zA-Z0-9+.-]*:[^\s<>]*)>/.exec(s.slice(i));
      if (m) {
        const href = safeHref(m[1]);
        if (href) {
          flush();
          out.push(renderLink(href, m[1], key()));
          i += m[0].length;
          continue;
        }
      }
    }

    // Bare web URL (GitHub Markdown links these).
    if ((c === 'h' || c === 'H' || c === 'm' || c === 'M') && !isWordChar(s[i - 1])) {
      const m = BARE_URL.exec(s.slice(i));
      if (m) {
        let url = m[0];
        // Trailing punctuation belongs to the sentence.
        while (/[.,;:!?'")\]*_~]$/.test(url)) {
          if (url.endsWith(')') && (url.match(/\(/g)?.length ?? 0) >= (url.match(/\)/g)?.length ?? 0)) break;
          url = url.slice(0, -1);
        }
        const href = safeHref(url);
        if (href && url.length > 'mailto:'.length) {
          flush();
          out.push(renderLink(href, url, key()));
          i += url.length;
          continue;
        }
      }
    }

    if (c === '*' || c === '_' || c === '~') {
      let matched = false;
      for (const { open, tag } of EMPHASIS) {
        if (!s.startsWith(open, i)) continue;
        const close = closingDelimiter(s, i, open);
        if (close === -1) continue;
        flush();
        const inner = renderInline(s.slice(i + open.length, close), `${keyPrefix}-${k}`, how);
        out.push(React.createElement(tag, { key: key() }, inner));
        i = close + open.length;
        matched = true;
        break;
      }
      if (matched) continue;
      // An unmatched run is text, whole (so `**` never half-matches later).
      const run = new RegExp(`^\\${c}+`).exec(s.slice(i))![0];
      text += run;
      i += run.length;
      continue;
    }

    text += c;
    i += 1;
  }
  flush();
  return out;
};

// ---------------------------------------------------------------------------
// Blocks
// ---------------------------------------------------------------------------

type Block =
  | { kind: 'para'; text: string }
  | { kind: 'heading'; text: string }
  | { kind: 'code'; text: string }
  | { kind: 'rule' }
  | { kind: 'quote'; lines: string[] }
  | { kind: 'list'; ordered: boolean; start: number; items: string[][] }
  | { kind: 'table'; align: CellAlign[]; head: string[]; rows: string[][] };

const FENCE = /^ {0,3}(`{3,}|~{3,})/;
const HEADING = /^ {0,3}#{1,6}(?:\s+(.*?))?\s*$/;
const RULE = /^ {0,3}(?:(?:\*[ \t]*){3,}|(?:-[ \t]*){3,}|(?:_[ \t]*){3,})$/;
const QUOTE = /^ {0,3}> ?(.*)$/;
const ITEM = /^( {0,3})([-*+]|(\d{1,9})[.)])(?:[ \t]+(.*)|$)/;
const SETEXT = /^ {0,3}(=+|-+)\s*$/;

const indentOf = (line: string) => /^ */.exec(line)![0].length;

const parseBlocks =(source: string): Block[] => {
  const lines = source.replace(/\r\n?/g, '\n').replace(/\t/g, '    ').split('\n');
  const blocks: Block[] = [];
  let para: string[] = [];
  const flushPara = () => {
    if (para.length) blocks.push({ kind: 'para', text: para.join('\n').trim() });
    para = [];
  };

  let i = 0;
  while (i < lines.length) {
    const line = lines[i];

    if (!line.trim()) { flushPara(); i += 1; continue; }

    const fence = FENCE.exec(line);
    if (fence) {
      flushPara();
      const marker = fence[1];
      const closing = new RegExp(`^ {0,3}\\${marker[0]}{${marker.length},}\\s*$`);
      const body: string[] = [];
      i += 1;
      while (i < lines.length && !closing.test(lines[i])) {
        body.push(lines[i]);
        i += 1;
      }
      i += 1; // the closing fence (or the end)
      blocks.push({ kind: 'code', text: body.join('\n') });
      continue;
    }

    // Indented code — only where it cannot continue a paragraph.
    if (!para.length && indentOf(line) >= 4) {
      const body: string[] = [];
      while (i < lines.length && (indentOf(lines[i]) >= 4 || !lines[i].trim())) {
        body.push(lines[i].slice(4));
        i += 1;
      }
      while (body.length && !body[body.length - 1].trim()) body.pop();
      blocks.push({ kind: 'code', text: body.join('\n') });
      continue;
    }

    // A setext underline turns the paragraph above into a heading.
    if (para.length && SETEXT.test(line)) {
      blocks.push({ kind: 'heading', text: para.join('\n').trim() });
      para = [];
      i += 1;
      continue;
    }

    // A pipe table (5.293.0), by the report's rules: a header row, a dashes
    // row of the same width, then rows until a blank line or a line without
    // a pipe.  Never straight after text — the report joins that into the
    // paragraph, so the preview does too.
    const align = !para.length && i + 1 < lines.length ? tableAlignments(line, lines[i + 1]) : null;
    if (align) {
      const width = align.length;
      const fit = (cells: string[]) =>
        cells.length >= width ? cells.slice(0, width) : [...cells, ...Array(width - cells.length).fill('')];
      const rows: string[][] = [];
      i += 2;
      while (i < lines.length && isTableRow(lines[i]) && !FENCE.test(lines[i]) && !QUOTE.test(lines[i])) {
        rows.push(fit(splitTableRow(lines[i])));
        i += 1;
      }
      blocks.push({ kind: 'table', align, head: fit(splitTableRow(line)), rows });
      continue;
    }

    const heading = HEADING.exec(line);
    if (heading) {
      flushPara();
      const text = (heading[1] ?? '').replace(/\s+#+\s*$/, '');
      if (text) blocks.push({ kind: 'heading', text });
      i += 1;
      continue;
    }

    if (RULE.test(line)) {
      flushPara();
      blocks.push({ kind: 'rule' });
      i += 1;
      continue;
    }

    if (QUOTE.test(line)) {
      flushPara();
      const quoted: string[] = [];
      while (i < lines.length && lines[i].trim()) {
        const q = QUOTE.exec(lines[i]);
        quoted.push(q ? q[1] : lines[i]);
        i += 1;
      }
      blocks.push({ kind: 'quote', lines: quoted });
      continue;
    }

    const item = ITEM.exec(line);
    // An ordered item interrupts a paragraph only when it starts at 1.
    if (item && (!para.length || !item[3] || item[3] === '1')) {
      flushPara();
      const ordered = item[3] !== undefined;
      const start = ordered ? parseInt(item[3], 10) : 1;
      const bullet = ordered ? null : item[2];
      const items: string[][] = [];
      while (i < lines.length) {
        const m = ITEM.exec(lines[i]);
        const sameKind = m && (m[3] !== undefined) === ordered && (ordered || m[2] === bullet);
        if (!m || !sameKind) break;
        const contentIndent = m[1].length + m[2].length + 1;
        const body: string[] = [m[4] ?? ''];
        i += 1;
        let blankRun = 0;
        while (i < lines.length) {
          const next = lines[i];
          if (!next.trim()) { blankRun += 1; body.push(''); i += 1; continue; }
          if (indentOf(next) >= contentIndent) {
            body.push(next.slice(contentIndent));
            blankRun = 0;
            i += 1;
            continue;
          }
          // A lazy continuation line of the item's paragraph.
          if (blankRun === 0 && !ITEM.test(next) && !FENCE.test(next) && !HEADING.test(next) && !QUOTE.test(next) && !RULE.test(next)) {
            body.push(next.trim());
            i += 1;
            continue;
          }
          break;
        }
        while (body.length > 1 && !body[body.length - 1].trim()) body.pop();
        items.push(body);
        // A blank line between items keeps the list going.
      }
      blocks.push({ kind: 'list', ordered, start, items });
      continue;
    }

    para.push(line);
    i += 1;
  }
  flushPara();
  return blocks;
};

const renderBlocks = (blocks: Block[], keyPrefix: string, how: How = {}): React.ReactNode[] =>
  blocks.map((b, n) => {
    const key = `${keyPrefix}-${n}`;
    switch (b.kind) {
      case 'para':
        return <p key={key}>{renderInline(b.text, key, how)}</p>;
      case 'heading':
        // A heading in written text is a bold paragraph, as in the report.
        return <p key={key}><strong>{renderInline(b.text, key, how)}</strong></p>;
      case 'code':
        return (
          <pre key={key} className="overflow-x-auto rounded bg-muted p-sm font-mono text-caption">
            <code>{b.text}</code>
          </pre>
        );
      case 'rule':
        return <hr key={key} className="border-border" />;
      case 'quote':
        return (
          <blockquote key={key} className="space-y-xs border-l-2 border-border pl-sm text-muted-foreground">
            {renderBlocks(parseBlocks(b.lines.join('\n')), key, how)}
          </blockquote>
        );
      case 'list': {
        const children = b.items.map((body, j) => {
          const inner = parseBlocks(body.join('\n'));
          const ikey = `${key}-${j}`;
          // A tight item is its text alone, not a paragraph.
          const content = inner.length === 1 && inner[0].kind === 'para'
            ? renderInline(inner[0].text, ikey, how)
            : renderBlocks(inner, ikey, how);
          return <li key={ikey} className="space-y-xs">{content}</li>;
        });
        return b.ordered ? (
          <ol key={key} start={b.start} className="ml-lg list-decimal space-y-xxs marker:text-muted-foreground">{children}</ol>
        ) : (
          <ul key={key} className="ml-lg list-disc space-y-xxs marker:text-muted-foreground">{children}</ul>
        );
      }
      case 'table': {
        const cellClass = 'min-w-0 break-words px-xs py-xxs align-top [overflow-wrap:anywhere]';
        const textAlign = (a: CellAlign) => (a ? { textAlign: a } : undefined);
        return (
          <table key={key} className="w-full border-collapse text-caption" style={{ tableLayout: 'fixed' }}>
            <thead>
              <tr className="border-b border-border">
                {b.head.map((c, j) => (
                  <th key={j} scope="col" className={`${cellClass} text-left font-medium`} style={textAlign(b.align[j])}>
                    {renderInline(c, `${key}-h${j}`, how)}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {b.rows.map((r, n) => (
                <tr key={n} className="border-b border-border/60">
                  {r.map((c, j) => (
                    <td key={j} className={cellClass} style={textAlign(b.align[j])}>
                      {renderInline(c, `${key}-${n}-${j}`, how)}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        );
      }
      default:
        return null;
    }
  });

interface Props {
  text: string;
  className?: string;
  /**
   * On a finding's page: which `evidence:<id>` references are that finding's
   * "In report" images, and their bytes.  Such a reference is then shown as
   * the image; one the resolver does not know is a visible "image not
   * available" note.  Without it nothing is loaded and a reference is a short
   * text marker.  Every OTHER image is its alt text, always.
   */
  evidence?: EvidenceResolver;
  /**
   * Discussion text (a note, a comment) sets both of these — through
   * `DiscussionText`, which knows the project's members.  A single newline is
   * then a line break, because the writer pressed Enter; report text leaves
   * it off, where a newline is a space as the report prints it.
   */
  lineBreaks?: boolean;
  /** Usernames whose `@name` is marked in plain runs of text. */
  mentions?: readonly string[];
}

const SafeMarkdown: React.FC<Props> = ({ text, className, evidence, lineBreaks, mentions }) => (
  <div className={`min-w-0 space-y-xs break-words ${className ?? ''}`.trim()}>
    {renderBlocks(parseBlocks(text), 'md', { evidence, lineBreaks, mentions })}
  </div>
);

export default SafeMarkdown;
