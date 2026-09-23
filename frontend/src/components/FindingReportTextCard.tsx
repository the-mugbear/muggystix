/**
 * The finding's report text (v5.260.0) — what the client report says about
 * the issue: description, impact, recommendation, steps to reproduce,
 * references and CVSS.  Markdown, written by people: seeded once at promotion
 * from the scanner row or source note, then edited here by the finding's
 * author or a project admin (the server's `can_modify`, same as renaming).
 *
 * Shown as written (there is no Markdown renderer in the app); the Reports
 * page's preview is where it is seen rendered.
 */
import React, { useState } from 'react';
import { Loader2, Pencil } from 'lucide-react';

import {
  Finding,
  FindingReportText,
  FindingReportTextField,
  FindingReportTextUpdate,
  updateFinding,
} from '../services/api';
import { useToast } from '../contexts/ToastContext';
import { formatApiError } from '../utils/apiErrors';
import { Button } from './ui/button';
import { Card, CardContent, CardHeader, CardTitle } from './ui/card';
import { Input } from './ui/input';
import { Label } from './ui/label';
import { Textarea } from './ui/textarea';

export const REPORT_TEXT_FIELDS: Array<{ key: FindingReportTextField; label: string; hint: string; rows: number }> = [
  { key: 'description', label: 'Description', hint: 'What the issue is, in the client’s terms.', rows: 6 },
  { key: 'impact', label: 'Impact', hint: 'What an attacker gains, on this network.', rows: 3 },
  { key: 'recommendation', label: 'Recommendation', hint: 'What to change to fix it.', rows: 4 },
  { key: 'steps_to_reproduce', label: 'Steps to reproduce', hint: 'How it was demonstrated (a list works well).', rows: 5 },
  { key: 'references', label: 'References', hint: 'Advisories and vendor guidance, one per line.', rows: 3 },
];

type Draft = Record<FindingReportTextField | 'cvss_vector' | 'cvss_score', string>;

const toDraft = (t: FindingReportText | null | undefined): Draft => ({
  description: t?.description ?? '',
  impact: t?.impact ?? '',
  recommendation: t?.recommendation ?? '',
  steps_to_reproduce: t?.steps_to_reproduce ?? '',
  references: t?.references ?? '',
  cvss_vector: t?.cvss_vector ?? '',
  cvss_score: t?.cvss_score != null ? String(t.cvss_score) : '',
});

/** The fields a report would show empty — the Reports page counts the same. */
export const missingReportText = (t: FindingReportText | null | undefined): string[] =>
  REPORT_TEXT_FIELDS
    .filter((f) => f.key !== 'steps_to_reproduce' && f.key !== 'references')
    .filter((f) => !(t?.[f.key] ?? '').trim())
    .map((f) => f.label.toLowerCase());

interface Props {
  finding: Finding;
  /** Analyst+ AND the server's can_modify (author / project admin). */
  canEdit: boolean;
  onSaved: (finding: Finding) => void;
}

const FindingReportTextCard: React.FC<Props> = ({ finding, canEdit, onSaved }) => {
  const toast = useToast();
  const text = finding.report_text;
  const [draft, setDraft] = useState<Draft | null>(null);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const missing = missingReportText(text);

  const save = async () => {
    if (!draft) return;
    const before = toDraft(text);
    const payload: FindingReportTextUpdate = {};
    for (const f of REPORT_TEXT_FIELDS) {
      if (draft[f.key] !== before[f.key]) payload[f.key] = draft[f.key].trim() || null;
    }
    if (draft.cvss_vector !== before.cvss_vector) payload.cvss_vector = draft.cvss_vector.trim() || null;
    if (draft.cvss_score !== before.cvss_score) {
      const raw = draft.cvss_score.trim();
      const score = raw === '' ? null : Number(raw);
      if (score !== null && (Number.isNaN(score) || score < 0 || score > 10)) {
        setError('A CVSS score is a number from 0.0 to 10.0.');
        return;
      }
      payload.cvss_score = score;
    }
    if (Object.keys(payload).length === 0) { setDraft(null); return; }
    setSaving(true);
    setError(null);
    try {
      const updated = await updateFinding(finding.id, payload);
      onSaved(updated);
      setDraft(null);
      toast.success('Report text saved.');
    } catch (err) {
      setError(formatApiError(err, 'Could not save the report text.'));
    } finally {
      setSaving(false);
    }
  };

  const cvssLine = text?.cvss_vector || text?.cvss_score != null
    ? `${text?.cvss_score != null ? text.cvss_score.toFixed(1) : '—'}${text?.cvss_vector ? ` · ${text.cvss_vector}` : ''}`
    : null;

  return (
    <Card className="mb-md">
      <CardHeader>
        <div className="flex flex-wrap items-start justify-between gap-sm">
          <div className="min-w-0">
            <CardTitle>Report text</CardTitle>
            <p className="text-caption text-muted-foreground">
              What the client report says about this finding. Markdown.
              {missing.length > 0 && !draft && (
                <> Still empty: <span className="text-foreground">{missing.join(', ')}</span>.</>
              )}
            </p>
          </div>
          {canEdit && !draft && (
            <Button variant="ghost" size="sm" onClick={() => { setDraft(toDraft(text)); setError(null); }}>
              <Pencil className="size-4" aria-hidden /> Edit
            </Button>
          )}
        </div>
      </CardHeader>
      <CardContent>
        {draft ? (
          <form className="space-y-md" onSubmit={(e) => { e.preventDefault(); void save(); }}>
            {REPORT_TEXT_FIELDS.map((f) => (
              <div key={f.key} className="space-y-xxs">
                <Label htmlFor={`rt-${f.key}`}>{f.label}</Label>
                <p className="text-caption text-muted-foreground">{f.hint}</p>
                <Textarea
                  id={`rt-${f.key}`}
                  rows={f.rows}
                  maxLength={32768}
                  value={draft[f.key]}
                  onChange={(e) => setDraft({ ...draft, [f.key]: e.target.value })}
                  disabled={saving}
                />
              </div>
            ))}
            <div className="flex flex-wrap gap-md">
              <div className="min-w-0 flex-1 space-y-xxs">
                <Label htmlFor="rt-cvss-vector">CVSS vector</Label>
                <Input
                  id="rt-cvss-vector"
                  value={draft.cvss_vector}
                  maxLength={200}
                  placeholder="CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H"
                  onChange={(e) => setDraft({ ...draft, cvss_vector: e.target.value })}
                  disabled={saving}
                  className="font-mono"
                />
                <p className="text-caption text-muted-foreground">
                  A 3.x or 2.0 vector sets the score; for 4.0 enter the score yourself.
                </p>
              </div>
              <div className="w-32 space-y-xxs">
                <Label htmlFor="rt-cvss-score">Score</Label>
                <Input
                  id="rt-cvss-score"
                  inputMode="decimal"
                  value={draft.cvss_score}
                  onChange={(e) => setDraft({ ...draft, cvss_score: e.target.value })}
                  disabled={saving}
                />
              </div>
            </div>
            {error && <p className="text-caption text-destructive">{error}</p>}
            <div className="flex gap-xs">
              <Button type="submit" size="sm" disabled={saving}>
                {saving && <Loader2 className="size-4 animate-spin" aria-hidden />} Save
              </Button>
              <Button type="button" variant="ghost" size="sm" onClick={() => setDraft(null)} disabled={saving}>
                Cancel
              </Button>
            </div>
          </form>
        ) : (
          <dl className="space-y-sm">
            {REPORT_TEXT_FIELDS.map((f) => (
              <div key={f.key} className="min-w-0">
                <dt className="text-caption font-medium text-muted-foreground">{f.label}</dt>
                <dd className="whitespace-pre-wrap break-words text-body">
                  {text?.[f.key]?.trim() ? text[f.key] : <span className="text-muted-foreground">Not written yet</span>}
                </dd>
              </div>
            ))}
            <div className="min-w-0">
              <dt className="text-caption font-medium text-muted-foreground">CVSS</dt>
              <dd className="break-all font-mono text-body">
                {cvssLine ?? <span className="font-sans text-muted-foreground">Not scored</span>}
              </dd>
            </div>
          </dl>
        )}
      </CardContent>
    </Card>
  );
};

export default FindingReportTextCard;
