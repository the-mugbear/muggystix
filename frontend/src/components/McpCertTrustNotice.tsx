/**
 * McpCertTrustNotice — the one prerequisite a first MCP connection needs.
 *
 * The self-signed certificate is where every client fails first: until it's
 * trusted, the connection is refused before a single tool call, and the error
 * the client shows ("self-signed certificate", "unable to verify") gives no
 * hint that a one-time setup fixes it. That story lived only on /reference/mcp
 * — two levels from the dialog where an operator actually mints a key and
 * connects. This surfaces it at the moment of connection, collapsed so it
 * doesn't crowd the config, with the full write-up one link away.
 *
 * Self-contained: it fetches the catalog itself (cheap, public, cached) so any
 * dialog can drop it in with no new props to thread through. Renders nothing
 * until the fetch resolves, so it never flashes an empty frame.
 */
import React, { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { ShieldAlert } from 'lucide-react';

import { getMcpTools, type McpCatalog } from '../services/api';
import { buildCertTrust } from '../utils/mcpCert';
import { CodeBlock } from './ui/code-block';
import {
  Accordion,
  AccordionContent,
  AccordionItem,
  AccordionTrigger,
} from './ui/accordion';

const McpCertTrustNotice: React.FC = () => {
  const [catalog, setCatalog] = useState<McpCatalog | null>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    let cancelled = false;
    getMcpTools()
      .then((c) => !cancelled && setCatalog(c))
      .catch(() => !cancelled && setFailed(true));
    return () => {
      cancelled = true;
    };
  }, []);

  // Before the catalog lands (or if it failed), stay out of the way — the
  // connect config below is the primary content and stands on its own.
  if (!catalog || failed) return null;

  const { fingerprint, selfSigned, commands } = buildCertTrust(catalog);
  const caIssued = selfSigned === false;

  return (
    <div className="mb-sm rounded-control border border-warning/40 bg-warning/5">
      <Accordion type="single" collapsible>
        <AccordionItem value="cert" className="border-0">
          <AccordionTrigger className="px-sm py-xs hover:no-underline">
            <span className="flex items-center gap-xs text-metadata font-semibold">
              <ShieldAlert className="size-4 shrink-0 text-warning" aria-hidden />
              {caIssued
                ? 'Connection refused? Trust this deployment’s certificate'
                : 'First: trust this deployment’s certificate (once)'}
            </span>
          </AccordionTrigger>
          <AccordionContent className="px-sm">
            <p className="mb-xs text-caption text-muted-foreground">
              {caIssued ? (
                <>
                  This deployment presents a CA-issued certificate, so most clients connect with no
                  setup. If yours refuses because the CA is an internal one it doesn’t know, pin it:
                </>
              ) : (
                <>
                  BlueStick defaults to a self-signed certificate. Until your client trusts it, every
                  MCP connection is refused. Run this once, then <strong>restart the client</strong>{' '}
                  (the trust variable is read at startup):
                </>
              )}
            </p>
            <CodeBlock text={commands} label="certificate trust setup" />
            <p className="mt-xs text-caption text-muted-foreground">
              Read the script before running — it installs a trust anchor.
              {fingerprint ? (
                <>
                  {' '}It prints a SHA-256 that must match{' '}
                  <span className="break-all font-mono text-foreground">{fingerprint}</span>.
                </>
              ) : null}{' '}
              Full walkthrough (per-client variables, VS Code vs Codex) on the{' '}
              <Link to="/reference/mcp" className="underline">
                MCP reference
              </Link>
              .
            </p>
          </AccordionContent>
        </AccordionItem>
      </Accordion>
    </div>
  );
};

export default McpCertTrustNotice;
