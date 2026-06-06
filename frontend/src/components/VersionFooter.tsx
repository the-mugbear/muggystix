import React from 'react';
import buildInfo from '../buildInfo.json';

/**
 * VersionFooter — fixed pill in the bottom-right corner showing build
 * identity.  Removed from the live shell per audit (#12 — it occluded
 * pagination + snackbars).  Kept as an importable component for the
 * UserMenu "About" entry to surface on demand.
 */
const VersionFooter: React.FC = () => {
  const buildTime =
    process.env.REACT_APP_BUILD_TIME || buildInfo.buildTime || new Date().toISOString();
  const version = process.env.REACT_APP_VERSION || buildInfo.frontendVersion || '1.0.0';
  const backendVersion =
    process.env.REACT_APP_BACKEND_VERSION || buildInfo.backendVersion || 'unknown';
  const gitCommit = process.env.REACT_APP_GIT_COMMIT || buildInfo.gitCommit || 'dev';

  return (
    <div
      className="fixed bottom-3 right-3 z-[1000] max-w-[min(calc(100vw-24px),560px)] truncate rounded-chip border border-border bg-card/95 px-sm py-xxs text-caption text-muted-foreground shadow-overlay"
    >
      BlueStick v{version} · API {backendVersion} · Built{' '}
      {new Date(buildTime).toLocaleString()} · {gitCommit.substring(0, 7)}
    </div>
  );
};

export default VersionFooter;
