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

import ReportDetail, { evidenceImagesLine } from '../../pages/ReportDetail';

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

  // Review 2026-10-01 S2 — the line said "N placed in text, M under Evidence"
  // whatever the template; it now says what THIS report prints.
  const ALL = { fields: ['description', 'impact', 'recommendation', 'references', 'steps_to_reproduce'], trailing: true };
  const NO_REASON = { finding_not_detailed: 0, section_not_printed: 0, no_evidence_block: 0 };
  const line = async (summary: Record<string, unknown>) => {
    const withImages = report(null);
    Object.assign(withImages.summary, summary);
    getClientReport.mockResolvedValue(withImages);
    show();
    return (await screen.findByTestId('report-images-line')).textContent;
  };

  it('says how many images print in the text and how many under Evidence', async () => {
    expect(await line({
      images: 5, images_placed: 3, images_unplaced: 2, images_skipped: 1,
      images_printed: 3, images_trailing: 2, images_not_printed: 0,
      images_not_printed_reasons: NO_REASON, template_images: ALL,
    })).toBe('3 in the text, 2 under Evidence · 1 skipped (WebP)');
  });

  it('says so when the template prints no evidence images', async () => {
    expect(await line({
      images: 4, images_placed: 3, images_unplaced: 1,
      images_printed: 0, images_trailing: 0, images_not_printed: 4,
      images_not_printed_reasons: { ...NO_REASON, section_not_printed: 3, no_evidence_block: 1 },
      template_images: { fields: [], trailing: false },
    })).toBe('This template prints no evidence images (4 ticked)');
  });

  it('says which ticked images a template leaves out, and why', async () => {
    expect(await line({
      images: 3, images_placed: 2, images_unplaced: 1,
      images_printed: 1, images_trailing: 0, images_not_printed: 2,
      images_not_printed_reasons: { ...NO_REASON, section_not_printed: 1, no_evidence_block: 1 },
      template_images: { fields: ['recommendation'], trailing: false },
    })).toBe('1 in the text · 2 ticked images are not printed by this template: it prints only images placed in the recommendation');
  });

  it('a report issued before this was measured claims nothing about where images print', async () => {
    // Before images could be placed, and with the earlier split (what the
    // authors did, which is not what a template prints).
    for (const summary of [{ images: 2 }, { images: 5, images_placed: 3, images_unplaced: 2 },
      { images: 5, images_printed: null, images_trailing: null, images_not_printed: null }]) {
      expect(evidenceImagesLine(summary)).toBe('Ticked “In report” on the findings');
    }
    expect(await line({ images: 2, images_placed: 1, images_unplaced: 1 })).toBe('Ticked “In report” on the findings');
  });

  it('words every other case from the measured counts', () => {
    const base = { images: 2, images_printed: 0, images_trailing: 0, images_not_printed: 2 };
    // An addendum's already-reported findings.
    expect(evidenceImagesLine({
      ...base, template_images: ALL, images_not_printed_reasons: { ...NO_REASON, finding_not_detailed: 2 },
    })).toBe('2 ticked images are not printed: their findings are listed in this report without their details');
    expect(evidenceImagesLine({
      images: 3, images_printed: 1, images_trailing: 1, images_not_printed: 1, template_images: ALL,
      images_not_printed_reasons: { ...NO_REASON, finding_not_detailed: 1 },
    })).toBe('1 in the text, 1 under Evidence · 1 ticked image is not printed: its finding is listed in this report without its details');
    // Several fields, and a template that prints only a trailing block.
    expect(evidenceImagesLine({
      ...base, images_not_printed: 1, images_printed: 1, images: 2,
      template_images: { fields: ['description', 'steps_to_reproduce'], trailing: false },
      images_not_printed_reasons: { ...NO_REASON, section_not_printed: 1 },
    })).toBe('1 in the text · 1 ticked image is not printed by this template: it prints only images placed in the description or steps to reproduce');
    expect(evidenceImagesLine({
      images: 2, images_printed: 0, images_trailing: 1, images_not_printed: 1,
      template_images: { fields: [], trailing: true },
      images_not_printed_reasons: { ...NO_REASON, section_not_printed: 1 },
    })).toBe('0 in the text, 1 under Evidence · 1 ticked image is not printed where they are placed: this template prints images only under Evidence');
    // No declaration to quote: still no false claim.
    expect(evidenceImagesLine({ ...base, template_images: null, images_not_printed_reasons: null }))
      .toBe('2 ticked images are not printed by this template');
    expect(evidenceImagesLine({ images: 0 })).toBe('');
  });

  it('under the cutoff there is no scope file to send', async () => {
    getClientReport.mockResolvedValue(report(null));
    show();
    await waitFor(() => expect(getClientReport).toHaveBeenCalled());
    await screen.findByText('Report details');
    expect(screen.queryByText(/Scope file/)).not.toBeInTheDocument();
  });
});
