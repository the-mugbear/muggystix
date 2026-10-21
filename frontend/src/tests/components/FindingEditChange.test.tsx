/**
 * 5.413.0 — an edit on a finding's history shows the field's value before
 * and after, read only when the reader opens it (a section can be 32 KB).
 */
import { render, screen, fireEvent } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';

const getFindingEdit = vi.fn();
vi.mock('../../services/api', () => ({
  getFindingEdit: (...a: unknown[]) => getFindingEdit(...a),
}));

import { FindingEditDisclosure } from '../../components/findings/FindingEditChange';

const open = () => fireEvent.click(screen.getByRole('button', { name: 'Show before and after' }));

describe('FindingEditDisclosure', () => {
  beforeEach(() => getFindingEdit.mockReset());

  it('reads the change only when opened, and shows a short field as two lines', async () => {
    getFindingEdit.mockResolvedValue({ id: 9, field: 'owner', before: null, after: 'Bob' });
    render(<FindingEditDisclosure projectId={1} findingId={4} entryId={9} field="owner" />);
    expect(getFindingEdit).not.toHaveBeenCalled();
    open();
    const change = await screen.findByTestId('edit-change');
    expect(getFindingEdit).toHaveBeenCalledWith(1, 4, 9, expect.anything());
    expect(change).toHaveTextContent('Beforenone');
    expect(change).toHaveTextContent('AfterBob');
    fireEvent.click(screen.getByRole('button', { name: 'Hide before and after' }));
    expect(screen.queryByTestId('edit-change')).toBeNull();
  });

  it('compares a section before and after', async () => {
    getFindingEdit.mockResolvedValue({
      id: 10, field: 'impact', before: 'Traffic can be read.', after: 'Sessions can be decrypted.',
    });
    render(<FindingEditDisclosure projectId={1} findingId={4} entryId={10} field="impact" />);
    open();
    expect(await screen.findByTestId('compare-current')).toHaveTextContent('BeforeTraffic can be read.');
    expect(screen.getByTestId('compare-proposed')).toHaveTextContent('AfterSessions can be decrypted.');
  });

  it('says a section was emptied, and shows what it held', async () => {
    getFindingEdit.mockResolvedValue({ id: 11, field: 'impact', before: 'Traffic can be read.', after: null });
    render(<FindingEditDisclosure projectId={1} findingId={4} entryId={11} field="impact" />);
    open();
    const change = await screen.findByTestId('edit-change');
    expect(change).toHaveTextContent('This edit emptied the section. It held:');
    expect(change).toHaveTextContent('Traffic can be read.');
  });

  it('says when the change could not be read', async () => {
    getFindingEdit.mockRejectedValueOnce({ response: { status: 404, data: { detail: 'No such edit on this finding.' } } });
    render(<FindingEditDisclosure projectId={1} findingId={4} entryId={12} field="title" />);
    open();
    expect(await screen.findByText(/No such edit on this finding/)).toBeInTheDocument();
  });
});
