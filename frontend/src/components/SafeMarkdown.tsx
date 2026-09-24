/**
 * SafeMarkdown (v5.290.0) — written Markdown shown the way the client report
 * prints it, under the report's rules (`backend/app/services/quarto_fields.lua`):
 *
 *   - raw HTML is never HTML: it stays literal text (the report reads GitHub
 *     Markdown WITHOUT raw HTML);
 *   - images are reduced to their alt text — nothing is ever loaded;
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
 * and indented code, rules, and inline code / bold / italic / strikethrough /
 * links / autolinks / bare web URLs / hard breaks.  Anything else stays text.
 */
import React from 'react';

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

const renderInline =(s: string, keyPrefix = 'i'): React.ReactNode[] => {
  const out: React.ReactNode[] = [];
  let text = '';
  let k = 0;
  const key = () => `${keyPrefix}-${k++}`;
  const flush = () => { if (text) { out.push(text); text = ''; } };

  let i = 0;
  while (i < s.length) {
    const c = s[i];

    // Hard break: backslash or two+ spaces before a newline.
    if (c === '\n') {
      if (/( {2,}|\\)$/.test(text)) {
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

    // Image: the alt text only (nothing is loaded).
    if (c === '!' && s[i + 1] === '[') {
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
        const label = renderInline(s.slice(i + 1, close), `${keyPrefix}-${k}`);
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
        const inner = renderInline(s.slice(i + open.length, close), `${keyPrefix}-${k}`);
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
  | { kind: 'list'; ordered: boolean; start: number; items: string[][] };

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

const renderBlocks = (blocks: Block[], keyPrefix: string): React.ReactNode[] =>
  blocks.map((b, n) => {
    const key = `${keyPrefix}-${n}`;
    switch (b.kind) {
      case 'para':
        return <p key={key}>{renderInline(b.text, key)}</p>;
      case 'heading':
        // A heading in written text is a bold paragraph, as in the report.
        return <p key={key}><strong>{renderInline(b.text, key)}</strong></p>;
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
            {renderBlocks(parseBlocks(b.lines.join('\n')), key)}
          </blockquote>
        );
      case 'list': {
        const children = b.items.map((body, j) => {
          const inner = parseBlocks(body.join('\n'));
          const ikey = `${key}-${j}`;
          // A tight item is its text alone, not a paragraph.
          const content = inner.length === 1 && inner[0].kind === 'para'
            ? renderInline(inner[0].text, ikey)
            : renderBlocks(inner, ikey);
          return <li key={ikey} className="space-y-xs">{content}</li>;
        });
        return b.ordered ? (
          <ol key={key} start={b.start} className="ml-lg list-decimal space-y-xxs marker:text-muted-foreground">{children}</ol>
        ) : (
          <ul key={key} className="ml-lg list-disc space-y-xxs marker:text-muted-foreground">{children}</ul>
        );
      }
      default:
        return null;
    }
  });

interface Props {
  text: string;
  className?: string;
}

const SafeMarkdown: React.FC<Props> = ({ text, className }) => (
  <div className={`min-w-0 space-y-xs break-words ${className ?? ''}`.trim()}>
    {renderBlocks(parseBlocks(text), 'md')}
  </div>
);

export default SafeMarkdown;
