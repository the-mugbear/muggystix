import React from 'react';
import { cn } from '../utils/cn';

/**
 * Custom hand-rolled SVG icons used by Layout's sidebar nav + user
 * menu.  They give the app its own visual identity (the bell with
 * wave lines, the phosphor-style scope, etc.) instead of mixing in
 * generic icon-pack glyphs.
 *
 * Each icon renders as a 1em-sized inline SVG with `currentColor`
 * fill/stroke, so callers control color via Tailwind text-* classes
 * and size via Tailwind size-* classes (defaults to size-4 / 16px to
 * match lucide-react's default).
 *
 *   <ScopeIcon className="size-5 text-primary" />
 */
type IconProps = React.SVGAttributes<SVGSVGElement> & {
  className?: string;
};

const Base: React.FC<React.PropsWithChildren<IconProps>> = ({
  className,
  children,
  ...rest
}) => (
  <svg
    xmlns="http://www.w3.org/2000/svg"
    viewBox="0 0 24 24"
    className={cn('size-4 shrink-0', className)}
    aria-hidden="true"
    {...rest}
  >
    {children}
  </svg>
);

export const GridIcon = (props: IconProps) => (
  <Base {...props}>
    <rect x="3" y="3" width="7" height="7" rx="1.5" fill="none" stroke="currentColor" strokeWidth="1.8" />
    <rect x="14" y="3" width="7" height="7" rx="1.5" fill="none" stroke="currentColor" strokeWidth="1.8" />
    <rect x="3" y="14" width="7" height="7" rx="1.5" fill="none" stroke="currentColor" strokeWidth="1.8" />
    <rect x="14" y="14" width="7" height="7" rx="1.5" fill="none" stroke="currentColor" strokeWidth="1.8" />
  </Base>
);

export const ScanLinesIcon = (props: IconProps) => (
  <Base {...props}>
    <path d="M5 18a7 7 0 0 1 14 0" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" />
    <path d="M7.5 18a4.5 4.5 0 0 1 9 0" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" />
    <path d="M10 18a2 2 0 0 1 4 0" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" />
    <circle cx="12" cy="7" r="2.2" fill="none" stroke="currentColor" strokeWidth="1.8" />
  </Base>
);

export const ServerStackIcon = (props: IconProps) => (
  <Base {...props}>
    <rect x="4" y="4" width="16" height="5" rx="1.5" fill="none" stroke="currentColor" strokeWidth="1.8" />
    <rect x="4" y="10" width="16" height="5" rx="1.5" fill="none" stroke="currentColor" strokeWidth="1.8" />
    <rect x="4" y="16" width="16" height="4" rx="1.5" fill="none" stroke="currentColor" strokeWidth="1.8" />
    <circle cx="8" cy="6.5" r="0.9" fill="currentColor" />
    <circle cx="8" cy="12.5" r="0.9" fill="currentColor" />
    <circle cx="8" cy="18" r="0.9" fill="currentColor" />
  </Base>
);

export const ActivityPulseIcon = (props: IconProps) => (
  <Base {...props}>
    <path d="M3 13h4l2-4 4 8 2-4h6" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" />
  </Base>
);

export const ScopeIcon = (props: IconProps) => (
  <Base {...props}>
    <circle cx="12" cy="12" r="8" fill="none" stroke="currentColor" strokeWidth="1.8" />
    <circle cx="12" cy="12" r="4" fill="none" stroke="currentColor" strokeWidth="1.8" />
    <path d="M12 4v3M12 17v3M4 12h3M17 12h3" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" />
  </Base>
);

export const ShieldBadgeIcon = (props: IconProps) => (
  <Base {...props}>
    <path d="M12 3l7 3v5c0 4.6-2.7 7.8-7 10-4.3-2.2-7-5.4-7-10V6l7-3Z" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinejoin="round" />
    <path d="m9.5 12 1.7 1.7 3.5-3.7" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" />
  </Base>
);

export const KeyholeIcon = (props: IconProps) => (
  <Base {...props}>
    <circle cx="9" cy="12" r="3" fill="none" stroke="currentColor" strokeWidth="1.8" />
    <path d="M12 12h8m-2 0v-2m-2 2v-2" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" />
  </Base>
);

export const AlertHexIcon = (props: IconProps) => (
  <Base {...props}>
    <path d="m8 4 8 0 4 8-4 8H8l-4-8 4-8Z" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinejoin="round" />
    <path d="M12 8v5" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" />
    <circle cx="12" cy="16.5" r="1" fill="currentColor" />
  </Base>
);

export const BellRingsIcon = (props: IconProps) => (
  <Base {...props}>
    <path d="M12 5a4 4 0 0 1 4 4v2.5c0 .9.3 1.8.9 2.5l1.1 1.3H6l1.1-1.3c.6-.7.9-1.6.9-2.5V9a4 4 0 0 1 4-4Z" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinejoin="round" />
    <path d="M9.5 18a2.5 2.5 0 0 0 5 0" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" />
    <path d="M18.2 6.8a3.5 3.5 0 0 1 0 4.8M5.8 6.8a3.5 3.5 0 0 0 0 4.8" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" />
  </Base>
);

export const PaletteSwatchIcon = (props: IconProps) => (
  <Base {...props}>
    <path d="M12 4c4.4 0 8 2.9 8 6.6 0 2.9-2 4.7-4.5 4.7H14a1.5 1.5 0 0 0-1.5 1.5c0 1.6-1.2 2.7-3 2.7-3.4 0-5.5-2.8-5.5-6.1C4 8 7.4 4 12 4Z" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinejoin="round" />
    <circle cx="8" cy="10" r="1" fill="currentColor" />
    <circle cx="11" cy="8" r="1" fill="currentColor" />
    <circle cx="15" cy="9" r="1" fill="currentColor" />
    <circle cx="16" cy="13" r="1" fill="currentColor" />
  </Base>
);

export const TerminalWindowIcon = (props: IconProps) => (
  <Base {...props}>
    <rect x="3" y="5" width="18" height="14" rx="2" fill="none" stroke="currentColor" strokeWidth="1.8" />
    <path d="m7 11 2 2-2 2M12 15h4" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" />
    <path d="M3 8h18" fill="none" stroke="currentColor" strokeWidth="1.6" />
  </Base>
);

export const UserCardIcon = (props: IconProps) => (
  <Base {...props}>
    <circle cx="12" cy="8" r="3" fill="none" stroke="currentColor" strokeWidth="1.8" />
    <path d="M6 18c1.3-2.4 3.4-3.6 6-3.6s4.7 1.2 6 3.6" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" />
    <rect x="3" y="3" width="18" height="18" rx="3" fill="none" stroke="currentColor" strokeWidth="1.4" opacity="0.75" />
  </Base>
);

export const CogSixIcon = (props: IconProps) => (
  <Base {...props}>
    <circle cx="12" cy="12" r="2.8" fill="none" stroke="currentColor" strokeWidth="1.8" />
    <path d="M12 4.5v2M12 17.5v2M4.5 12h2M17.5 12h2M6.7 6.7l1.4 1.4M15.9 15.9l1.4 1.4M17.3 6.7l-1.4 1.4M8.1 15.9l-1.4 1.4" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" />
  </Base>
);

export const LogoutArrowIcon = (props: IconProps) => (
  <Base {...props}>
    <path d="M10 6H6a2 2 0 0 0-2 2v8a2 2 0 0 0 2 2h4" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" />
    <path d="M13 8l4 4-4 4M17 12H9" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" />
  </Base>
);

export const LockShieldIcon = (props: IconProps) => (
  <Base {...props}>
    <path d="M12 3l6 2.5v4.8c0 4-2.4 6.8-6 8.7-3.6-1.9-6-4.7-6-8.7V5.5L12 3Z" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinejoin="round" />
    <rect x="9" y="10.5" width="6" height="4.8" rx="1.2" fill="none" stroke="currentColor" strokeWidth="1.6" />
    <path d="M10.5 10.5V9.4a1.5 1.5 0 0 1 3 0v1.1" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" />
  </Base>
);

export const AdminShieldIcon = (props: IconProps) => (
  <Base {...props}>
    <path d="M12 3l7 3v5c0 4.6-2.7 7.8-7 10-4.3-2.2-7-5.4-7-10V6l7-3Z" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinejoin="round" />
    <path d="M12 8v7M8.5 11.5h7" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" />
  </Base>
);

export const AnalystChartIcon = (props: IconProps) => (
  <Base {...props}>
    <path d="M5 18V9M12 18V6M19 18v-4" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" />
    <path d="M4 18h16" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" />
  </Base>
);

export const AuditorClipboardIcon = (props: IconProps) => (
  <Base {...props}>
    <rect x="6" y="5" width="12" height="15" rx="2" fill="none" stroke="currentColor" strokeWidth="1.8" />
    <path d="M9 5.5h6a1.5 1.5 0 0 0-1.5-1.5h-3A1.5 1.5 0 0 0 9 5.5Z" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinejoin="round" />
    <path d="M9 10h6M9 14h6" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" />
  </Base>
);

export const ViewerEyeIcon = (props: IconProps) => (
  <Base {...props}>
    <path d="M2.5 12s3.5-5 9.5-5 9.5 5 9.5 5-3.5 5-9.5 5-9.5-5-9.5-5Z" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinejoin="round" />
    <circle cx="12" cy="12" r="2.5" fill="none" stroke="currentColor" strokeWidth="1.8" />
  </Base>
);
