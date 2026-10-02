/**
 * Visual pass 2026-10-01 — on the Hosts table a ticked row was not marked
 * (`data-state` was on no `<tr>`, so the selected fill never painted), and
 * with 2 of 25 rows ticked the header box looked fully checked: the Checkbox
 * drew the same tick for "some" as for "all".
 */
import React, { useState } from 'react';
import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import type { ColumnDef, RowSelectionState } from '@tanstack/react-table';

import { Checkbox } from '../../components/ui/checkbox';
import { DataTableShell, selectionColumn, useDataTable } from '../../components/ui/data-table';
import { LIST_CURSOR_CLASS } from '../../hooks/useListCursor';

interface Row { id: number; ip: string }
const ROWS: Row[] = [1, 2, 3].map((id) => ({ id, ip: `10.0.0.${id}` }));

const Harness: React.FC<{ withSelection?: boolean; cursor?: number }> = ({ withSelection = true, cursor }) => {
  const [rowSelection, setRowSelection] = useState<RowSelectionState>({});
  const columns = React.useMemo<ColumnDef<Row, unknown>[]>(() => [
    ...(withSelection ? [selectionColumn<Row>({ ariaLabel: (row) => `Select ${row.original.ip}` })] : []),
    { id: 'ip', header: 'Address', cell: ({ row }) => row.original.ip },
  ], [withSelection]);
  const table = useDataTable<Row>({
    data: ROWS, columns, getRowId: (r) => String(r.id),
    rowSelection, onRowSelectionChange: setRowSelection, enableRowSelection: withSelection,
    manualPagination: true, manualSorting: true,
  });
  return (
    <DataTableShell<Row>
      table={table}
      getRowClassName={(row) => (row.index === cursor ? LIST_CURSOR_CLASS : undefined)}
    />
  );
};

const rowOf = (ip: string) => screen.getByText(ip).closest('tr') as HTMLTableRowElement;
const selectAll = () => screen.getByRole('checkbox', { name: 'Select all rows on this page' });

describe('a table with a selection column', () => {
  it('marks a ticked row as selected, and only that row', () => {
    render(<Harness />);
    expect(rowOf('10.0.0.2')).not.toHaveAttribute('data-state');
    expect(rowOf('10.0.0.2')).toHaveAttribute('aria-selected', 'false');
    fireEvent.click(screen.getByRole('checkbox', { name: 'Select 10.0.0.2' }));
    expect(rowOf('10.0.0.2')).toHaveAttribute('data-state', 'selected');
    expect(rowOf('10.0.0.2')).toHaveAttribute('aria-selected', 'true');
    expect(rowOf('10.0.0.2').className).toContain('data-[state=selected]:bg-accent');
    expect(rowOf('10.0.0.1')).not.toHaveAttribute('data-state');
    fireEvent.click(screen.getByRole('checkbox', { name: 'Select 10.0.0.2' }));
    expect(rowOf('10.0.0.2')).not.toHaveAttribute('data-state');
  });

  it('keeps the keyboard cursor’s ring on a row that is also selected', () => {
    render(<Harness cursor={1} />);
    fireEvent.click(screen.getByRole('checkbox', { name: 'Select 10.0.0.2' }));
    const row = rowOf('10.0.0.2');
    expect(row).toHaveAttribute('data-state', 'selected');
    // The fill says "selected" (the attribute rule outranks the cursor's own
    // tint); the ring, which no selected row has, still says "you are here".
    for (const cls of LIST_CURSOR_CLASS.split(' ').filter((c) => c.startsWith('ring'))) {
      expect(row.className).toContain(cls);
    }
  });

  it('says nothing about selection on a table that has none', () => {
    render(<Harness withSelection={false} />);
    expect(rowOf('10.0.0.1')).not.toHaveAttribute('aria-selected');
    expect(rowOf('10.0.0.1')).not.toHaveAttribute('data-state');
  });

  it('shows "some" on the header box for a partial selection, and a click then selects all', () => {
    render(<Harness />);
    expect(selectAll()).toHaveAttribute('aria-checked', 'false');
    fireEvent.click(screen.getByRole('checkbox', { name: 'Select 10.0.0.1' }));
    expect(selectAll()).toHaveAttribute('aria-checked', 'mixed');
    expect(selectAll().querySelector('[data-glyph="some"]')).not.toBeNull();
    expect(selectAll().querySelector('[data-glyph="all"]')).toBeNull();

    fireEvent.click(selectAll());
    expect(selectAll()).toHaveAttribute('aria-checked', 'true');
    expect(selectAll().querySelector('[data-glyph="all"]')).not.toBeNull();
    for (const ip of ['10.0.0.1', '10.0.0.2', '10.0.0.3']) expect(rowOf(ip)).toHaveAttribute('data-state', 'selected');

    fireEvent.click(selectAll());
    expect(selectAll()).toHaveAttribute('aria-checked', 'false');
    expect(rowOf('10.0.0.1')).not.toHaveAttribute('data-state');
  });
});

describe('Checkbox', () => {
  it('draws a dash for "some" and a tick for "all" — never the same mark for both', () => {
    const { rerender } = render(<Checkbox checked="indeterminate" aria-label="box" onCheckedChange={() => {}} />);
    const box = screen.getByRole('checkbox', { name: 'box' });
    expect(box.querySelector('[data-glyph="some"]')).not.toBeNull();
    expect(box.querySelector('[data-glyph="all"]')).toBeNull();
    rerender(<Checkbox checked aria-label="box" onCheckedChange={() => {}} />);
    expect(box.querySelector('[data-glyph="all"]')).not.toBeNull();
    expect(box.querySelector('[data-glyph="some"]')).toBeNull();
    rerender(<Checkbox checked={false} aria-label="box" onCheckedChange={() => {}} />);
    expect(box.querySelector('[data-glyph]')).toBeNull();
  });

  it('draws the right mark when it keeps its own state', () => {
    render(<Checkbox defaultChecked="indeterminate" aria-label="own" />);
    const box = screen.getByRole('checkbox', { name: 'own' });
    expect(box.querySelector('[data-glyph="some"]')).not.toBeNull();
    fireEvent.click(box);
    expect(box.querySelector('[data-glyph="all"]')).not.toBeNull();
    expect(box.querySelector('[data-glyph="some"]')).toBeNull();
  });
});
