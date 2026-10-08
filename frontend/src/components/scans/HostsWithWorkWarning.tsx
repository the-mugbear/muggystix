import { Link } from 'react-router-dom';
import { AlertTriangle } from 'lucide-react';
import { Checkbox } from '../ui/checkbox';
import { workPhrase, type HostWithWork } from '../../utils/scanDeletion';

interface HostsWithWorkWarningProps {
  /** How many of the hosts the delete removes carry work. */
  count: number;
  /** The first of them (the server sends at most 50), in address order. */
  sample: HostWithWork[];
  /** The server's own words, when it refused a delete for these hosts. */
  message?: string | null;
  reviewed: boolean;
  onReviewedChange: (reviewed: boolean) => void;
  disabled?: boolean;
}

/**
 * The delete-scan dialog's warning: hosts only this scan saw are removed with
 * the notes, tests, evidence… people put on them.  Each address opens the host
 * in a new tab, so the reader can look without losing the dialog, and the
 * delete stays locked until the box is ticked.
 */
export default function HostsWithWorkWarning({
  count,
  sample,
  message,
  reviewed,
  onReviewedChange,
  disabled = false,
}: HostsWithWorkWarningProps) {
  if (count <= 0) return null;
  const more = count - sample.length;
  return (
    <div
      data-testid="hosts-with-work"
      className="mt-3 min-w-0 rounded-md border border-warning/40 bg-warning/10 p-3 text-metadata"
    >
      {message && (
        <p role="alert" className="mb-2 break-words font-medium">
          {message}
        </p>
      )}
      <div className="flex items-start gap-2">
        <AlertTriangle className="mt-0.5 size-4 shrink-0 text-warning" aria-hidden />
        <span>
          <span className="font-medium">{count.toLocaleString()}</span> of the hosts this
          removes {count === 1 ? 'has' : 'have'} work on{' '}
          {count === 1 ? 'it' : 'them'}, which goes with {count === 1 ? 'it' : 'them'}
          {sample.length > 0 ? ':' : '.'}
        </span>
      </div>
      {sample.length > 0 && (
        <ul
          data-testid="hosts-with-work-list"
          className="mt-2 max-h-40 space-y-1 overflow-y-auto overflow-x-hidden pr-1"
        >
          {sample.map((host) => (
            <li key={host.host_id} className="min-w-0">
              <div className="flex min-w-0 items-baseline gap-2">
                <Link
                  to={`/hosts/${host.host_id}`}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="shrink-0 font-mono text-primary underline-offset-2 hover:underline"
                >
                  {host.ip_address}
                </Link>
                {host.hostname && (
                  <span className="min-w-0 truncate text-muted-foreground" title={host.hostname}>
                    {host.hostname}
                  </span>
                )}
              </div>
              {/* What is lost is the point of the warning: it wraps, never truncates. */}
              <div className="break-words">{workPhrase(host.work)}</div>
            </li>
          ))}
        </ul>
      )}
      {sample.length > 0 && more > 0 && (
        <p className="mt-1 text-muted-foreground">and {more.toLocaleString()} more</p>
      )}
      <label className="mt-2.5 flex items-start gap-2 border-t border-warning/40 pt-2.5">
        <Checkbox
          className="mt-0.5"
          checked={reviewed}
          disabled={disabled}
          onCheckedChange={(value) => onReviewedChange(value === true)}
        />
        <span>
          I have reviewed {count === 1 ? 'this host' : 'these hosts'}; delete{' '}
          {count === 1 ? 'its' : 'their'} work with the scan
        </span>
      </label>
    </div>
  );
}
