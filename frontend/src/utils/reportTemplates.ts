/**
 * Report templates by what they are for (5.342.0).  Client-free: the pages
 * and their tests import this without the API client.
 */
import type { ReportTemplate } from '../services/api';

/** A report format's name, everywhere a page says one (three copies with
 *  different words until review 2026-10-14).  `pdf` labels a file of a
 *  report issued before PDF was removed (5.293.0). */
export const REPORT_FORMAT_LABEL: Record<string, string> = {
  html: 'HTML', docx: 'Word', qmd: 'QMD source (.zip)', pdf: 'PDF',
};
export const formatLabel = (format: string): string => REPORT_FORMAT_LABEL[format] ?? format;

/** A template a client report can use — the choosers offer only these.  The
 *  other kind (`contact`, the remediation list prepared for one contact) is
 *  listed on the Reports page for its files alone. */
export const isClientTemplate = (t: Pick<ReportTemplate, 'kind'>): boolean => (t.kind ?? 'client') === 'client';
