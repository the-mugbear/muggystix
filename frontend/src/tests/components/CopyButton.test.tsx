/**
 * `ui/code-block.CopyButton` is the one copy icon button (review 2026-10-23b,
 * FEC-7: the host tests had a second).  A copy says that it worked, or that it
 * did not; the click goes no further than the button.
 */
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

const clipboard = vi.hoisted(() => ({ copyToClipboard: vi.fn() }));
vi.mock('../../utils/clipboard', () => clipboard);

import { CopyButton } from '../../components/ui/code-block';

describe('CopyButton', () => {
  it('copies the text, and the click does not reach the row it sits on', async () => {
    clipboard.copyToClipboard.mockResolvedValue(true);
    const onRow = vi.fn();
    render(<div onClick={onRow}><CopyButton text="nxc smb 10.0.0.5" label="Copy command" size="compact" /></div>);
    const button = screen.getByRole('button', { name: 'Copy command' });
    expect(button).toHaveClass('size-7');
    fireEvent.click(button);
    await waitFor(() => expect(clipboard.copyToClipboard).toHaveBeenCalledWith('nxc smb 10.0.0.5'));
    expect(onRow).not.toHaveBeenCalled();
    await waitFor(() => expect(button.querySelector('.text-success')).not.toBeNull());
  });

  it('shows that a copy failed, rather than nothing', async () => {
    clipboard.copyToClipboard.mockResolvedValue(false);
    render(<CopyButton text="x" label="Copy command" />);
    const button = screen.getByRole('button', { name: 'Copy command' });
    fireEvent.click(button);
    await waitFor(() => expect(button.querySelector('.text-destructive')).not.toBeNull());
    expect(button.querySelector('.text-success')).toBeNull();
  });
});
