/**
 * Everything known about one service (v5.297.0), shown when its row in the
 * Services table is expanded: its weaknesses, what logged in (and how many
 * attempts failed), its web pages and discovered paths, and the tools' own
 * output — each script marked as read by BlueStick or kept as raw text.
 *
 * Replaces the per-tool sections (Web interfaces, Discovered paths, NSE
 * script output, SMB / AD enumeration) that split one service's evidence four
 * ways down the page.
 */
import React, { useState } from 'react';
import { Link } from 'react-router-dom';

import type { Port } from '../../services/api';
import { foldNetexecRows, NetExecResultRow } from '../NetExecCard';
import { ScriptItem } from '../NseScriptsCard';
import WebInterfacesCard from '../WebInterfacesCard';
import { WebPathRow } from '../WebPathsCard';
import { Accordion } from '../ui/accordion';
import { Badge } from '../ui/badge';
import { SEVERITY_BADGE_VARIANT, type Severity } from '../../utils/severity';
import { summariseAccess, type ServiceEvidence } from '../../utils/serviceEvidence';
import { jumpToInspectorSection } from './InspectorSection';

const PATH_PREVIEW = 15;

const Block: React.FC<{ title: string; hint?: React.ReactNode; children: React.ReactNode }> = ({ title, hint, children }) => (
  <div className="min-w-0">
    <h4 className="text-caption font-semibold uppercase tracking-wide text-muted-foreground">{title}</h4>
    {hint && <p className="text-caption text-muted-foreground">{hint}</p>}
    <div className="mt-xxs">{children}</div>
  </div>
);

const linkButton =
  'rounded text-caption text-primary underline-offset-2 hover:underline focus:outline-none focus-visible:ring-2 focus-visible:ring-ring';

interface Props {
  hostId: number;
  port: Port;
  evidence: ServiceEvidence;
}

const ServiceEvidencePanel: React.FC<Props> = ({ hostId, port, evidence }) => {
  const [showFailed, setShowFailed] = useState(false);
  const [showAllPaths, setShowAllPaths] = useState(false);
  const access = summariseAccess(foldNetexecRows(evidence.access).map((o) => ({ ...o, auth_success: o.latest.auth_success })));
  const scripts = port.scripts ?? [];
  const nothing = evidence.weaknesses.length + evidence.access.length + evidence.web.length
    + evidence.paths.length + scripts.length === 0;

  if (nothing) {
    return (
      <p className="text-caption text-muted-foreground">
        Nothing beyond the service identification was recorded for this port.
      </p>
    );
  }

  return (
    <div className="space-y-sm">
      {evidence.weaknesses.length > 0 && (
        <Block title={`Weaknesses (${evidence.weaknesses.length})`}>
          <ul className="space-y-xxs">
            {evidence.weaknesses.map((v) => (
              <li key={v.id} className="flex min-w-0 items-center gap-xs text-metadata">
                <Badge variant={(SEVERITY_BADGE_VARIANT[(v.severity ?? '').toLowerCase() as Severity] ?? 'outline') as never}
                  className="shrink-0 uppercase">
                  {v.severity ?? 'unknown'}
                </Badge>
                <button type="button" className={`${linkButton} min-w-0 truncate text-left text-metadata text-foreground`}
                  title={`${v.title ?? ''} — open in Weaknesses`}
                  onClick={() => jumpToInspectorSection('host-detail-vulnerabilities')}>
                  {v.title ?? 'Untitled observation'}
                </button>
                <span className="ml-auto shrink-0 text-caption uppercase text-muted-foreground">{v.source}</span>
              </li>
            ))}
          </ul>
        </Block>
      )}

      {evidence.access.length > 0 && (
        <Block
          title="Access"
          hint={
            <>
              NetExec / SMBMap results on this port.{' '}
              <Link to="/reference/tool-coverage?tool=netexec" className="text-info underline-offset-2 hover:underline">
                What BlueStick reads from NetExec
              </Link>
            </>
          }
        >
          <div className="divide-y divide-border">
            {access.worked.map(({ latest, count }) => (
              <NetExecResultRow key={latest.id} result={latest} seenCount={count} />
            ))}
            {access.other.map(({ latest, count }) => (
              <NetExecResultRow key={latest.id} result={latest} seenCount={count} />
            ))}
          </div>
          {access.failed.length > 0 && (
            <div className="pt-xxs">
              <button type="button" className={linkButton} aria-expanded={showFailed}
                onClick={() => setShowFailed((s) => !s)}>
                {access.failed.length} failed attempt{access.failed.length === 1 ? '' : 's'} · {showFailed ? 'hide' : 'show'}
              </button>
              {showFailed && (
                <div className="divide-y divide-border">
                  {access.failed.map(({ latest, count }) => (
                    <NetExecResultRow key={latest.id} result={latest} seenCount={count} />
                  ))}
                </div>
              )}
            </div>
          )}
        </Block>
      )}

      {evidence.web.length > 0 && (
        <Block title="Web">
          <WebInterfacesCard hostId={hostId} count={evidence.web.length} rows={evidence.web} embedded />
        </Block>
      )}

      {evidence.paths.length > 0 && (
        <Block title={`Discovered paths (${evidence.paths.length})`}>
          <ul className="divide-y divide-border">
            {(showAllPaths ? evidence.paths : evidence.paths.slice(0, PATH_PREVIEW)).map((r) => (
              <WebPathRow key={r.url} row={r} />
            ))}
          </ul>
          {evidence.paths.length > PATH_PREVIEW && (
            <button type="button" className={linkButton} onClick={() => setShowAllPaths((s) => !s)}>
              {showAllPaths ? 'show fewer' : `show all ${evidence.paths.length}`}
            </button>
          )}
        </Block>
      )}

      {scripts.length > 0 && (
        <Block
          title={`Tool output (${scripts.length})`}
          hint="Nmap scripts and masscan banners for this port, marked read by BlueStick or raw text."
        >
          <Accordion type="multiple" className="rounded-control border border-border px-sm">
            {scripts.map((s) => (
              <ScriptItem key={s.id} script={s} itemValue={`svc-${port.id}-${s.id}`} />
            ))}
          </Accordion>
        </Block>
      )}
    </div>
  );
};

export default ServiceEvidencePanel;
