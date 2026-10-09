/**
 * The export dialogs said the raw client error ("Request failed with status
 * code 403").  They say what the rest of the app says (`formatApiError`): the
 * server's own words when it gave any, else the meaning of the status.
 */
import type { ReactElement } from 'react';
import { fireEvent, render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';

const api = vi.hoisted(() => ({
  getScopeHostList: vi.fn(), getOutOfScopeHostList: vi.fn(), getToolReadyOutput: vi.fn(), enqueueInventoryJson: vi.fn(),
}));
vi.mock('../../services/api', () => ({
  ...api,
  downloadInventoryCsv: vi.fn(),
  downloadReportJob: vi.fn(),
  listReportJobs: vi.fn().mockResolvedValue([]),
  dismissReportJob: vi.fn(), retryReportJob: vi.fn(), cancelReportJob: vi.fn(),
}));

import InventoryDownloadDialog from '../../components/InventoryDownloadDialog';
import OutOfScopeExport from '../../components/OutOfScopeExport';
import ScopeExport from '../../components/ScopeExport';
import ToolReadyOutput from '../../components/ToolReadyOutput';
import { TooltipProvider } from '../../components/ui/tooltip';

/** What axios rejects with: a message nobody should read, and the response. */
const refused = (status: number, detail?: string) => Object.assign(
  new Error(`Request failed with status code ${status}`),
  { response: { status, data: detail ? { detail } : {} } },
);

const show = (ui: ReactElement) => render(<MemoryRouter><TooltipProvider>{ui}</TooltipProvider></MemoryRouter>);

beforeEach(() => {
  vi.spyOn(console, 'error').mockImplementation(() => {});
  Object.values(api).forEach((m) => m.mockReset());
});

describe('export dialogs — a refused request', () => {
  it('the scope export says the meaning of a 403, not the client error', async () => {
    api.getScopeHostList.mockRejectedValue(refused(403));
    show(<ScopeExport open onClose={vi.fn()} scopeId={3} scopeName="DMZ" />);
    fireEvent.click(screen.getByRole('button', { name: 'Generate list' }));
    expect(await screen.findByText('You do not have permission to perform this action.')).toBeInTheDocument();
    expect(screen.queryByText(/Request failed with status code/)).not.toBeInTheDocument();
  });

  it('the out-of-scope export says the server’s own reason when it gives one', async () => {
    api.getOutOfScopeHostList.mockRejectedValue(refused(403, 'Exports need the auditor role on this project.'));
    show(<OutOfScopeExport open onClose={vi.fn()} />);
    fireEvent.click(screen.getByRole('button', { name: 'Generate list' }));
    expect(await screen.findByText('Exports need the auditor role on this project.')).toBeInTheDocument();
    expect(screen.queryByText(/Request failed with status code/)).not.toBeInTheDocument();
  });

  it('the tool-ready export does the same', async () => {
    api.getToolReadyOutput.mockRejectedValue(refused(503));
    show(<ToolReadyOutput open onClose={vi.fn()} filters={{}} />);
    fireEvent.click(screen.getByRole('button', { name: 'Generate output' }));
    expect(await screen.findByText('The server is having trouble right now. Please try again shortly.')).toBeInTheDocument();
    expect(screen.queryByText(/Request failed with status code/)).not.toBeInTheDocument();
  });

  it('the inventory download does the same when the JSON cannot be queued', async () => {
    api.enqueueInventoryJson.mockRejectedValue(refused(403));
    show(<InventoryDownloadDialog open onClose={vi.fn()} filters={{}} totalHosts={10} />);
    fireEvent.click(screen.getByRole('button', { name: 'Prepare JSON' }));
    expect(await screen.findByText('You do not have permission to perform this action.')).toBeInTheDocument();
    expect(screen.queryByText(/Request failed with status code|Unknown error/)).not.toBeInTheDocument();
  });
});
