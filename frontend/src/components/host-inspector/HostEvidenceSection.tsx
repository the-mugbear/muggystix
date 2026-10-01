/**
 * Evidence on this host (v5.316.0; a person's test results too since
 * 5.321.0): what an agent or an analyst ran against it and
 * what came back — tool, command, outcome, summary, and the raw output (a
 * preview, the whole of it on request).  Recorded directly and never changed:
 * the audit trail behind a proposal that cites it.  Renders nothing when the
 * host has none.
 */
import React, { useCallback, useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { FileTerminal, Loader2 } from 'lucide-react';

import { EvidenceRecord, getEvidenceRawOutput, listEvidenceRecords } from '../../services/api';
import { formatApiError } from '../../utils/apiErrors';
import { formatRelativeTime } from '../../utils/relativeTime';
import { Badge } from '../ui/badge';
import { Button } from '../ui/button';
import { InspectorSection } from './InspectorSection';

const OUTCOME: Record<string, { label: string; variant: 'warning-outline' | 'success' | 'muted' | 'destructive-outline' | 'outline' }> = {
  finding: { label: 'issue shown', variant: 'warning-outline' },
  no_finding: { label: 'not present', variant: 'outline' },
  inconclusive: { label: 'inconclusive', variant: 'muted' },
  failed: { label: 'could not run', variant: 'destructive-outline' },
  info: { label: 'context', variant: 'muted' },
};

const Output: React.FC<{ rec: EvidenceRecord }> = ({ rec }) => {
  const [full, setFull] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  if (!rec.raw_output_preview) return null;
  const loadAll = async () => {
    setBusy(true);
    try {
      setFull(await getEvidenceRawOutput(rec.id));
      setError(null);
    } catch (err) {
      setError(formatApiError(err, 'Could not load the output.'));
    } finally {
      setBusy(false);
    }
  };
  return (
    <details className="text-caption">
      <summary className="cursor-pointer select-none text-muted-foreground">
        Output{rec.raw_output_bytes ? ` · ${Math.max(1, Math.round(rec.raw_output_bytes / 1024))} KB` : ''}
      </summary>
      <pre className="mt-xxs max-h-80 overflow-auto whitespace-pre-wrap break-all rounded bg-muted p-xs font-mono text-caption">
        {full ?? rec.raw_output_preview}
      </pre>
      {full === null && rec.raw_output_truncated_in_preview && (
        <Button variant="ghost" size="sm" className="mt-xxs" onClick={() => void loadAll()} disabled={busy}>
          {busy && <Loader2 className="size-4 animate-spin" aria-hidden />} Show the whole output
        </Button>
      )}
      {error && <p className="text-destructive">{error}</p>}
    </details>
  );
};

/** One evidence record: tool, outcome, when and who, the summary, the command
 *  and its output. Shared with the Tests section (5.320.0), which lists the
 *  records that answer one test — so a record reads the same in both. */
export const EvidenceItem: React.FC<{ rec: EvidenceRecord; hideFindingLink?: boolean }> = ({ rec, hideFindingLink }) => {
  const outcome = OUTCOME[rec.outcome] ?? { label: rec.outcome, variant: 'muted' as const };
  return (
    <>
      <div className="flex min-w-0 flex-wrap items-baseline gap-x-sm gap-y-xxs">
        <span className="font-medium">#{rec.id} · {rec.tool}</span>
        <Badge variant={outcome.variant} className="shrink-0">{outcome.label}</Badge>
        <span className="min-w-0 text-caption text-muted-foreground">
          {formatRelativeTime(rec.executed_at ?? rec.created_at, { fallback: '' })}
          {rec.observed_ip ? ` · reached ${rec.observed_ip}` : ''}
          {rec.agent_session_id != null && (
            <> · <Link to={`/agent-sessions/${rec.agent_session_id}`} className="text-info hover:underline">session #{rec.agent_session_id}</Link></>
          )}
          {rec.agent_model ? ` · ${rec.agent_model}` : ''}
          {rec.recorded_by ? ` · ${rec.recorded_by}` : ''}
        </span>
      </div>
      <p className="break-words text-body">{rec.summary}</p>
      {rec.command && (
        // Up to 10,000 characters: clamped (UI style guide —
        // command lines truncate); the title holds the whole.
        <p className="line-clamp-3 break-all font-mono text-caption text-muted-foreground" title={rec.command}>{rec.command}</p>
      )}
      {rec.finding_id != null && !hideFindingLink && (
        <p className="text-caption"><Link to={`/findings/${rec.finding_id}`} className="text-info hover:underline">Finding #{rec.finding_id}</Link></p>
      )}
      <Output rec={rec} />
    </>
  );
};

const HostEvidenceSection: React.FC<{ hostId: number; refreshKey?: number }> = ({ hostId, refreshKey = 0 }) => {
  const [items, setItems] = useState<EvidenceRecord[] | null>(null);
  const [total, setTotal] = useState(0);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const res = await listEvidenceRecords({ host_id: hostId, unlinked: true, limit: 100 });
      setItems(res.items);
      setTotal(res.total);
      setError(null);
    } catch (err) {
      setError(formatApiError(err, 'Evidence records unavailable.'));
    }
  }, [hostId]);

  // `refreshKey` changes when a result or a finding was recorded on this host.
  useEffect(() => { void load(); }, [load, refreshKey]);

  if (!error && (!items || items.length === 0)) return null;
  return (
    <div id="evidence">
      <InspectorSection
        id="host-detail-evidence"
        title="Other evidence"
        titleHint="Commands recorded against this host that answer no test, and what came back. Recorded as they happened, never changed. A test's results are listed under that test."
        icon={<FileTerminal className="size-4 shrink-0 text-primary" aria-hidden />}
        count={total}
      >
        {error ? <p className="text-caption text-destructive">{error}</p> : (
          <ul className="space-y-sm">
            {(items ?? []).map((rec) => (
              <li key={rec.id} className="min-w-0 space-y-xxs border-b border-border pb-sm last:border-b-0" data-evidence={rec.id}>
                <EvidenceItem rec={rec} />
              </li>
            ))}
          </ul>
        )}
        {items && total > items.length && (
          <p className="mt-xs text-caption text-muted-foreground">Showing the newest {items.length} of {total}.</p>
        )}
      </InspectorSection>
    </div>
  );
};

export default HostEvidenceSection;
