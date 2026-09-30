/**
 * Agent evidence on this host (v5.316.0): what an agent ran against it and
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

const HostEvidenceSection: React.FC<{ hostId: number }> = ({ hostId }) => {
  const [items, setItems] = useState<EvidenceRecord[] | null>(null);
  const [total, setTotal] = useState(0);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const res = await listEvidenceRecords({ host_id: hostId, limit: 100 });
      setItems(res.items);
      setTotal(res.total);
      setError(null);
    } catch (err) {
      setError(formatApiError(err, 'Evidence records unavailable.'));
    }
  }, [hostId]);

  useEffect(() => { void load(); }, [load]);

  if (!error && (!items || items.length === 0)) return null;
  return (
    <div id="evidence">
      <InspectorSection
        id="host-detail-evidence"
        title="Agent evidence"
        titleHint="Commands an agent ran against this host and what came back — recorded as they happened, never changed."
        icon={<FileTerminal className="size-4 shrink-0 text-primary" aria-hidden />}
        count={total}
      >
        {error ? <p className="text-caption text-destructive">{error}</p> : (
          <ul className="space-y-sm">
            {(items ?? []).map((rec) => {
              const outcome = OUTCOME[rec.outcome] ?? { label: rec.outcome, variant: 'muted' as const };
              return (
                <li key={rec.id} className="min-w-0 space-y-xxs border-b border-border pb-sm last:border-b-0" data-evidence={rec.id}>
                  <div className="flex min-w-0 flex-wrap items-baseline gap-x-sm gap-y-xxs">
                    <span className="font-medium">#{rec.id} · {rec.tool}</span>
                    <Badge variant={outcome.variant} className="shrink-0">{outcome.label}</Badge>
                    <span className="min-w-0 text-caption text-muted-foreground">
                      {formatRelativeTime(rec.executed_at ?? rec.created_at, { fallback: '' })}
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
                  {rec.finding_id != null && (
                    <p className="text-caption"><Link to={`/findings/${rec.finding_id}`} className="text-info hover:underline">Finding #{rec.finding_id}</Link></p>
                  )}
                  <Output rec={rec} />
                </li>
              );
            })}
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
