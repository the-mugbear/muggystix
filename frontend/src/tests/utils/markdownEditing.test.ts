/**
 * Markdown editing helpers (5.293.0): the toolbar's edits, and the table
 * rules the client report applies (Pandoc's GitHub Markdown reader).
 */
import { describe, expect, it } from 'vitest';

import {
  CODE_TEMPLATE,
  TABLE_TEMPLATE,
  listingCaptionOf,
  tableCaptionOf,
  insertBlock,
  insertLink,
  prefixLines,
  splitTableRow,
  tableAlignments,
  tablesAfterText,
  wrapSelection,
} from '../../utils/markdownEditing';

const at = (value: string, start: number, end = start) => ({ value, start, end });

describe('toolbar edits', () => {
  it('wraps the selection, or a selected placeholder', () => {
    expect(wrapSelection(at('a word b', 2, 6), '**', 'bold')).toEqual({ value: 'a **word** b', start: 4, end: 8 });
    const empty = wrapSelection(at('ab', 1), '_', 'italic text');
    expect(empty.value).toBe('a_italic text_b');
    expect(empty.value.slice(empty.start, empty.end)).toBe('italic text');
  });

  it('selects the URL of a new link', () => {
    const e = insertLink(at('see docs', 4, 8));
    expect(e.value).toBe('see [docs](https://)');
    expect(e.value.slice(e.start, e.end)).toBe('https://');
  });

  it('turns every selected line into a list item', () => {
    expect(prefixLines(at('one\ntwo\nthree', 0, 13), true).value).toBe('1. one\n2. two\n3. three');
    expect(prefixLines(at('intro\nitem', 8), false).value).toBe('intro\n- item');
  });

  it('puts a block on lines of its own with a blank line either side', () => {
    const e = insertBlock(at('Before.After.', 7), TABLE_TEMPLATE, 'Column');
    expect(e.value).toBe(`Before.\n\n${TABLE_TEMPLATE}\n\nAfter.`);
    expect(e.value.slice(e.start, e.end)).toBe('Column');
    expect(insertBlock(at('', 0), TABLE_TEMPLATE).value).toBe(TABLE_TEMPLATE);
    expect(insertBlock(at('Text\n\n', 6), TABLE_TEMPLATE).value).toBe(`Text\n\n${TABLE_TEMPLATE}`);
  });

  it('the table template ends with an EMPTY caption line: no placeholder word that could be issued', () => {
    expect(TABLE_TEMPLATE).toBe('| Column | Column |\n| ------ | ------ |\n| Value  | Value  |\n\nTable: ');
    // The report's rule reads it as a caption with no text ("Table N" alone).
    expect(tableCaptionOf(TABLE_TEMPLATE.split('\n\n')[1])).toBe('');
  });

  it('reads a caption only from a paragraph that opens with Table:', () => {
    expect(tableCaptionOf('Table: Accounts found')).toBe('Accounts found');
    expect(tableCaptionOf('table:no space')).toBe('no space');
    expect(tableCaptionOf('Table:')).toBe('');
    expect(tableCaptionOf('Table: over\ntwo lines')).toBe('over\ntwo lines');
    expect(tableCaptionOf('TABLE: shouted')).toBeNull();
    expect(tableCaptionOf('See Table: one')).toBeNull();
    expect(tableCaptionOf('Tables: plural')).toBeNull();
  });
});

describe('the report\'s table rules', () => {
  it('splits rows on unescaped pipes, with or without outer pipes', () => {
    expect(splitTableRow('| January  | $250    |')).toEqual(['January', '$250']);
    expect(splitTableRow('A | B')).toEqual(['A', 'B']);
    expect(splitTableRow('| a \\| b | c |')).toEqual(['a | b', 'c']);
  });

  it('needs a dashes row as wide as the header', () => {
    expect(tableAlignments('| A | B | C |', '|---|:-:|--:|')).toEqual([null, 'center', 'right']);
    expect(tableAlignments('A | B', '--- | ---')).toEqual([null, null]);
    expect(tableAlignments('| A | B |', '|---|')).toBeNull();
    expect(tableAlignments('Heading', '---')).toBeNull();
  });

  it('flags a table written straight after text, and only that', () => {
    const table = '| A | B |\n| - | - |\n| 1 | 2 |';
    expect(tablesAfterText(`Intro line.\n${table}`)).toEqual([2]);
    expect(tablesAfterText(`Intro line.\n\n${table}`)).toEqual([]);
    expect(tablesAfterText(`## Heading\n${table}`)).toEqual([]);
    expect(tablesAfterText(table)).toEqual([]);
    expect(tablesAfterText(`\`\`\`\ntext\n${table}\n\`\`\``)).toEqual([]);
    expect(tablesAfterText(`One.\n${table}\n\nTwo.\n${table}`)).toEqual([2, 7]);
  });
});

// Owner, 2026-10-14: code blocks have their own count.  The Code block button
// writes an empty `Listing:` line, as the Table button writes `Table:`; the
// preview pairs a `Listing:` paragraph with the code block it follows.
describe('code block captions', () => {
  it('the Code block button writes an empty caption line', () => {
    expect(CODE_TEMPLATE).toBe('```\ncommand or output\n```\n\nListing: ');
  });
  it('reads a caption as the report does', () => {
    expect(listingCaptionOf('Listing: The share list')).toBe('The share list');
    expect(listingCaptionOf('listing:')).toBe('');
    expect(listingCaptionOf('A Listing: in prose')).toBeNull();
    expect(listingCaptionOf('Table: not a listing')).toBeNull();
  });
});
