/**
 * A file save shows that it is running and that it finished.  Users reported
 * downloading several copies of a report: the request gave no sign at all.
 */
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';

const toastMock = { success: vi.fn(), warning: vi.fn(), error: vi.fn(), info: vi.fn() };
vi.mock('../../contexts/ToastContext', () => ({ useToast: () => toastMock }));

import DownloadButton, { savedMessage } from '../../components/DownloadButton';

/** A request the test answers when it chooses. */
const held = <T,>() => {
  let resolve!: (value: T) => void;
  let reject!: (reason: unknown) => void;
  const promise = new Promise<T>((res, rej) => { resolve = res; reject = rej; });
  return { promise, resolve, reject };
};

beforeEach(() => { vi.clearAllMocks(); });

describe('DownloadButton', () => {
  it('says it is downloading, takes no second press, then says the file was saved', async () => {
    const request = held<string>();
    const saveFn = vi.fn(() => request.promise);
    render(<DownloadButton label="Download Word preview" errorText="Could not download the preview." saveFn={saveFn} />);

    const button = screen.getByRole('button', { name: 'Download Word preview' });
    fireEvent.click(button);
    await waitFor(() => expect(button).toHaveTextContent('Downloading…'));
    expect(button).toBeDisabled();
    expect(button).toHaveAttribute('aria-busy', 'true');
    // The button keeps its name while it runs.
    expect(screen.getByRole('button', { name: 'Download Word preview' })).toBe(button);
    fireEvent.click(button);
    fireEvent.click(button);
    expect(saveFn).toHaveBeenCalledTimes(1);
    expect(screen.queryByText('Downloaded')).not.toBeInTheDocument();
    expect(toastMock.success).not.toHaveBeenCalled();

    request.resolve('acme-report-3.docx');
    expect(await screen.findByText('Downloaded')).toBeInTheDocument();
    expect(toastMock.success).toHaveBeenCalledTimes(1);
    expect(toastMock.success).toHaveBeenCalledWith('Saved acme-report-3.docx to your downloads.');
    expect(button).toBeEnabled();
    expect(button).toHaveTextContent('Download Word preview');
    expect(button).not.toHaveAttribute('aria-busy');
  });

  it('names the file it was told about when the request does not answer one', async () => {
    render(<DownloadButton label="Download scope file (CSV)" filename="scope-acme.csv" errorText="x"
      saveFn={() => Promise.resolve()} />);
    fireEvent.click(screen.getByRole('button', { name: 'Download scope file (CSV)' }));
    await waitFor(() => expect(toastMock.success).toHaveBeenCalledWith('Saved scope-acme.csv to your downloads.'));
    expect(savedMessage(null)).toBe('Saved the file to your downloads.');
  });

  it('one file on its way does not hold up another', async () => {
    const html = held<void>();
    const word = vi.fn(() => Promise.resolve());
    render(
      <>
        <DownloadButton compact label="HTML" name="Download r.html" filename="r.html" errorText="x" saveFn={() => html.promise} />
        <DownloadButton compact label="DOCX" name="Download r.docx" filename="r.docx" errorText="x" saveFn={word} />
      </>,
    );
    fireEvent.click(screen.getByRole('button', { name: 'Download r.html' }));
    await waitFor(() => expect(screen.getByRole('button', { name: 'Download r.html' })).toBeDisabled());
    const other = screen.getByRole('button', { name: 'Download r.docx' });
    expect(other).toBeEnabled();
    fireEvent.click(other);
    await waitFor(() => expect(toastMock.success).toHaveBeenCalledWith('Saved r.docx to your downloads.'));
    // Compact: the mark is on the button itself — only on the one that was saved.
    expect(other).toHaveAttribute('data-downloaded');
    expect(other).toHaveAccessibleDescription('Downloaded during this visit');
    expect(screen.getByRole('button', { name: 'Download r.html' })).not.toHaveAttribute('data-downloaded');
    expect(screen.queryByText('Downloaded')).not.toBeInTheDocument();
    html.resolve();
    await waitFor(() => expect(screen.getByRole('button', { name: 'Download r.html' })).toHaveAttribute('data-downloaded'));
  });

  it('a failure is said, leaves no mark, and the button can be pressed again', async () => {
    const saveFn = vi.fn()
      .mockRejectedValueOnce(new Error('HTTP 410'))
      .mockResolvedValueOnce('again.html');
    render(<DownloadButton label="Download HTML preview" errorText="Could not download the preview." saveFn={saveFn} />);
    const button = screen.getByRole('button', { name: 'Download HTML preview' });
    fireEvent.click(button);
    await waitFor(() => expect(toastMock.error).toHaveBeenCalledTimes(1));
    expect(String(toastMock.error.mock.calls[0][0])).toContain('Could not download the preview.');
    expect(toastMock.success).not.toHaveBeenCalled();
    expect(screen.queryByText('Downloaded')).not.toBeInTheDocument();
    await waitFor(() => expect(button).toBeEnabled());

    fireEvent.click(button);
    expect(await screen.findByText('Downloaded')).toBeInTheDocument();
    expect(saveFn).toHaveBeenCalledTimes(2);
  });

  it('a later failure does not take back that the file was saved', async () => {
    const saveFn = vi.fn().mockResolvedValueOnce('a.html').mockRejectedValueOnce(new Error('HTTP 500'));
    render(<DownloadButton label="Download" errorText="Could not download." saveFn={saveFn} />);
    fireEvent.click(screen.getByRole('button', { name: 'Download' }));
    expect(await screen.findByText('Downloaded')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Download' }));
    await waitFor(() => expect(toastMock.error).toHaveBeenCalledTimes(1));
    expect(screen.getByText('Downloaded')).toBeInTheDocument();
  });
});
