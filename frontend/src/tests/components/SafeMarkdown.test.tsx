/**
 * SafeMarkdown (v5.290.0) — written Markdown rendered under the client
 * report's rules (quarto_fields.lua): no raw HTML, no images, web/mail links
 * only, headings as bold paragraphs.
 */
import { render } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import SafeMarkdown from '../../components/SafeMarkdown';
import type { EvidenceResolver } from '../../utils/reportImages';

const md = (text: string) => render(<SafeMarkdown text={text} />).container;

describe('SafeMarkdown — tables (5.293.0), as the report prints them', () => {
  const rows = (c: HTMLElement) =>
    Array.from(c.querySelectorAll('tbody tr')).map((tr) => Array.from(tr.children).map((td) => td.textContent));

  it('draws every row of a table that ends the text', () => {
    const c = md('| Month    | Savings |\n| -------- | ------- |\n| January  | $250    |\n| February | $80     |\n| March    | $420    |');
    expect(Array.from(c.querySelectorAll('th')).map((th) => th.textContent)).toEqual(['Month', 'Savings']);
    expect(rows(c)).toEqual([['January', '$250'], ['February', '$80'], ['March', '$420']]);
    expect(c.querySelector('table')?.style.tableLayout).toBe('fixed');
  });

  it('pads short rows, trims long ones, aligns columns and formats cells', () => {
    const c = md('| A | B | C |\n|---|:-:|--:|\n| **1** |\n| 1 | 2 | 3 | 4 |');
    expect(rows(c)).toEqual([['1', '', ''], ['1', '2', '3']]);
    expect(c.querySelector('tbody strong')?.textContent).toBe('1');
    const heads = c.querySelectorAll('th');
    expect((heads[1] as HTMLElement).style.textAlign).toBe('center');
    expect((heads[2] as HTMLElement).style.textAlign).toBe('right');
  });

  it('keeps as text what the report keeps as text', () => {
    // Straight after a line of text: joined into the paragraph.
    expect(md('Intro line.\n| A | B |\n| - | - |\n| 1 | 2 |').querySelector('table')).toBeNull();
    // A dashes row narrower than the header.
    expect(md('| A | B |\n|---|\n| 1 | 2 |').querySelector('table')).toBeNull();
  });

  it('ends a table at a line without a pipe', () => {
    const c = md('| A | B |\n|---|---|\n| 1 | 2 |\nafter');
    expect(rows(c)).toEqual([['1', '2']]);
    expect(c.querySelector('p')?.textContent).toBe('after');
  });
});

describe('SafeMarkdown', () => {
  it('renders emphasis, code and strikethrough', () => {
    const c = md('**bold** and *it* and `a<b>` and ~~gone~~ and snake_case_name');
    expect(c.querySelector('strong')?.textContent).toBe('bold');
    expect(c.querySelector('em')?.textContent).toBe('it');
    expect(c.querySelector('code')?.textContent).toBe('a<b>');
    expect(c.querySelector('del')?.textContent).toBe('gone');
    expect(c.textContent).toContain('snake_case_name');
  });

  it('keeps raw HTML as text — no element is ever created from it', () => {
    const c = md('<img src=x onerror="alert(1)">\n\n<script>alert(1)</script>\n\n<a href="javascript:alert(1)">x</a>');
    expect(c.querySelector('img')).toBeNull();
    expect(c.querySelector('script')).toBeNull();
    expect(c.querySelector('a')).toBeNull();
    expect(c.textContent).toContain('<img src=x onerror="alert(1)">');
    expect(c.textContent).toContain('<script>alert(1)</script>');
  });

  it('reduces an image to its alt text and loads nothing', () => {
    const c = md('Before ![the screenshot](https://evil.example/x.png "t") after');
    expect(c.querySelector('img')).toBeNull();
    expect(c.textContent).toBe('Before the screenshot after');
  });

  it('keeps only web and mail links, opened safely', () => {
    const c = md('[vendor](https://vendor.example/adv) [mail](mailto:sec@example.com) [bad](javascript:alert(1)) [file](file:///etc/passwd) [rel](/admin)');
    const links = Array.from(c.querySelectorAll('a'));
    expect(links.map((a) => a.getAttribute('href'))).toEqual(['https://vendor.example/adv', 'mailto:sec@example.com']);
    for (const a of links) {
      expect(a.getAttribute('rel')).toBe('noopener noreferrer');
      expect(a.getAttribute('target')).toBe('_blank');
    }
    // The refused ones are their text.
    expect(c.textContent).toContain('bad');
    expect(c.textContent).toContain('file');
    expect(c.textContent).toContain('rel');
  });

  it('links a bare web URL and an autolink, leaving trailing punctuation out', () => {
    const c = md('See https://nvd.nist.gov/vuln/detail/CVE-2024-1. Also <https://a.example/b>.');
    const hrefs = Array.from(c.querySelectorAll('a')).map((a) => a.getAttribute('href'));
    expect(hrefs).toEqual(['https://nvd.nist.gov/vuln/detail/CVE-2024-1', 'https://a.example/b']);
  });

  it('turns a heading into a bold paragraph, never a heading element', () => {
    const c = md('# Big title\n\nSub\n---\n\nbody');
    expect(c.querySelector('h1, h2, h3, h4, h5, h6')).toBeNull();
    const strongs = Array.from(c.querySelectorAll('p > strong')).map((s) => s.textContent);
    expect(strongs).toEqual(['Big title', 'Sub']);
  });

  it('renders lists, code blocks and paragraphs; a single newline is a space', () => {
    const c = md('1. first\n2. second\n   - nested\n\n```\n<b>raw</b>\n```\n\nline one\nline two');
    expect(c.querySelectorAll('ol > li')).toHaveLength(2);
    expect(c.querySelector('ol li ul li')?.textContent).toBe('nested');
    expect(c.querySelector('pre code')?.textContent).toBe('<b>raw</b>');
    expect(c.querySelector('b')).toBeNull();
    const paras = c.querySelectorAll('p');
    expect(paras[paras.length - 1]?.textContent).toBe('line one line two');
  });

  it('honours a hard line break', () => {
    const c = md('one  \ntwo');
    expect(c.querySelector('br')).not.toBeNull();
  });
});

describe('SafeMarkdown — the finding’s own images (evidence:<id>)', () => {
  // The finding's ticked images: 57 (bytes loaded) and 58 (still loading).
  const resolver = (): EvidenceResolver & { ensure: ReturnType<typeof vi.fn> } => ({
    lookup: (id) => (id === 57 ? { caption: 'Stored caption', src: 'blob:fifty-seven' }
      : id === 58 ? { caption: null } : null),
    ensure: vi.fn(),
  });
  const withImages = (text: string, evidence = resolver()) => ({
    c: render(<SafeMarkdown text={text} evidence={evidence} />).container, evidence,
  });

  it('shows a reference to one of the finding’s images as that image, from the resolver’s URL', () => {
    const { c, evidence } = withImages('Before\n\n![The relayed session](evidence:57)\n\nAfter ![](evidence:57)');
    const imgs = Array.from(c.querySelectorAll('img'));
    expect(imgs.map((i) => i.getAttribute('src'))).toEqual(['blob:fifty-seven', 'blob:fifty-seven']);
    // The alt text is the caption for that place; empty, the stored caption.
    expect(imgs.map((i) => i.getAttribute('alt'))).toEqual(['The relayed session', 'Stored caption']);
    expect(c.textContent).toContain('Figure: The relayed session');
    expect(c.textContent).toContain('Figure: Stored caption');
    expect(evidence.ensure).toHaveBeenCalledWith(57);
  });

  it('asks for the bytes of a known image and waits without a broken picture', () => {
    const { c, evidence } = withImages('![](evidence:58)');
    expect(c.querySelector('img')).toBeNull();
    expect(c.textContent).toContain('Loading image 58');
    expect(c.textContent).toContain('no caption');
    expect(evidence.ensure).toHaveBeenCalledWith(58);
  });

  it('says so when a reference is not one of the finding’s “In report” images', () => {
    const { c, evidence } = withImages('![gone](evidence:999)');
    expect(c.querySelector('img')).toBeNull();
    const note = c.querySelector('[role="note"]');
    expect(note?.textContent).toContain('Image not available: evidence:999');
    expect(note?.textContent).toContain('“gone” as text');
    expect(evidence.ensure).not.toHaveBeenCalled();
  });

  it('still reduces every OTHER image to its alt text and loads nothing', () => {
    const { c } = withImages(
      '![web](https://example.com/x.png) ![js](javascript:alert(1)) ![data](data:image/png;base64,AAAA) '
      + '![path](../../etc/passwd) ![near](evidence:57.png) ![also](https://evil.example/evidence:57)\n\n'
      + '<img src="evidence:57" onerror="alert(1)">',
    );
    expect(c.querySelector('img')).toBeNull();
    expect(c.querySelector('[role="note"]')).toBeNull();
    expect(c.textContent).toContain('web js data path near also');
    expect(c.textContent).toContain('<img src="evidence:57" onerror="alert(1)">');
  });

  it('renders a reference inside a table cell and a list item', () => {
    const { c } = withImages('| a | b |\n|---|---|\n| x | ![cell](evidence:57) |\n\n- item ![in list](evidence:57)');
    expect(c.querySelector('td img')?.getAttribute('src')).toBe('blob:fifty-seven');
    expect(c.querySelector('li img')?.getAttribute('alt')).toBe('in list');
  });

  it('without a resolver, marks the reference as text and loads nothing', () => {
    const c = md('See ![The relayed session](evidence:57) and ![](evidence:9).');
    expect(c.querySelector('img')).toBeNull();
    expect(c.textContent).toBe('See [image 57: The relayed session] and [image 9].');
  });
});
