import { Badge } from './badge';
import { cn } from '../../utils/cn';
import { SEVERITY_BADGE_VARIANT, SEVERITY_LABEL, type Severity } from '../../utils/severity';

/**
 * The one severity badge (v5.288.0).  The Findings list and Scanner
 * observations drew their own — one uppercase, one title case, and Info in
 * the scanner view as plain text — so the same word looked like two
 * different things side by side.  Colour and label both come from
 * `utils/severity`; an unrecognised value renders as a neutral badge with
 * its own text rather than vanishing.
 */
export function SeverityBadge({ severity, className }: { severity: string | null | undefined; className?: string }) {
  const key = (severity ?? '').toLowerCase() as Severity;
  const known = key in SEVERITY_LABEL;
  return (
    <Badge
      variant={(known ? SEVERITY_BADGE_VARIANT[key] : 'outline') as never}
      className={cn('max-w-full truncate', className)}
    >
      {known ? SEVERITY_LABEL[key] : (severity || 'Unknown')}
    </Badge>
  );
}

export default SeverityBadge;
