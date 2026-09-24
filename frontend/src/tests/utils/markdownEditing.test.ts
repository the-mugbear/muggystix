/**
 * Markdown editing helpers (5.293.0): the toolbar's edits, and the table
 * rules the client report applies (Pandoc's GitHub Markdown reader).
 */
import { describe, expect, it } from 'vitest';

import {
  TABLE_TEMPLATE,
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
