/**
 * System settings → Report defaults: who produces the installation's reports
 * and the disclaimer they close with.  A project's report defaults override
 * them and a report its project's (`services/report_defaults` on the server
 * is the one reader of that order); an empty disclaimer everywhere prints the
 * template's own.
 *
 * One record for the installation: every signed-in user reads it (a report's
 * page says what an empty field inherits), a global admin writes it — System
 * settings is theirs, as its neighbour `ReportWritingGuidanceSection` is.
 * Only the fields that changed are sent.
 */
import React, { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Loader2 } from 'lucide-react';

import { getReportDefaults, saveReportDefaults, type ReportDefaults } from '../../services/api';
import { useToast } from '../../contexts/ToastContext';
import { queryErrorText } from '../../lib/query';
import { formatApiError } from '../../utils/apiErrors';
import { formatTimestamp } from '../../utils/relativeTime';
import PostureSection from '../posture/PostureSection';
import { Button } from '../ui/button';
import { Input } from '../ui/input';
import { Label } from '../ui/label';
import MarkdownField from '../MarkdownField';

type Key = 'author_name' | 'disclaimer';
type Draft = Record<Key, string>;
const KEYS: Key[] = ['author_name', 'disclaimer'];

const draftOf = (defaults: ReportDefaults): Draft => ({
  author_name: defaults.values.author_name ?? '',
  disclaimer: defaults.values.disclaimer ?? '',
});

/** The keys whose field differs from what is stored, with what to send (a
 *  blank field removes the value). */
export const changedDefaults = (defaults: ReportDefaults, draft: Draft): Partial<Record<Key, string | null>> => {
  const stored = draftOf(defaults);
  return Object.fromEntries(
    KEYS.filter((k) => draft[k].trim() !== stored[k].trim()).map((k) => [k, draft[k].trim() || null]),
  );
};

const ReportDefaultsSection: React.FC = () => {
  const toast = useToast();
  const queryClient = useQueryClient();
  const query = useQuery({
    queryKey: ['getReportDefaults'],
    queryFn: ({ signal }) => getReportDefaults(signal),
  });
  const defaults = query.data ?? null;
  const error = queryErrorText(query.error, 'The report defaults could not be loaded.');

  // What the reader typed over what is stored: nothing is copied out of the
  // server's answer.
  const [edits, setEdits] = useState<Partial<Draft>>({});
  const draft: Draft | null = defaults ? { ...draftOf(defaults), ...edits } : null;
  const changed = defaults && draft ? changedDefaults(defaults, draft) : {};
  const dirty = Object.keys(changed).length > 0;

  const saving = useMutation({
    mutationFn: (values: Partial<Record<Key, string | null>>) => saveReportDefaults(values),
    onSuccess: (next) => {
      queryClient.setQueryData(['getReportDefaults'], next);
      setEdits({});
      toast.success('Report defaults saved. Reports that write none of their own use them from the next preview.');
    },
    onError: (err) => toast.error(formatApiError(err, 'Could not save the report defaults.')),
  });
  const busy = saving.isPending;
  const save = () => { if (dirty) saving.mutate(changed); };

  return (
    <PostureSection
      title="Report defaults"
      description="These apply to every project's reports unless a project or a report writes its own; an empty disclaimer prints the template's own."
    >
      {error && (
        <p role="alert" className="text-caption text-destructive">
          {error} <button type="button" className="text-info hover:underline" onClick={() => void query.refetch()}>Retry</button>
        </p>
      )}
      {!defaults && !error && <p className="text-caption text-muted-foreground">Loading…</p>}
      {defaults && draft && (
        <form className="max-w-4xl space-y-sm" onSubmit={(e) => { e.preventDefault(); save(); }}>
          <div className="min-w-0 max-w-xl space-y-xxs">
            <Label htmlFor="ss-report-author">{defaults.labels.author_name ?? 'Author'}</Label>
            <Input id="ss-report-author" value={draft.author_name} disabled={busy}
              maxLength={defaults.max_chars.author_name ?? 255} placeholder="e.g. Example Security Ltd"
              title="Who produced the report"
              onChange={(e) => setEdits({ ...edits, author_name: e.target.value })} />
          </div>
          <div className="min-w-0 space-y-xxs">
            <Label htmlFor="ss-report-disclaimer">{defaults.labels.disclaimer ?? 'Disclaimer'}</Label>
            <MarkdownField id="ss-report-disclaimer" label={defaults.labels.disclaimer ?? 'Disclaimer'} rows={6}
              maxLength={defaults.max_chars.disclaimer ?? 32768} value={draft.disclaimer} disabled={busy}
              onChange={(v) => setEdits({ ...edits, disclaimer: v })} />
          </div>
          {defaults.updated_at && (
            <p className="text-caption text-muted-foreground">
              Last changed {formatTimestamp(defaults.updated_at)}{defaults.updated_by ? ` by ${defaults.updated_by}` : ''}.
            </p>
          )}
          {dirty && (
            <div className="flex items-center gap-xs">
              <Button type="submit" size="sm" disabled={busy}>
                {busy && <Loader2 className="size-4 animate-spin" aria-hidden />} Save report defaults
              </Button>
              <Button type="button" size="sm" variant="ghost" disabled={busy} onClick={() => setEdits({})}>
                Undo changes
              </Button>
            </div>
          )}
        </form>
      )}
    </PostureSection>
  );
};

export default ReportDefaultsSection;
