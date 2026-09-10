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
                  MCP connection is refused, and the client’s error (“self-signed certificate”,
                  “unable to verify”) does not say a one-time setup fixes it. The script only
                  prepares trust — it writes the certificate under your home directory and adds it
                  to what your client already trusts; it turns nothing off.
                </>
              )}
            </p>
            {/* v5.203.0 — explicit steps. "Run this, then restart" skipped the
                one operators miss: the script runs in a child shell and cannot
                export into the shell that launched it, so a client restarted
                from a shell without the exports still refuses the connection. */}
            <ol className="mb-xs list-decimal space-y-xxs pl-md text-caption text-muted-foreground">
              <li>
                Download, <strong>read</strong>, then run it on the machine that runs the client:
              </li>
            </ol>
            <CodeBlock text={commands} label="certificate trust setup" />
            <ol
              className="mt-xs list-decimal space-y-xxs pl-md text-caption text-muted-foreground"
              start={2}
            >
              <li>
                Compare the SHA-256 it prints
                {fingerprint ? (
                  <>
                    {' '}against{' '}
                    <span className="break-all font-mono text-foreground">{fingerprint}</span>
                  </>
                ) : (
                  <> against the one on the MCP reference page</>
                )}
                . Installing a trust anchor without checking it is trusting whatever answered.
              </li>
              <li>
                <strong>Add the two exports it prints to your shell profile</strong> (
                <span className="font-mono">NODE_EXTRA_CA_CERTS</span> for VS Code and Claude Code,{' '}
                <span className="font-mono">SSL_CERT_DIR</span> for Codex). The script cannot set
                them for you.
              </li>
              <li>
                Open a <strong>new shell</strong> and launch the client from it. Both variables are
                read at client start; a client restarted from a shell without them is unchanged.
              </li>
              <li>
                Check the client’s own status (
                <span className="font-mono">claude mcp list</span>, Codex’s{' '}
                <span className="font-mono">/mcp</span>, VS Code’s “MCP: List Servers”) — then ask the
                verification prompt below. Only an authenticated tool call proves the key works.
              </li>
            </ol>
            <p className="mt-xs text-caption text-muted-foreground">
              Prefer to set it up by hand, or on a remote host? The per-client variables and the
              raw certificate are on the{' '}
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
