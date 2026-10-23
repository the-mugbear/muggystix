/**
 * The one "why?" dialog for closing a finding (review 2026-10-22, B3-1):
 * the Findings list (single and bulk), the finding page and the host
 * inspector's Findings section all open it.
 */
import { render, screen, fireEvent } from '@testing-library/react';
import { describe, it, expect, vi } from 'vitest';
import { StatusReasonDialog, type StatusReasonRequest } from '../../components/findings/StatusReasonDialog';

describe('StatusReasonDialog', () => {
  it('names a bulk change’s count and how many are off the page', () => {
    render(
      <StatusReasonDialog
        request={{ status: 'accepted_risk', bulk: { count: 3, offscreen: 2 } }}
        onCancel={vi.fn()} onSave={vi.fn()}
      />,
    );
    expect(screen.getByRole('dialog', { name: 'Mark 3 findings Accepted risk' })).toHaveTextContent(
      '2 of the 3 selected are not on this page.',
    );
  });

  it('hands back the trimmed reason, or none', () => {
    const onSave = vi.fn();
    render(<StatusReasonDialog request={{ status: 'remediated' }} onCancel={vi.fn()} onSave={onSave} />);
    fireEvent.click(screen.getByRole('button', { name: 'Save without a reason' }));
    expect(onSave).toHaveBeenLastCalledWith(undefined);
    fireEvent.change(screen.getByLabelText('Disposition reason'), { target: { value: '  patched ' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    expect(onSave).toHaveBeenLastCalledWith('patched');
  });

  it('every opening starts with an empty reason', () => {
    const first: StatusReasonRequest = { status: 'remediated' };
    const { rerender } = render(<StatusReasonDialog request={first} onCancel={vi.fn()} onSave={vi.fn()} />);
    fireEvent.change(screen.getByLabelText('Disposition reason'), { target: { value: 'left over' } });
    rerender(<StatusReasonDialog request={null} onCancel={vi.fn()} onSave={vi.fn()} />);
    rerender(<StatusReasonDialog request={{ status: 'false_positive' }} onCancel={vi.fn()} onSave={vi.fn()} />);
    expect(screen.getByLabelText('Disposition reason')).toHaveValue('');
  });
});
