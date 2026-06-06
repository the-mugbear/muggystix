import React from 'react';
import { ScrollText, ShieldAlert } from 'lucide-react';

import { Host, NseScript } from '../services/api';
import { Badge } from './ui/badge';
import { Card, CardContent, CardHeader, CardTitle } from './ui/card';
import {
  Accordion,
  AccordionContent,
  AccordionItem,
  AccordionTrigger,
} from './ui/accordion';

/**
 * NseScriptsCard — surfaces Nmap Scripting Engine (NSE) output that
 * the nmap parser has always stored but the UI never rendered.
 *
 * NSE output is the richest free-form data nmap produces — SMB signing
 * posture, TLS cipher grades, anonymous-FTP checks, `vuln-*` CVE hits.
 * It is captured at two levels and shown in two sections here:
 *   - Host scripts  (Host.host_scripts) — host-wide, e.g. smb-os-discovery
 *   - Port scripts  (Port.scripts)      — grouped under their port
 *
 * The card renders nothing when a host has no script output, so it
 * stays invisible for hosts scanned without `-sC`/`--script`.
 */

// Friendly display names for the NSE scripts seen most often.  Anything
// not in the map falls back to a title-cased version of the script id,
// so an unknown script still reads cleanly (`http-wordpress-enum` →
// "Http Wordpress Enum").
const NSE_FRIENDLY_NAMES: Record<string, string> = {
  'ssl-cert': 'SSL/TLS Certificate',
  'ssl-enum-ciphers': 'SSL/TLS Cipher Enumeration',
  'ssl-poodle': 'POODLE (SSLv3) Check',
  'ssl-heartbleed': 'Heartbleed Check',
  'ssl-dh-params': 'TLS Diffie-Hellman Parameters',
  'smb-os-discovery': 'SMB OS Discovery',
  'smb-security-mode': 'SMB Security Mode (signing)',
  'smb2-security-mode': 'SMB2 Security Mode (signing)',
  'smb2-capabilities': 'SMB2 Capabilities',
  'smb-enum-shares': 'SMB Share Enumeration',
  'smb-enum-users': 'SMB User Enumeration',
  'smb-protocols': 'SMB Protocol Versions',
  'http-title': 'HTTP Page Title',
  'http-server-header': 'HTTP Server Header',
  'http-headers': 'HTTP Response Headers',
  'http-methods': 'Allowed HTTP Methods',
  'http-robots.txt': 'robots.txt',
  'http-auth': 'HTTP Authentication',
  'ssh-hostkey': 'SSH Host Key',
  'ssh2-enum-algos': 'SSH Algorithms',
  'ssh-auth-methods': 'SSH Auth Methods',
  'dns-nsid': 'DNS NSID',
  'ftp-anon': 'Anonymous FTP Access',
  'rdp-ntlm-info': 'RDP NTLM Info',
  'rdp-enum-encryption': 'RDP Encryption',
  'ms-sql-info': 'MS SQL Server Info',
  banner: 'Service Banner',
  vulners: 'Vulners CVE Lookup',
};

const titleCase = (raw: string): string =>
  raw
    .replace(/[-_.]+/g, ' ')
    .replace(/\s+/g, ' ')
    .trim()
    .replace(/\b\w/g, (c) => c.toUpperCase());

const friendlyName = (scriptId: string): string =>
  NSE_FRIENDLY_NAMES[scriptId] ?? titleCase(scriptId);

// A script is "security-relevant" — gets a warning badge — when it is
// an NSE vuln-* probe or a member of a small curated high-signal set.
// This is a display hint only; it doesn't assert the host IS vulnerable.
const SECURITY_RELEVANT = new Set([
  'ssl-poodle',
  'ssl-heartbleed',
  'ssl-enum-ciphers',
  'smb-security-mode',
  'smb2-security-mode',
  'ftp-anon',
  'vulners',
]);

const isSecurityRelevant = (scriptId: string): boolean =>
  scriptId.startsWith('vuln') || SECURITY_RELEVANT.has(scriptId);

interface ScriptItemProps {
  script: NseScript;
  /** Unique accordion value — script id alone collides across ports. */
  itemValue: string;
}

const ScriptItem: React.FC<ScriptItemProps> = ({ script, itemValue }) => {
  const flagged = isSecurityRelevant(script.script_id);
  const output = (script.output || '').trim();
  return (
    <AccordionItem value={itemValue} className="border-border">
      <AccordionTrigger className="py-sm hover:no-underline">
        <div className="flex min-w-0 flex-1 flex-wrap items-center gap-xs pr-sm text-left">
          {flagged && (
            <ShieldAlert className="size-4 shrink-0 text-warning" aria-hidden />
          )}
          <span className="min-w-0 truncate font-medium text-metadata">
            {friendlyName(script.script_id)}
          </span>
          <code className="truncate font-mono text-caption text-muted-foreground">
            {script.script_id}
          </code>
          {flagged && (
            <Badge variant="warning-outline" className="text-caption">
              security check
            </Badge>
          )}
        </div>
      </AccordionTrigger>
      <AccordionContent>
        {output ? (
          <pre className="max-h-80 overflow-auto whitespace-pre-wrap break-words rounded-control border border-border bg-muted/40 p-sm font-mono text-caption leading-relaxed">
            {output}
          </pre>
        ) : (
          <p className="text-caption text-muted-foreground">
            The script ran but produced no output.
          </p>
        )}
      </AccordionContent>
    </AccordionItem>
  );
};

interface NseScriptsCardProps {
  host: Host;
}

const NseScriptsCard: React.FC<NseScriptsCardProps> = ({ host }) => {
  const hostScripts = host.host_scripts ?? [];

  // Ports that actually carry script output, sorted by port number so
  // the section reads in the same order as the ports table.
  const portsWithScripts = (host.ports ?? [])
    .filter((p) => (p.scripts?.length ?? 0) > 0)
    .slice()
    .sort((a, b) => a.port_number - b.port_number);

  const totalScripts =
    hostScripts.length +
    portsWithScripts.reduce((acc, p) => acc + (p.scripts?.length ?? 0), 0);

  if (totalScripts === 0) return null;

  return (
    <Card>
      <CardHeader>
        <div className="flex flex-wrap items-center gap-xs">
          <ScrollText className="size-5 text-muted-foreground" aria-hidden />
          <CardTitle>NSE Script Output</CardTitle>
          <Badge variant="outline">{totalScripts}</Badge>
        </div>
        <p className="mt-xxs text-caption text-muted-foreground">
          Free-form results from the Nmap Scripting Engine (<code className="font-mono">-sC</code> /{' '}
          <code className="font-mono">--script</code>). Expand a row to read the raw output.
        </p>
      </CardHeader>
      <CardContent className="space-y-md">
        {hostScripts.length > 0 && (
          <div>
            <h3 className="mb-xs text-metadata font-semibold">
              Host scripts
              <span className="ml-xs font-normal text-muted-foreground">
                ({hostScripts.length})
              </span>
            </h3>
            <Accordion type="multiple" className="rounded-control border border-border px-sm">
              {hostScripts.map((script) => (
                <ScriptItem
                  key={`host-${script.id}`}
                  script={script}
                  itemValue={`host-${script.id}`}
                />
              ))}
            </Accordion>
          </div>
        )}

        {portsWithScripts.length > 0 && (
          <div className="space-y-sm">
            <h3 className="text-metadata font-semibold">
              Port scripts
              <span className="ml-xs font-normal text-muted-foreground">
                ({portsWithScripts.length} port{portsWithScripts.length === 1 ? '' : 's'})
              </span>
            </h3>
            {portsWithScripts.map((port) => (
              <div key={port.id}>
                <div className="mb-xxs flex flex-wrap items-baseline gap-xs">
                  <span className="font-mono text-metadata font-semibold">
                    {port.port_number}/{port.protocol}
                  </span>
                  {port.service_name && (
                    <span className="truncate text-caption text-muted-foreground">
                      {port.service_name}
                    </span>
                  )}
                </div>
                <Accordion type="multiple" className="rounded-control border border-border px-sm">
                  {(port.scripts ?? []).map((script) => (
                    <ScriptItem
                      key={`port-${port.id}-${script.id}`}
                      script={script}
                      itemValue={`port-${port.id}-${script.id}`}
                    />
                  ))}
                </Accordion>
              </div>
            ))}
          </div>
        )}
      </CardContent>
    </Card>
  );
};

export default NseScriptsCard;
