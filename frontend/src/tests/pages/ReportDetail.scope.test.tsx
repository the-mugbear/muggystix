/**
 * v5.319.0 — a report whose scope is over its template's cutoff names a
 * separate scope file; the report's page says so, shows the file's SHA-256
 * and downloads it.  Under the cutoff there is nothing to send.
 */
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { describe, it, expect, vi, beforeEach } from 'vitest';

const getClientReport = vi.fn();
const downloadClientReportScope = vi.fn();
vi.mock('../../services/api', () => ({
  getClientReport: (...a: unknown[]) => getClientReport(...a),
  downloadClientReportScope: (...a: unknown[]) => downloadClientReportScope(...a),
  listReportTemplates: vi.fn().mockResolvedValue([]),
  listProjectMembers: vi.fn().mockResolvedValue([]),
  getReportJob: vi.fn(),
  deleteClientReport: vi.fn(),
  downloadReportJob: vi.fn(),
  issueClientReport: vi.fn(),
  previewClientReport: vi.fn(),
  rerenderClientReport: vi.fn(),
  reviseClientReport: vi.fn(),
  updateClientReport: vi.fn(),
}));
vi.mock('../../contexts/ToastContext', () => ({
  useToast: () => ({ success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() }),
}));
vi.mock('../../contexts/AuthContext', () => ({
  useAuth: () => ({ user: { id: 1, username: 'admin' }, hasPermission: () => true }),
}));
vi.mock('../../hooks/useVisibilityPoll', () => ({ useVisibilityPoll: () => undefined }));

import ReportDetail from '../../pages/ReportDetail';

const SHA = 'ecfd783943d5e4902bb57603d68a01aba5d69aca4648c543f6e59e968e7d46e8';

const report = (scopeExternal: unknown) => ({
  id: 12, project_id: 1, kind: 'full', status: 'issued', title: 'Report', number: 3, template: 'pentest',
  baseline: null, revision_of: null, superseded_by: null,
  settings: { client_name: 'Acme', classification: null, engagement_type: null, testers: [], distribution: [], system_description: null },
  executive_summary: null, template_fingerprint: 'abc', quarto_version: '1.6', render_status: 'done', render_error: null,
  files: [], created_by_name: 'Admin', issued_by_name: 'Admin', created_at: null, updated_at: null, issued_at: null,
  summary: { counts: { critical: 0, high: 0, medium: 0, low: 0, info: 0, total: 0 }, scope_external: scopeExternal },
  can_edit: false, can_issue: true,
});

const show = () => render(
  <MemoryRouter initialEntries={['/reports/12']}>
    <Routes><Route path="/reports/:reportId" element={<ReportDetail />} /></Routes>
  </MemoryRouter>,
);

beforeEach(() => { getClientReport.mockReset(); downloadClientReportScope.mockReset(); });

describe('ReportDetail — the scope file', () => {
  it('names the file, shows its SHA-256 and downloads it', async () => {
    getClientReport.mockResolvedValue(report({
      networks: 4212, domains: 3, inline_max: 25, domains_inline_max: 25,
      file: { name: 'scope-acme-report-3.csv', sha256: SHA, bytes: 90000 },
    }));
    downloadClientReportScope.mockResolvedValue(undefined);
    show();
    expect(await screen.findByText(/Scope file — send it with the report/)).toBeInTheDocument();
    expect(screen.getByText(SHA)).toBeInTheDocument();
    expect(screen.getByText('scope-acme-report-3.csv')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /Download scope file/ }));
    await waitFor(() => expect(downloadClientReportScope).toHaveBeenCalledWith(12, 'scope-acme-report-3.csv'));
  });

  it('says how many images are placed in text and how many print under Evidence', async () => {
    const withImages = report(null);
    Object.assign(withImages.summary, { images: 5, images_placed: 3, images_unplaced: 2, images_skipped: 1 });
    getClientReport.mockResolvedValue(withImages);
    show();
    expect(await screen.findByText('3 placed in text, 2 under Evidence · 1 skipped (WebP)')).toBeInTheDocument();
  });

  it('a report issued before images could be placed keeps the old line', async () => {
    const before = report(null);
    Object.assign(before.summary, { images: 2 });
    getClientReport.mockResolvedValue(before);
    show();
    expect(await screen.findByText('Ticked “In report” on the findings')).toBeInTheDocument();
  });

  it('under the cutoff there is no scope file to send', async () => {
    getClientReport.mockResolvedValue(report(null));
    show();
    await waitFor(() => expect(getClientReport).toHaveBeenCalled());
    await screen.findByText('Report details');
    expect(screen.queryByText(/Scope file/)).not.toBeInTheDocument();
  });
});
