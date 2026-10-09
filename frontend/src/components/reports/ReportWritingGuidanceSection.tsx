/**
 * System settings → Report writing guidance (5.350.0): what a model is told
 * when it drafts a finding's report text — the in-app AI draft and an agent's
 * proposal read the same record.
 *
 * One box for every section and one per section.  A box left on its shipped
 * default says so; an edited one offers the default back.  The rules that
 * always apply are the server's and are shown, not edited.  Only the boxes
 * that changed are sent.
 */
import React, { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Loader2 } from 'lucide-react';

import {
  getReportWritingGuidance, updateReportWritingGuidance, type ReportWritingGuidance,
} from '../../services/api';
import { useToast } from '../../contexts/ToastContext';
import { GLOBAL, queryErrorText } from '../../lib/query';
import { formatApiError } from '../../utils/apiErrors';
import { formatTimestamp } from '../../utils/relativeTime';
import PostureSection from '../posture/PostureSection';
import { Button } from '../ui/button';
import { CharacterCount } from '../ui/character-count';
import { Label } from '../ui/label';
import { Textarea } from '../ui/textarea';

type Draft = Record<string, string>;

const draftOf = (guidance: ReportWritingGuidance): Draft =>
  Object.fromEntries(guidance.sections.map((s) => [s.key, s.text]));

/** The keys whose box differs from what is stored, with the text to send. */
export const changedSections = (guidance: ReportWritingGuidance, draft: Draft): Record<string, string> =>
  Object.fromEntries(
    guidance.sections
      .filter((s) => (draft[s.key] ?? '').trim() !== s.text)
      .map((s) => [s.key, (draft[s.key] ?? '').trim()]),
  );

export const ReportWritingGuidanceSection: React.FC = () => {
  const toast = useToast();
  const queryClient = useQueryClient();
  const query = useQuery({
    queryKey: [GLOBAL, 'getReportWritingGuidance'],
    queryFn: ({ signal }) => getReportWritingGuidance(signal),
  });
  const guidance = query.data ?? null;
  const error = queryErrorText(query.error, 'The report writing guidance could not be loaded.');
  const load = () => { void query.refetch(); };

  // What the reader typed, per box, over what is stored: nothing is copied
  // out of the server's answer, so a save or a reload cannot leave the two
  // out of step.
  const [edits, setEdits] = useState<Draft>({});
  const draft: Draft | null = guidance ? { ...draftOf(guidance), ...edits } : null;
  const setDraft = (next: Draft) => setEdits(next);

  const changed = guidance && draft ? changedSections(guidance, draft) : {};
  const dirty = Object.keys(changed).length > 0;

  const saving = useMutation({
    mutationFn: (sections: Record<string, string>) => updateReportWritingGuidance(sections),
    onSuccess: (next) => {
      queryClient.setQueryData([GLOBAL, 'getReportWritingGuidance'], next);
      setEdits({});
      toast.success('Writing guidance saved. The next draft is written to it.');
    },
    onError: (err) => toast.error(formatApiError(err, 'Could not save the writing guidance.')),
  });
  const busy = saving.isPending;
  const save = () => {
    if (!guidance || !draft || !dirty) return;
    saving.mutate(changed);
  };

  return (
    <PostureSection
      title="Report writing guidance"
      description="What a model is told when it drafts a finding's report text: the in-app AI draft and agents' proposals alike."
    >
      {error && (
        <p role="alert" className="text-caption text-destructive">
          {error} <button type="button" className="text-info hover:underline" onClick={load}>Retry</button>
        </p>
      )}
      {!guidance && !error && <p className="text-caption text-muted-foreground">Loading…</p>}
      {guidance && draft && (
        <form className="max-w-4xl" onSubmit={(e) => { e.preventDefault(); save(); }}>
          <div className="flex flex-col gap-sm">
            {guidance.sections.map((section) => {
              const id = `ss-guidance-${section.key}`;
              const value = draft[section.key] ?? '';
              const onDefault = value.trim() === section.default || value.trim() === '';
              return (
                <div key={section.key} className="min-w-0">
                  <div className="flex items-baseline justify-between gap-sm">
                    <Label htmlFor={id}>{section.label}</Label>
                    {onDefault ? (
                      <span className="text-caption text-muted-foreground">Default</span>
                    ) : (
                      <button type="button" className="text-caption text-info hover:underline" disabled={busy}
                        onClick={() => setDraft({ ...draft, [section.key]: section.default })}>
                        Reset to default
                      </button>
                    )}
                  </div>
                  <Textarea id={id} value={value} rows={section.key === 'general' ? 4 : 3}
                    maxLength={guidance.max_chars} placeholder={section.default}
                    aria-describedby={`${id}-count`} className="break-words"
                    onChange={(e) => setDraft({ ...draft, [section.key]: e.target.value })} />
                  <CharacterCount id={`${id}-count`} value={value} max={guidance.max_chars} />
                </div>
              );
            })}
          </div>
          <p className="mt-xs text-caption text-muted-foreground">
            An emptied box goes back to its default.
            {guidance.updated_at && (
              <> Last changed {formatTimestamp(guidance.updated_at)}{guidance.updated_by ? ` by ${guidance.updated_by}` : ''}.</>
            )}
          </p>
          {dirty && (
            <div className="mt-sm flex items-center gap-xs">
              <Button type="submit" size="sm" disabled={busy}>
                {busy && <Loader2 className="size-4 animate-spin" aria-hidden />} Save guidance
              </Button>
              <Button type="button" size="sm" variant="ghost" disabled={busy}
                onClick={() => setEdits({})}>Discard</Button>
            </div>
          )}

          <details className="mt-md">
            <summary className="cursor-pointer text-metadata font-medium">Rules that always apply</summary>
            <p className="mt-xs text-caption text-muted-foreground">
              Not editable: they come after the guidance and take precedence over it.
            </p>
            <ul className="mt-xs list-disc pl-md text-caption text-muted-foreground">
              {guidance.fixed_rules.map((rule) => <li key={rule} className="break-words">{rule}</li>)}
            </ul>
          </details>
          <details className="mt-xs">
            <summary className="cursor-pointer text-metadata font-medium">The prompt the in-app draft sends, as saved</summary>
            <pre data-testid="ss-guidance-prompt"
              className="mt-xs max-h-96 overflow-auto whitespace-pre-wrap break-words rounded-control border border-border p-sm text-caption">
              {guidance.drafter_prompt}
            </pre>
          </details>
        </form>
      )}
    </PostureSection>
  );
};

export default ReportWritingGuidanceSection;
