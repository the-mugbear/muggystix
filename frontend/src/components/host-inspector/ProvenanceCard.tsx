/**
 * Where this host is registered and hosted.
 *
 * Scope validation was previously self-referential — a host was "in scope"
 * because its address fell inside a CIDR someone typed into the scope, which
 * checks a spreadsheet against itself. This card shows the outside world's
 * answer: who the netblock is registered to (RDAP), and who a CA validated as
 * controlling the name (certificate Organization).
 *
 * Two deliberate choices about absence:
 *
 *  - No attribution is rendered as "not looked up", never as a warning. Most
 *    engagements are internal, where none of this applies, and a host whose
 *    block simply hasn't been queried is not suspicious.
 *  - A missing certificate Organization is a *non-claim*. DV certificates
 *    (Let's Encrypt and most of the modern web) carry no O= at all, so its
 *    absence says nothing about ownership.
 *
 * Where the two signals disagree — the cert names one company, the
 * registration another — that is the interesting case, and it's surfaced
 * rather than resolved, matching how the confidence service treats scans that
 * disagree about a host attribute.
 */
import React from 'react';
import { Globe, ShieldCheck } from 'lucide-react';

import type { HostCertOrg, NetworkAttribution } from '../../services/api';
import { Badge } from '../ui/badge';
import { Card, CardContent, CardHeader, CardTitle } from '../ui/card';
import { Tooltip, TooltipContent, TooltipTrigger } from '../ui/tooltip';
import { safeFallback } from '../../utils/uiStyles';

export interface ProvenanceCardProps {
  attributions?: NetworkAttribution[];
  certOrgs?: HostCertOrg[];
}

const formatAge = (iso: string | null): string | null => {
  if (!iso) return null;
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return null;
  const days = Math.floor((Date.now() - then) / 86_400_000);
  if (days < 1) return 'today';
  if (days === 1) return 'yesterday';
  if (days < 60) return `${days} days ago`;
  return `${Math.round(days / 30)} months ago`;
};

/** Registration older than this is called out — an operator shouldn't hand a
 *  client evidence that was true six months ago without knowing it. */
const STALE_DAYS = 180;

const isStale = (iso: string | null): boolean => {
  if (!iso) return false;
  const then = new Date(iso).getTime();
  return !Number.isNaN(then) && Date.now() - then > STALE_DAYS * 86_400_000;
};

export const ProvenanceCard: React.FC<ProvenanceCardProps> = ({
  attributions = [],
  certOrgs = [],
}) => {
  if (attributions.length === 0 && certOrgs.length === 0) return null;

  // Disagreement between an independently-validated cert and a self-declared
  // registration is the signal worth raising.
  const registeredOrgs = attributions
    .map((a) => a.org_name?.toLowerCase().trim())
    .filter((v): v is string => !!v);
  const certOrgNames = certOrgs.map((c) => c.org.toLowerCase().trim());
  const disagrees =
    registeredOrgs.length > 0 &&
    certOrgNames.length > 0 &&
    !certOrgNames.some((cert) =>
      registeredOrgs.some((reg) => reg.includes(cert) || cert.includes(reg)),
    );

  return (
    <Card id="host-detail-provenance">
      <CardHeader>
        <div className="flex items-center gap-xs">
          <Globe className="size-5 text-info" aria-hidden />
          <CardTitle>Provenance</CardTitle>
          {disagrees && (
            <Tooltip>
              <TooltipTrigger asChild>
                <span
                  tabIndex={0}
                  className="rounded text-caption text-warning focus:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                >
                  certificate and registration disagree
                </span>
              </TooltipTrigger>
              <TooltipContent>
                The certificate names a different organisation than the netblock
                registration. Common for hosted or CDN-fronted services — worth
                confirming the host is in scope.
              </TooltipContent>
            </Tooltip>
          )}
        </div>
      </CardHeader>
      <CardContent className="space-y-sm">
        {attributions.map((a) => (
          <div key={a.id} className="min-w-0 space-y-xxs">
            <div className="flex flex-wrap items-center gap-xs">
              <span className="font-mono text-caption text-muted-foreground">{a.cidr}</span>
              <span className="text-metadata font-medium text-foreground">
                {safeFallback(a.org_name, 'Registrant not published')}
              </span>
              {a.country && <Badge variant="outline">{a.country}</Badge>}
              {/* Populated only once the cloud prefix-list importer lands;
                  until then this is always absent, which reads correctly as
                  "not known" rather than as "not in a cloud". */}
              {a.cloud_provider && (
                <Badge variant="info-outline">
                  {a.cloud_provider.toUpperCase()}
                  {a.cloud_region ? ` · ${a.cloud_region}` : ''}
                </Badge>
              )}
            </div>
            <p className="text-caption text-muted-foreground">
              {a.asn ? `AS${a.asn}${a.as_name ? ` (${a.as_name})` : ''} · ` : ''}
              {a.registry ? `${a.registry} · ` : ''}
              {a.handle ? `${a.handle} · ` : ''}
              {a.looked_up_at ? (
                <span className={isStale(a.looked_up_at) ? 'text-warning' : undefined}>
                  looked up {formatAge(a.looked_up_at)}
                  {isStale(a.looked_up_at) && ' — re-check before citing'}
                </span>
              ) : (
                'lookup date unknown'
              )}
            </p>
          </div>
        ))}

        {certOrgs.length > 0 && (
          <div className="space-y-xxs border-t border-border pt-sm">
            {certOrgs.map((c) => (
              <div key={c.url} className="flex flex-wrap items-center gap-xs">
                <ShieldCheck className="size-3.5 shrink-0 text-success" aria-hidden />
                <span className="text-metadata font-medium text-foreground">{c.org}</span>
                <span className="min-w-0 truncate text-caption text-muted-foreground">
                  on certificate for {c.url}
                  {c.issuer ? ` · issued by ${c.issuer}` : ''}
                </span>
              </div>
            ))}
          </div>
        )}
      </CardContent>
    </Card>
  );
};

export default ProvenanceCard;
