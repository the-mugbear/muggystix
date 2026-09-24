/**
 * The finding's report text (v5.260.0) — what the client report says about
 * the issue: description, impact, recommendation, steps to reproduce,
 * references and CVSS.  Markdown, written by people: seeded once at promotion
 * from the scanner row or source note, then edited here by the finding's
 * author or a project admin (the server's `can_modify`, same as renaming).
 *
 * v5.290.0 — shown rendered, under the report's own rules (SafeMarkdown: no
 * raw HTML, no images, web/mail links only, headings as bold text); before,
 * `**bold**` printed literally.
 *
 * 5.293.0 — each field is a MarkdownField: a formatting toolbar, a preview
 * under the same rules (tables included), a guide to what the report prints,
 * and a fix for a table written straight after text.
 */
import React, { useEffect, useRef, useState } from 'react';
import { Loader2, Pencil, Sparkles } from 'lucide-react';

import {
  draftFindingText,
  Finding,
  FindingReportText,
  FindingReportTextField,
  FindingReportTextUpdate,
  updateFinding,
} from '../services/api';
import { useToast } from '../contexts/ToastContext';
import { formatApiError } from '../utils/apiErrors';
import { Button } from './ui/button';
import PostureSection from './posture/PostureSection';
import { Input } from './ui/input';
import { Label } from './ui/label';
import MarkdownField from './MarkdownField';
import SafeMarkdown from './SafeMarkdown';

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

/** The sections a report needs — the ones an AI draft may fill. */
const DRAFTABLE: FindingReportTextField[] = ['description', 'impact', 'recommendation'];

interface Props {
  finding: Finding;
  /** Analyst+ AND the server's can_modify (author / project admin). */
  canEdit: boolean;
  onSaved: (finding: Finding) => void;
  /** Open in the editor (the Reports page's "missing report text" links). */
  startEditing?: boolean;
}

const FindingReportTextCard: React.FC<Props> = ({ finding, canEdit, onSaved, startEditing = false }) => {
  const toast = useToast();
  const text = finding.report_text;
  const [draft, setDraft] = useState<Draft | null>(() => (startEditing && canEdit ? toDraft(text) : null));
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // Sections filled by an AI suggestion and not yet saved — marked until then.
  const [aiFilled, setAiFilled] = useState<Set<FindingReportTextField>>(new Set());
  const [drafting, setDrafting] = useState(false);
  const cardRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (startEditing) cardRef.current?.scrollIntoView?.({ block: 'start' });
  }, [startEditing]);

  const missing = missingReportText(text);

  // Suggest text for the required sections still empty in the editor.  The
  // suggestion only fills those boxes; nothing is saved until Save.
  const draftEmpty = async (base: Draft) => {
    const empty = DRAFTABLE.filter((k) => !base[k].trim());
    if (empty.length === 0) return;
    setDrafting(true);
    setError(null);
    try {
      const { suggestions } = await draftFindingText(finding.id, empty);
      const filled = empty.filter((k) => suggestions[k]);
      setDraft((current) => {
        const next = { ...(current ?? base) };
        for (const k of filled) if (!next[k].trim()) next[k] = suggestions[k] as string;
        return next;
      });
      setAiFilled((prev) => new Set([...prev, ...filled]));
    } catch (err) {
      setError(formatApiError(err, 'Could not draft the report text.'));
    } finally {
      setDrafting(false);
    }
  };
  const startDraft = () => {
    const base = draft ?? toDraft(text);
    if (!draft) setDraft(base);
    void draftEmpty(base);
  };
  const emptyInEditor = draft ? DRAFTABLE.filter((k) => !draft[k].trim()).length : 0;

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
      setAiFilled(new Set());
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
    // v5.294.0 (UX review) — a section over a thin rule, not a bordered card
    // (UI_STYLE_GUIDE §7). The wrapper keeps the ref the ?edit= link scrolls to.
    <div className="mb-md" ref={cardRef}>
      <PostureSection
        title={<span>Report text</span>}
        description={<>
          What the client report says about this finding. Written in Markdown; shown as the report prints it.
          {missing.length > 0 && !draft && (
            <> Still empty: <span className="text-foreground">{missing.join(', ')}</span>.</>
          )}
        </>}
        actions={canEdit ? (
          <>
            {((!draft && missing.length > 0) || emptyInEditor > 0) && (
              <Button variant="ghost" size="sm" onClick={startDraft} disabled={drafting || saving}
                title="Suggest text for the empty sections with your LLM provider; nothing is saved until you save">
                {drafting ? <Loader2 className="size-4 animate-spin" aria-hidden /> : <Sparkles className="size-4" aria-hidden />}
                Draft empty sections
              </Button>
            )}
            {!draft && (
              <Button variant="ghost" size="sm" onClick={() => { setDraft(toDraft(text)); setError(null); }}>
                <Pencil className="size-4" aria-hidden /> Edit
              </Button>
            )}
          </>
        ) : undefined}
      >
        {draft ? (
          <form className="space-y-md" onSubmit={(e) => { e.preventDefault(); void save(); }}>
            {REPORT_TEXT_FIELDS.map((f) => (
              <div key={f.key} className="space-y-xxs">
                <Label htmlFor={`rt-${f.key}`}>{f.label}</Label>
                <p className="text-caption text-muted-foreground">{f.hint}</p>
                {aiFilled.has(f.key) && (
                  <p className="text-caption text-warning">
                    Drafted by AI from this finding&apos;s data — check every statement before you save.
                  </p>
                )}
                <MarkdownField
                  id={`rt-${f.key}`}
                  label={f.label}
                  rows={f.rows}
                  maxLength={32768}
                  value={draft[f.key]}
                  onChange={(v) => setDraft((d) => (d ? { ...d, [f.key]: v } : d))}
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
              <Button type="button" variant="ghost" size="sm" onClick={() => { setDraft(null); setAiFilled(new Set()); }} disabled={saving}>
                Cancel
              </Button>
            </div>
          </form>
        ) : (
          <dl className="space-y-sm">
            {REPORT_TEXT_FIELDS.map((f) => (
              <div key={f.key} className="min-w-0">
                <dt className="text-caption font-medium text-muted-foreground">{f.label}</dt>
                <dd className="min-w-0 break-words text-body" data-testid={`report-text-${f.key}`}>
                  {text?.[f.key]?.trim()
                    ? <SafeMarkdown text={text[f.key] as string} />
                    : <span className="text-muted-foreground">Not written yet</span>}
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
      </PostureSection>
    </div>
  );
};

export default FindingReportTextCard;
