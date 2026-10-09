/**
 * Default credentials: the sheet is CSV, and is read as CSV.  A password
 * holding a comma (quoted in the sheet) was cut at the comma — the reader was
 * shown, and copied, the first part only.
 */
import { render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import DefaultCredentials from '../../pages/DefaultCredentials';
import { TooltipProvider } from '../../components/ui/tooltip';

const serveSheet = (text: string) => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: true, text: async () => text }));
};

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('DefaultCredentials', () => {
  it('shows a quoted password whole: its comma and its quote', async () => {
    serveSheet([
      'productvendor,username,password',
      'Acme,admin,"pa,ss""word"',
      'Zyx,root,<blank>',
      '',
    ].join('\n'));
    render(<TooltipProvider><DefaultCredentials /></TooltipProvider>);

    expect(await screen.findByText('pa,ss"word')).toBeInTheDocument();
    expect(screen.queryByText('"pa')).not.toBeInTheDocument();
    // The rows around it read as before.
    expect(screen.getByText('root')).toBeInTheDocument();
    expect(screen.getByText('(blank)')).toBeInTheDocument();
    expect(screen.getByText(/2 of 2 credentials · 2 vendors/)).toBeInTheDocument();
  });
});
