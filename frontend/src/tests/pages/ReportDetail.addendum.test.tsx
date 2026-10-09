/**
 * An addendum's lead sentence says what changed since its baseline — and
 * (2026-10-01) counts reported findings whose severity changed.  The summary
 * also says how many test results the report prints as its proof, and how
 * many of those an agent recorded.
 */
import { render, screen } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { describe, it, expect, vi, beforeEach } from 'vitest';

const getClientReport = vi.fn();
vi.mock('../../services/api', () => ({
  getClientReport: (...a: unknown[]) => getClientReport(...a),
  downloadClientReportScope: vi.fn(),
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
// The project on screen: every request names it first.
vi.mock('../../contexts/ProjectContext', () => ({ useProject: () => ({ currentProject: { id: 1, name: 'P' } }) }));

import ReportDetail, { evidenceRecordsNotPrintedNotice, evidenceRecordsNotice } from '../../pages/ReportDetail';

const addendum = (summary: Record<string, unknown>) => ({
  id: 12, project_id: 1, kind: 'addendum', status: 'issued', title: 'Addendum', number: 4, template: 'pentest',
  baseline: { id: 9, number: 3, title: 'Report', status: 'issued', issued_at: null },
  revision_of: null, superseded_by: null,
  settings: { client_name: 'Acme', classification: null, engagement_type: null, testers: [], distribution: [], system_description: null },
  executive_summary: null, template_fingerprint: 'abc', quarto_version: '1.6', render_status: 'done', render_error: null,
  files: [], created_by_name: 'Admin', issued_by_name: 'Admin', created_at: null, updated_at: null, issued_at: null,
  summary: { counts: { critical: 0, high: 0, medium: 0, low: 0, info: 0, total: 0 }, ...summary },
  can_edit: false, can_issue: true,
});

const show = async (summary: Record<string, unknown>) => {
  getClientReport.mockResolvedValue(addendum(summary));
  render(
    <MemoryRouter initialEntries={['/reports/12']}>
      <Routes><Route path="/reports/:reportId" element={<ReportDetail />} /></Routes>
    </MemoryRouter>,
  );
  return (await screen.findByText(/Compared with report #3/)).textContent ?? '';
};

beforeEach(() => { getClientReport.mockReset(); });

describe('ReportDetail — what an addendum says changed', () => {
  it('counts reported findings with a changed severity, singular and plural', async () => {
    const one = await show({ delta: { new_findings: 2, findings_with_new_endpoints: 1, withdrawn: 0, findings_with_changed_severity: 1 } });
    expect(one).toBe('Compared with report #3: 2 new findings, 1 reported finding on further systems, 0 withdrawals, 1 reported finding with a changed severity.');
  });

  it('…plural', async () => {
    const many = await show({ delta: { new_findings: 0, findings_with_new_endpoints: 0, withdrawn: 0, findings_with_changed_severity: 3 } });
    expect(many).toContain(', 3 reported findings with a changed severity.');
  });

  it('leaves the clause out at 0, and for a report issued before it was counted', async () => {
    const zero = await show({ delta: { new_findings: 1, findings_with_new_endpoints: 0, withdrawn: 0, findings_with_changed_severity: 0 } });
    expect(zero).toBe('Compared with report #3: 1 new finding, 0 reported findings on further systems, 0 withdrawals.');
  });

  it('…absent field', async () => {
    const old = await show({ delta: { new_findings: 1, findings_with_new_endpoints: 0, withdrawn: 2 } });
    expect(old).not.toContain('changed severity');
    expect(old).toContain('2 withdrawals.');
  });
});

describe('ReportDetail — test results printed as proof', () => {
  it('says how many are printed and how many an agent recorded', async () => {
    await show({ delta: { new_findings: 0, findings_with_new_endpoints: 0, withdrawn: 0 }, evidence_records: 7, agent_evidence_records: 2 });
    expect(screen.getByTestId('report-evidence-notice').textContent)
      .toBe('7 test results are printed as how findings were confirmed; 2 were recorded by an agent.');
  });

  it('omits the agent clause when none was, and the line when there are none', async () => {
    expect(evidenceRecordsNotice(1, 0)).toBe('1 test result is printed as how findings were confirmed.');
    expect(evidenceRecordsNotice(4)).toBe('4 test results are printed as how findings were confirmed.');
    expect(evidenceRecordsNotice(3, 1)).toBe('3 test results are printed as how findings were confirmed; 1 was recorded by an agent.');
    await show({ delta: { new_findings: 0, findings_with_new_endpoints: 0, withdrawn: 0 } });
    expect(screen.queryByTestId('report-evidence-notice')).toBeNull();
    expect(screen.queryByTestId('report-evidence-not-printed')).toBeNull();
  });

  it('says how many are not printed because their finding is listed without its details', async () => {
    // Review 2026-10-01 S2: an addendum's already-reported findings.
    await show({
      delta: { new_findings: 1, findings_with_new_endpoints: 1, withdrawn: 0 },
      evidence_records: 1, agent_evidence_records: 0, evidence_records_not_printed: 3,
    });
    expect(screen.getByTestId('report-evidence-notice').textContent)
      .toBe('1 test result is printed as how findings were confirmed.');
    expect(screen.getByTestId('report-evidence-not-printed').textContent)
      .toBe('3 further test results belong to findings this report lists without their details; they are not printed.');
    expect(evidenceRecordsNotPrintedNotice(1))
      .toBe('1 further test result belongs to findings this report lists without their details; it is not printed.');
  });
});
