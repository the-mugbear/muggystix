/**
 * The installation's report writing guidance (5.350.0): what a model is told
 * when it drafts a finding's report text.  One record; every signed-in user
 * reads it, a global admin writes it (System settings).
 */
import { api } from './client';

export interface ReportWritingGuidanceSection {
  /** `general` (every section) or a report-text field. */
  key: string;
  label: string;
  /** The text in force: what was saved, else the shipped default. */
  text: string;
  default: string;
  is_default: boolean;
}

export interface ReportWritingGuidance {
  sections: ReportWritingGuidanceSection[];
  /** What always holds, whatever the guidance says. Not editable. */
  fixed_rules: string[];
  /** The in-app drafter's system prompt as it is sent now. */
  drafter_prompt: string;
  max_chars: number;
  updated_at: string | null;
  updated_by: string | null;
}

export const getReportWritingGuidance = async (signal?: AbortSignal): Promise<ReportWritingGuidance> =>
  (await api.get<ReportWritingGuidance>('/report-writing-guidance', { signal })).data;

/** Only the keys sent are touched; null or a blank text goes back to the default. */
export const updateReportWritingGuidance = async (
  sections: Record<string, string | null>,
): Promise<ReportWritingGuidance> =>
  (await api.put<ReportWritingGuidance>('/report-writing-guidance', { sections })).data;
