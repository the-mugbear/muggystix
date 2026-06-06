import * as React from 'react';
import { ArrowRight, X } from 'lucide-react';
import { Button } from './ui/button';
import { cn } from '../utils/cn';

/**
 * NextStepBanner — terminal-state guidance for workflows that "succeed"
 * silently.  The recurring antipattern surfaced by the UX audit: recon
 * completes → page goes quiet; plan submitted → no hint of what's next;
 * password changed → user stranded.
 *
 * Use this at the top of any page that can land on a terminal state
 * the user needs to act on.  Pair `title` with a short body that names
 * the next step concretely, and a `primaryCta` that performs it.
 *
 * Tones map to standard surfaces:
 *  - `success` (default) — celebratory but neutral: "done — here's
 *    what's next"
 *  - `info` — neutral hand-off, no celebration
 *  - `warning` — terminal state but with caveats the user should know
 *
 * `dismissible` defaults to true.  Pass `false` for handoffs the user
 * shouldn't be able to bury (e.g. a stuck recon session needing
 * Abandon).
 */
type Tone = 'success' | 'info' | 'warning';

const TONE_CLASS: Record<Tone, string> = {
  success: 'border-success/40 bg-success/10 text-success',
  info: 'border-info/40 bg-info/10 text-info',
  warning: 'border-warning/40 bg-warning/10 text-warning',
};

export interface NextStepBannerProps {
  title: React.ReactNode;
  body?: React.ReactNode;
  /** Primary call-to-action.  Provide either a button or a link. */
  primaryCta?: { label: string; onClick?: () => void; href?: string };
  /** Secondary action (less emphasis). */
  secondaryCta?: { label: string; onClick?: () => void; href?: string };
  tone?: Tone;
  /** Optional icon to lead with. */
  icon?: React.ReactNode;
  /** Show an `X` dismiss button.  Defaults to true. */
  dismissible?: boolean;
  onDismiss?: () => void;
  className?: string;
}

export const NextStepBanner: React.FC<NextStepBannerProps> = ({
  title,
  body,
  primaryCta,
  secondaryCta,
  tone = 'success',
  icon,
  dismissible = true,
  onDismiss,
  className,
}) => {
  const [dismissed, setDismissed] = React.useState(false);
  if (dismissed) return null;
  const handleDismiss = () => {
    setDismissed(true);
    onDismiss?.();
  };

  const renderCta = (cta: NonNullable<NextStepBannerProps['primaryCta']>, variant: 'default' | 'outline') =>
    cta.href ? (
      <Button asChild size="sm" variant={variant}>
        <a href={cta.href}>
          {cta.label}
          {variant === 'default' && <ArrowRight className="size-3.5" aria-hidden />}
        </a>
      </Button>
    ) : (
      <Button size="sm" variant={variant} onClick={cta.onClick}>
        {cta.label}
        {variant === 'default' && <ArrowRight className="size-3.5" aria-hidden />}
      </Button>
    );

  return (
    <div
      role="status"
      className={cn(
        'flex items-start gap-sm rounded-panel border p-md',
        TONE_CLASS[tone],
        className,
      )}
    >
      {icon && <div className="mt-xxs shrink-0">{icon}</div>}
      <div className="min-w-0 flex-1">
        <p className="text-subheading font-semibold leading-tight">{title}</p>
        {body && <div className="mt-xxs text-metadata text-foreground/85">{body}</div>}
        {(primaryCta || secondaryCta) && (
          <div className="mt-sm flex flex-wrap gap-xs">
            {primaryCta && renderCta(primaryCta, 'default')}
            {secondaryCta && renderCta(secondaryCta, 'outline')}
          </div>
        )}
      </div>
      {dismissible && (
        <button
          type="button"
          onClick={handleDismiss}
          className="-mr-xxs -mt-xxs shrink-0 rounded-control p-xxs text-foreground/60 hover:bg-foreground/10 hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          aria-label="Dismiss"
        >
          <X className="size-4" aria-hidden />
        </button>
      )}
    </div>
  );
};
