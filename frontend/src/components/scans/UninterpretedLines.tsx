/**
 * The lines an import's parser did not interpret (v5.301.0), as REDACTED
 * shapes: each line's structure with addresses, names, credentials and values
 * replaced (<IP>, <HOST>, <VALUE>…), grouped and counted.  They are what a
 * parser fix needs when the file itself cannot leave the network — copy them
 * out and they carry no client data the redaction knew about.
 */
import React, { useState } from 'react';
import { Link } from 'react-router-dom';

import { getUninterpretedLines, type UninterpretedLines as Receipt } from '../../services/api';
import { copyToClipboard } from '../../utils/clipboard';
import { asAxiosError, formatApiError } from '../../utils/apiErrors';
import { Badge } from '../ui/badge';

const KIND_LABEL: Record<string, { label: string; hint: string }> = {
  dropped: { label: 'dropped', hint: 'No pattern read this line: it is not in the inventory.' },
  text_only: { label: 'kept as text', hint: 'Stored as the tool’s line and shown on the host, but nothing in it was read: no login, flag or check.' },
  module_as_login: { label: 'module, read as a login', hint: 'A module’s result read by the login pattern — probably misread.' },
  module_as_text: { label: 'module, kept as text', hint: 'A module’s result kept as the tool’s line; not interpreted.' },
};

const linkButton =
  'rounded text-caption text-primary underline-offset-2 hover:underline focus:outline-none focus-visible:ring-2 focus-visible:ring-ring';

interface Props {
  jobId: number;
  total: number;
  distinct: number;
  formatKey?: string | null;
}

const UninterpretedLines: React.FC<Props> = ({ jobId, total, distinct, formatKey }) => {
  const [open, setOpen] = useState(false);
  const [receipt, setReceipt] = useState<Receipt | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);

  const toggle = () => {
    const next = !open;
    setOpen(next);
    if (next && !receipt) {
      setError(null);
      getUninterpretedLines(jobId)
        .then(setReceipt)
        .catch((err) => setError(formatApiError(asAxiosError(err), 'The lines could not be loaded')));
    }
  };

  const copyAll = async () => {
    if (!receipt) return;
    const text = [
      `# ${receipt.tool_name ?? 'import'} — ${receipt.total} line(s) not interpreted, ${receipt.distinct} shape(s); values redacted`,
      ...receipt.shapes.map((s) => `${s.count}\t${s.kind}\t${s.shape}`),
    ].join('\n');
    setCopied(await copyToClipboard(text));
  };

  return (
    <div className="min-w-0 text-caption">
      <button type="button" className={linkButton} aria-expanded={open} onClick={toggle}>
        {total.toLocaleString()} line{total === 1 ? '' : 's'} not interpreted
        {' '}({distinct} shape{distinct === 1 ? '' : 's'}) · {open ? 'hide' : 'show'}
      </button>
      {open && (
        <div className="mt-xs space-y-xs">
          <p className="text-muted-foreground">
            Each line’s structure with its values replaced — addresses, names, credentials, hashes and
            paths become placeholders — so these can be shared to improve the parser without the file.
            {formatKey && (
              <>
                {' '}
                <Link to={`/reference/tool-coverage?format=${encodeURIComponent(formatKey)}`}
                  className="text-info underline-offset-2 hover:underline">
                  What BlueStick reads from this format
                </Link>
              </>
            )}
          </p>
          {error && <p className="text-destructive">{error}</p>}
          {!receipt && !error && <p className="text-muted-foreground">Loading…</p>}
          {receipt && (
            <>
              <ul className="divide-y divide-border rounded-control border border-border">
                {receipt.shapes.map((s) => {
                  const kind = KIND_LABEL[s.kind] ?? { label: s.kind, hint: '' };
                  return (
                    <li key={`${s.kind}|${s.shape}`} className="flex min-w-0 items-baseline gap-xs px-sm py-xxs">
                      <span className="w-10 shrink-0 text-right tabular-nums text-muted-foreground">×{s.count}</span>
                      <Badge variant={s.kind === 'text_only' ? 'outline' : 'warning'} className="shrink-0 whitespace-nowrap"
                        title={kind.hint}>
                        {kind.label}
                      </Badge>
                      <code className="min-w-0 flex-1 break-all font-mono">{s.shape}</code>
                    </li>
                  );
                })}
              </ul>
              {receipt.distinct > receipt.shapes.length && (
                <p className="text-muted-foreground">
                  The {receipt.shapes.length} most frequent of {receipt.distinct} shapes.
                </p>
              )}
              <button type="button" className={linkButton} onClick={copyAll}>
                {copied ? 'copied' : 'copy all (tab-separated)'}
              </button>
            </>
          )}
        </div>
      )}
    </div>
  );
};

export default UninterpretedLines;
