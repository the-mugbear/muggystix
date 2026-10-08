/**
 * The table's rows are memoised, and its header widths come from the column
 * definitions themselves.
 */
import React, { useState } from 'react';
import { act, fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import type { ColumnDef } from '@tanstack/react-table';

import { DataTableShell, useDataTable } from '../../components/ui/data-table';

interface Item { id: number; ip: string }
const ROWS: Item[] = [1, 2, 3].map((id) => ({ id, ip: `10.0.0.${id}` }));

describe('DataTableShell — rows', () => {
  const renders = { count: 0 };
  let bump: () => void = () => {};
  let setSuffix: (s: string) => void = () => {};

  const Harness: React.FC = () => {
    const [, setTick] = useState(0);
    const [suffix, setSuffixState] = useState('');
    bump = () => setTick((n) => n + 1);
    setSuffix = setSuffixState;
    const columns = React.useMemo<ColumnDef<Item, unknown>[]>(() => [
      { id: 'ip', header: 'Address', cell: ({ row }) => { renders.count += 1; return `${row.original.ip}${suffix}`; } },
    ], [suffix]);
    const table = useDataTable<Item>({ data: ROWS, columns, getRowId: (r) => String(r.id), manualPagination: true, manualSorting: true });
    const onRowClick = React.useCallback(() => undefined, []);
    return <DataTableShell<Item> table={table} onRowClick={onRowClick} />;
  };

  it('a re-render of the page with the same rows does not render their cells again', () => {
    renders.count = 0;
    render(<Harness />);
    expect(renders.count).toBe(ROWS.length);
    act(() => bump());
    act(() => bump());
    expect(renders.count).toBe(ROWS.length);
  });

  it('new column definitions do render them again', () => {
    renders.count = 0;
    render(<Harness />);
    act(() => setSuffix(' (changed)'));
    expect(screen.getByText('10.0.0.2 (changed)')).toBeInTheDocument();
    expect(renders.count).toBe(ROWS.length * 2);
  });

  it('expanding a row shows its sub-row', () => {
    const Expandable: React.FC = () => {
      const [expanded, setExpanded] = useState({});
      const columns = React.useMemo<ColumnDef<Item, unknown>[]>(() => [
        { id: 'ip', header: 'Address', cell: ({ row }) => <button onClick={() => row.toggleExpanded()}>{row.original.ip}</button> },
      ], []);
      const table = useDataTable<Item>({
        data: ROWS, columns, getRowId: (r) => String(r.id), expanded, onExpandedChange: setExpanded as never,
        getRowCanExpand: () => true, manualPagination: true, manualSorting: true,
      });
      const renderSubRow = React.useCallback((row: { original: Item }) => `more about ${row.original.ip}`, []);
      return <DataTableShell<Item> table={table} renderSubRow={renderSubRow} />;
    };
    render(<Expandable />);
    expect(screen.queryByText('more about 10.0.0.2')).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '10.0.0.2' }));
    expect(screen.getByText('more about 10.0.0.2')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '10.0.0.2' }));
    expect(screen.queryByText('more about 10.0.0.2')).not.toBeInTheDocument();
  });
});

describe('DataTableShell — header widths', () => {
  it('a column is as wide as its definition says — 150 included — and unsized when it says nothing', () => {
    const Widths: React.FC = () => {
      const columns = React.useMemo<ColumnDef<Item, unknown>[]>(() => [
        { id: 'a', header: 'Fixed', size: 150, cell: () => null },
        { id: 'b', header: 'Share', size: 90, meta: { width: '30%' }, cell: () => null },
        { id: 'c', header: 'Spare', cell: () => null },
      ], []);
      const table = useDataTable<Item>({ data: ROWS, columns, manualPagination: true, manualSorting: true });
      return <DataTableShell<Item> table={table} />;
    };
    render(<Widths />);
    expect(screen.getByRole('columnheader', { name: 'Fixed' })).toHaveStyle({ width: '150px' });
    expect(screen.getByRole('columnheader', { name: 'Share' })).toHaveStyle({ width: '30%' });
    expect(screen.getByRole('columnheader', { name: 'Spare' }).style.width).toBe('');
  });
});
