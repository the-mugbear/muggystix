/**
 * Report templates by what they are for (5.342.0).  Client-free: the pages
 * and their tests import this without the API client.
 */
import type { ReportTemplate } from '../services/api';

/** A template a client report can use — the choosers offer only these.  The
 *  other kind (`contact`, the remediation list prepared for one contact) is
 *  listed on the Reports page for its files alone. */
export const isClientTemplate = (t: Pick<ReportTemplate, 'kind'>): boolean => (t.kind ?? 'client') === 'client';
