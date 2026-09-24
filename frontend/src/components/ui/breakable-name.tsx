import * as React from 'react';
import { cn } from '../../utils/cn';

/** Characters after which a file name may wrap. */
const SEPARATOR = /([-_./\\])/;
/** A run between separators longer than this may still split anywhere, so a
 *  single unbreakable token wider than its column cannot overflow it. */
const LONG_RUN = 16;

/**
 * Split a name into the pieces a line may break between: each piece ends
 * with its separator ("netexec-", "spider-", "172.", "30.", …).
 */
export function nameSegments(name: string): string[] {
  const parts = name.split(SEPARATOR);
  const out: string[] = [];
  for (let i = 0; i < parts.length; i += 2) {
    const piece = parts[i] + (parts[i + 1] ?? '');
    if (piece) out.push(piece);
  }
  return out;
}

/**
 * A file name that wraps only after a separator (v5.289.0).
 *
 * `break-all` split "netexec-spider-172.30.77.10.json" as
 * "netexec-spider-172.30.77.1 / 0.json" and "…-nmap-tls.xml" as "…-nma /
 * p-tls.xml".  Here the text keeps `overflow-wrap: normal` and gains a
 * `<wbr>` after every '-', '_', '.' and '/', so a line breaks between tokens.
 * Only a run longer than {@link LONG_RUN} characters is allowed to split
 * anywhere — and the browser does that only when the run alone is wider than
 * the column (no other break opportunity on the line).
 */
export const BreakableName: React.FC<{
  name: string;
  className?: string;
  title?: string;
  as?: 'span' | 'p';
}> = ({ name, className, title, as = 'span' }) => {
  const Tag = as;
  const segments = nameSegments(name);
  return (
    <Tag className={cn('[overflow-wrap:normal] [word-break:normal]', className)} title={title}>
      {segments.map((seg, i) => (
        <React.Fragment key={i}>
          {seg.length > LONG_RUN ? <span className="[overflow-wrap:anywhere]">{seg}</span> : seg}
          {i < segments.length - 1 && <wbr />}
        </React.Fragment>
      ))}
    </Tag>
  );
};

export default BreakableName;
