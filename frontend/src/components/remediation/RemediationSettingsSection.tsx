/**
 * System settings → Remediation tracking (5.340.0): the installation's one
 * switch and its timeline per severity.
 *
 * Off is the default: a team that uses BlueStick to write reports never sees
 * anything about remediation.  On, findings can be assigned to the people who
 * must fix them, each with a deadline that follows from its severity, and
 * admins are told when one is close to or past it.
 *
 * The switch applies at once (like a preference); the days and the time zone
 * are a form with its own Save, because changing them moves every open
 * deadline.  The zone decides which calendar day is "today" on the server —
 * this page never works a state out itself.
 */
import React, { useEffect, useState } from 'react';
import { Loader2 } from 'lucide-react';

import { getRemediationPolicy, updateRemediationPolicy, type RemediationPolicy } from '../../services/api';
import { useToast } from '../../contexts/ToastContext';
import { setRemediationPolicy } from '../../hooks/useRemediationPolicy';
import { formatApiError } from '../../utils/apiErrors';
import { severityWord } from '../../utils/remediation';
import PostureSection from '../posture/PostureSection';
import { Button } from '../ui/button';
import { Input } from '../ui/input';
import { Label } from '../ui/label';
import { Switch } from '../ui/switch';

const SEVERITIES = ['critical', 'high', 'medium', 'low', 'info'];
const MAX_DAYS = 3650;

type Draft = Record<string, string>;

const zoneOf = (policy: RemediationPolicy): string => policy.time_zone || 'UTC';

const draftOf = (policy: RemediationPolicy): Draft => ({
  ...Object.fromEntries(SEVERITIES.map((s) => [s, policy.days[s] == null ? '' : String(policy.days[s])])),
  due_soon: String(policy.due_soon_days),
  time_zone: zoneOf(policy),
});

/** Why a time zone cannot be saved, or null.  A first check only: the server
 *  decides which names it knows. */
export const zoneProblem = (raw: string | undefined): string | null => {
  const name = (raw ?? '').trim();
  const problem = 'Time zone: an IANA name such as UTC, Europe/Paris or America/Los_Angeles.';
  if (name === '' || name.length > 64 || !/^[A-Za-z0-9_+\-/]+$/.test(name)) return problem;
  try {
    new Intl.DateTimeFormat('en', { timeZone: name });
    return null;
  } catch {
    return problem;
  }
};

/** Why a field cannot be saved, or null.  Empty days = no deadline. */
export const daysProblem = (draft: Draft): string | null => {
  for (const severity of SEVERITIES) {
    const raw = draft[severity].trim();
    if (raw === '') continue;
    const n = Number(raw);
    if (!Number.isInteger(n) || n < 1 || n > MAX_DAYS) {
      return `${severityWord(severity)}: a whole number of days from 1 to ${MAX_DAYS.toLocaleString()}, or empty for no deadline.`;
    }
  }
  const soon = Number(draft.due_soon.trim());
  if (draft.due_soon.trim() === '' || !Number.isInteger(soon) || soon < 0 || soon > 365) {
    return 'Warning window: a whole number of days from 0 to 365.';
  }
  return null;
};

export const RemediationSettingsSection: React.FC = () => {
  const toast = useToast();
  const [policy, setPolicy] = useState<RemediationPolicy | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [draft, setDraft] = useState<Draft | null>(null);
  const [busy, setBusy] = useState<'switch' | 'days' | null>(null);

  const load = () => {
    setError(null);
    getRemediationPolicy()
      .then((p) => { setPolicy(p); setDraft(draftOf(p)); })
      .catch((err) => setError(formatApiError(err, 'The remediation settings could not be loaded.')));
  };
  useEffect(load, []);

  const saved = (next: RemediationPolicy) => {
    setPolicy(next);
    setDraft(draftOf(next));
    setRemediationPolicy(next);          // the navigation follows at once
  };

  const toggle = async (on: boolean) => {
    setBusy('switch');
    try {
      saved(await updateRemediationPolicy({ enabled: on }));
      toast.success(on
        ? 'Remediation tracking is on. Projects now have a Remediation page.'
        : 'Remediation tracking is off. What was recorded is kept.');
    } catch (err) {
      toast.error(formatApiError(err, 'Could not change the setting.'));
    } finally {
      setBusy(null);
    }
  };

  const problem = draft ? (daysProblem(draft) ?? zoneProblem(draft.time_zone)) : null;
  const dirty = !!policy && !!draft && JSON.stringify(draft) !== JSON.stringify(draftOf(policy));

  const saveDays = async () => {
    if (!draft || !policy || problem) return;
    setBusy('days');
    try {
      const zone = draft.time_zone.trim();
      saved(await updateRemediationPolicy({
        days: Object.fromEntries(SEVERITIES.map((s) => [s, draft[s].trim() === '' ? null : Number(draft[s])])),
        due_soon_days: Number(draft.due_soon),
        // Sent only when it changed: the days are one form, the zone moves
        // every state at once and should not ride along unnoticed.
        ...(zone !== zoneOf(policy) ? { time_zone: zone } : {}),
      }));
      toast.success('Timelines saved. Open deadlines now follow them.');
    } catch (err) {
      toast.error(formatApiError(err, 'Could not save the timelines.'));
    } finally {
      setBusy(null);
    }
  };

  return (
    <PostureSection
      title="Remediation tracking"
      description="For installations that follow findings to their fix. Off, nothing about remediation appears anywhere."
    >
      {error && (
        <p role="alert" className="text-caption text-destructive">
          {error} <button type="button" className="text-info hover:underline" onClick={load}>Retry</button>
        </p>
      )}
      {!policy && !error && <p className="text-caption text-muted-foreground">Loading…</p>}
      {policy && draft && (
        <div className="max-w-4xl">
          <div className="flex items-start gap-sm">
            <Switch id="ss-remediation" checked={policy.enabled} disabled={busy !== null}
              onCheckedChange={(v) => void toggle(v)} aria-describedby="ss-remediation-hint" />
            <div className="min-w-0">
              <Label htmlFor="ss-remediation">Track remediation on this installation</Label>
              <p id="ss-remediation-hint" className="text-caption text-muted-foreground">
                {policy.enabled
                  ? 'On. Project admins assign findings on hosts to the people who fix them; admins are told when a deadline is close or has passed.'
                  : 'Off. Turn it on to assign findings to remediators with a deadline by severity, and to follow up on the ones at risk.'}
              </p>
            </div>
          </div>

          <form className="mt-md" onSubmit={(e) => { e.preventDefault(); void saveDays(); }}>
            <p className="text-metadata font-medium">Days to remediate, from the day a finding is assigned</p>
            <div className="mt-xs grid grid-cols-2 gap-sm sm:grid-cols-3 lg:grid-cols-6">
              {SEVERITIES.map((severity) => (
                <div key={severity} className="min-w-0">
                  <Label htmlFor={`ss-days-${severity}`}>{severityWord(severity)}</Label>
                  <Input id={`ss-days-${severity}`} inputMode="numeric" value={draft[severity]}
                    placeholder="No deadline" maxLength={4}
                    onChange={(e) => setDraft({ ...draft, [severity]: e.target.value })} />
                </div>
              ))}
              <div className="min-w-0">
                {/* Short enough for one line: a wrapped label pushed this input
                    below its neighbours (seen in the browser). */}
                <Label htmlFor="ss-days-soon" title="How many days before a deadline a finding counts as due soon">Warn before</Label>
                <Input id="ss-days-soon" inputMode="numeric" value={draft.due_soon} maxLength={3}
                  onChange={(e) => setDraft({ ...draft, due_soon: e.target.value })} />
              </div>
            </div>
            <p className="mt-xs text-caption text-muted-foreground">
              All in days. Empty means no deadline for that severity; “Warn before” is how close to its deadline a finding counts as due soon. A change applies to every finding still open; one already closed keeps the deadline it was closed against.
            </p>
            <div className="mt-sm max-w-xs min-w-0">
              <Label htmlFor="ss-remediation-zone"
                title="The time zone whose calendar day is today for every deadline">Time zone</Label>
              <Input id="ss-remediation-zone" value={draft.time_zone} maxLength={64} spellCheck={false}
                autoComplete="off" placeholder="UTC" aria-describedby="ss-remediation-zone-hint"
                onChange={(e) => setDraft({ ...draft, time_zone: e.target.value })} />
              <p id="ss-remediation-zone-hint" className="mt-xs text-caption text-muted-foreground">
                An IANA name, such as Europe/Paris. A deadline passes when the day ends there.
              </p>
            </div>
            {problem && dirty && <p role="alert" className="mt-xs text-caption text-destructive">{problem}</p>}
            {dirty && (
              <div className="mt-sm flex items-center gap-xs">
                <Button type="submit" size="sm" disabled={busy !== null || !!problem}>
                  {busy === 'days' && <Loader2 className="size-4 animate-spin" aria-hidden />} Save timelines
                </Button>
                <Button type="button" size="sm" variant="ghost" disabled={busy !== null}
                  onClick={() => setDraft(draftOf(policy))}>Discard</Button>
              </div>
            )}
          </form>
        </div>
      )}
    </PostureSection>
  );
};

export default RemediationSettingsSection;
