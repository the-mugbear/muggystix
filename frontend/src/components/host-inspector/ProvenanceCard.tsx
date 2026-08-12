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
import { Globe, ShieldCheck, ShieldAlert } from 'lucide-react';

import type { HostCertOrg, HostCertStatus, NetworkAttribution } from '../../services/api';
import { Badge } from '../ui/badge';
import { Card, CardContent, CardHeader, CardTitle } from '../ui/card';
import { safeFallback } from '../../utils/uiStyles';

export interface ProvenanceCardProps {
  attributions?: NetworkAttribution[];
  certOrgs?: HostCertOrg[];
  /** Expiry / self-signed state. Separate from certOrgs because a DV cert has
   *  no organisation but still has an expiry worth acting on. */
  certStatus?: HostCertStatus[];
}

/** Days until expiry; negative when already expired. */
const daysUntil = (iso: string | null | undefined): number | null => {
  if (!iso) return null;
  const t = new Date(iso).getTime();
  if (Number.isNaN(t)) return null;
  return Math.round((t - Date.now()) / 86_400_000);
};

/** Expiry is a deadline, so it's stated as one. 30 days is the usual renewal
 *  window — inside it, the operator has something to act on. */
const EXPIRY_WARN_DAYS = 30;

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

/** True when an attribution lookup is old enough to re-verify before citing. */
export const attributionIsStale = (iso: string | null | undefined): boolean => {
  if (!iso) return false;
  const then = new Date(iso).getTime();
  return !Number.isNaN(then) && Date.now() - then > STALE_DAYS * 86_400_000;
};

/** The cert names one org and the registration another — the scope-validation
 *  signal. Substring either way, since one is often a parent/brand of the
 *  other ("Amazon" vs "Amazon.com, Inc."). */
export function certRegistrationDisagree(
  attributions: NetworkAttribution[] = [],
  certOrgs: HostCertOrg[] = [],
): boolean {
  const registeredOrgs = attributions
    .map((a) => a.org_name?.toLowerCase().trim())
    .filter((v): v is string => !!v);
  const certOrgNames = certOrgs.map((c) => c.org.toLowerCase().trim());
  return (
    registeredOrgs.length > 0 &&
    certOrgNames.length > 0 &&
    !certOrgNames.some((cert) =>
      registeredOrgs.some((reg) => reg.includes(cert) || cert.includes(reg)),
    )
  );
}

/** True when the Provenance card holds MORE than the one-line owner summary the
 *  host header already shows — so it's worth rendering as its own card. When
 *  false, a single fresh attribution is fully captured by the header line and
 *  the card would be redundant chrome. Cert data, a second block, or a stale
 *  lookup (all things the header omits, and the last a signal to act on) tip it
 *  back to worth-showing. */
export function provenanceExceedsSummary(
  attributions: NetworkAttribution[] = [],
  certOrgs: HostCertOrg[] = [],
  certStatus: HostCertStatus[] = [],
): boolean {
  if (certOrgs.length > 0 || certStatus.length > 0) return true;
  if (attributions.length > 1) return true;
  return attributions.some((a) => attributionIsStale(a.looked_up_at));
}

export const ProvenanceCard: React.FC<ProvenanceCardProps> = ({
  attributions = [],
  certOrgs = [],
  certStatus = [],
}) => {
  if (attributions.length === 0 && certOrgs.length === 0 && certStatus.length === 0) {
    return null;
  }

  // Disagreement between an independently-validated cert and a self-declared
  // registration is the signal worth raising.
  const disagrees = certRegistrationDisagree(attributions, certOrgs);

  return (
    <Card id="host-detail-provenance">
      <CardHeader>
        <div className="flex items-center gap-xs">
          <Globe className="size-5 text-info" aria-hidden />
          <CardTitle>Provenance</CardTitle>
          {disagrees && (
            <Badge variant="outline" className="border-warning/40 text-warning">scope check</Badge>
          )}
        </div>
      </CardHeader>
      <CardContent className="space-y-sm">
        {disagrees && (
          <div className="flex items-start gap-xs rounded-control border border-warning/40 bg-warning/10 p-sm">
            <ShieldAlert className="mt-0.5 size-4 shrink-0 text-warning" aria-hidden />
            <p className="text-caption text-foreground">
              <span className="font-medium">Certificate and registration disagree.</span>{' '}
              The TLS certificate names a different organisation than the netblock is
              registered to. Common for hosted / CDN-fronted services — but worth confirming
              this host is actually in the engagement's scope before acting on it.
            </p>
          </div>
        )}
        {attributions.map((a) => (
          <div key={a.id} className="min-w-0 space-y-xxs">
            <div className="flex flex-wrap items-center gap-xs">
              <span className="font-mono text-caption text-muted-foreground">{a.cidr}</span>
              <span className="min-w-0 break-words text-metadata font-medium text-foreground">
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
                <span className={attributionIsStale(a.looked_up_at) ? 'text-warning' : undefined}>
                  looked up {formatAge(a.looked_up_at)}
                  {attributionIsStale(a.looked_up_at) && ' — re-check before citing'}
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
                <span className="min-w-0 break-words text-metadata font-medium text-foreground">{c.org}</span>
                <span className="min-w-0 truncate text-caption text-muted-foreground">
                  on certificate for {c.url}
                  {c.issuer ? ` · issued by ${c.issuer}` : ''}
                </span>
              </div>
            ))}
          </div>
        )}

        {certStatus.length > 0 && (
          <div className="space-y-xxs border-t border-border pt-sm">
            {certStatus.map((c) => {
              const days = daysUntil(c.not_after);
              const expired = days !== null && days < 0;
              const expiringSoon = days !== null && days >= 0 && days <= EXPIRY_WARN_DAYS;
              return (
                <div key={`status-${c.url}`} className="flex flex-wrap items-center gap-xs">
                  {c.self_signed ? (
                    <ShieldAlert className="size-3.5 shrink-0 text-warning" aria-hidden />
                  ) : (
                    <ShieldCheck className="size-3.5 shrink-0 text-muted-foreground" aria-hidden />
                  )}
                  <span className="min-w-0 truncate text-caption text-muted-foreground">
                    {safeFallback(c.url)}
                  </span>
                  {c.self_signed && (
                    <Badge variant="outline" className="border-warning/40 text-warning">
                      self-signed
                    </Badge>
                  )}
                  {days !== null && (
                    <span
                      className={
                        expired || expiringSoon ? 'text-metadata text-destructive' : 'text-caption text-muted-foreground'
                      }
                    >
                      {expired
                        ? `certificate expired ${Math.abs(days)} day${Math.abs(days) === 1 ? '' : 's'} ago`
                        : `expires in ${days} day${days === 1 ? '' : 's'}`}
                    </span>
                  )}
                </div>
              );
            })}
          </div>
        )}
      </CardContent>
    </Card>
  );
};

export default ProvenanceCard;
