/**
 * What one edit on a finding's history changed: the field's value before and
 * after (5.413.0; owner, 2026-10-21 — any analyst may edit a finding, so each
 * edit is recorded and can be read back).  Read when the reader opens it: a
 * report-text section can be 32 KB, so the history list carries only the
 * field's name.  A section is compared like a proposal (side by side, or the
 * words added and removed); a title, severity, owner or score is two lines.
 */
import React, { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { ChevronDown, ChevronRight, Loader2 } from 'lucide-react';

import { getFindingEdit } from '../../services/api';
import { queryErrorText } from '../../lib/query';
import SafeMarkdown from '../SafeMarkdown';
import { Button } from '../ui/button';
import TextComparison from '../proposals/TextComparison';

/** Shown as plain text, not Markdown. */
const SHORT_FIELDS = new Set(['title', 'severity', 'owner', 'cvss_score']);

const EDIT_LABELS = { current: 'Before', proposed: 'After', empty: 'Empty before this edit.' };

interface Props {
  projectId: number;
  findingId: number;
  entryId: number;
  field: string;
}

const FindingEditChange: React.FC<Props> = ({ projectId, findingId, entryId, field }) => {
  // A recorded edit never changes: read once.
  const query = useQuery({
    queryKey: ['getFindingEdit', projectId, findingId, entryId],
    queryFn: ({ signal }) => getFindingEdit(projectId, findingId, entryId, signal),
    staleTime: Infinity,
  });
  if (query.isPending) {
    return (
      <div className="flex items-center gap-xs text-caption text-muted-foreground" role="status">
        <Loader2 className="size-4 animate-spin" aria-hidden /> Loading the change…
      </div>
    );
  }
  if (query.isError) {
    return <p className="text-caption text-destructive">{queryErrorText(query.error, 'Could not load the change.')}</p>;
  }
  const { before, after } = query.data;

  if (SHORT_FIELDS.has(field)) {
    return (
      <dl className="grid min-w-0 grid-cols-[auto_1fr] gap-x-sm gap-y-xxs text-metadata" data-testid="edit-change">
        <dt className="text-muted-foreground">Before</dt>
        <dd className="min-w-0 break-words">{before ?? <span className="text-muted-foreground">none</span>}</dd>
        <dt className="text-muted-foreground">After</dt>
        <dd className="min-w-0 break-words">{after ?? <span className="text-muted-foreground">none</span>}</dd>
      </dl>
    );
  }
  if (after === null) {
    // Cleared: there is nothing to compare against, so the text that was removed.
    return (
      <div className="min-w-0 space-y-xxs" data-testid="edit-change">
        <p className="text-caption text-muted-foreground">This edit emptied the section. It held:</p>
        <div className="border-l-2 border-border pl-sm">
          {field === 'cvss_vector'
            ? <p className="break-all font-mono text-body">{before}</p>
            : <SafeMarkdown text={before ?? ''} className="text-body" />}
        </div>
      </div>
    );
  }
  return (
    <div className="min-w-0" data-testid="edit-change">
      <TextComparison current={before} proposed={after} mono={field === 'cvss_vector'} labels={EDIT_LABELS} />
    </div>
  );
};

/** "Show before and after" under an edit's line; the change is read when opened. */
export const FindingEditDisclosure: React.FC<Props> = (props) => {
  const [open, setOpen] = useState(false);
  return (
    <div className="mt-xxs min-w-0 space-y-xs">
      <Button variant="ghost" size="sm" aria-expanded={open} onClick={() => setOpen((v) => !v)}>
        {open ? <ChevronDown className="size-4" aria-hidden /> : <ChevronRight className="size-4" aria-hidden />}
        {open ? 'Hide before and after' : 'Show before and after'}
      </Button>
      {open && <FindingEditChange {...props} />}
    </div>
  );
};

export default FindingEditChange;
