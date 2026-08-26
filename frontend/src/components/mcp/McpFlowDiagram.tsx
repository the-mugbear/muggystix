/**
 * McpFlowDiagram — what a tool call actually does, as one picture.
 *
 * The whole security story of the MCP layer is "it changes how an agent
 * reaches this project, not what it may do": the transport forwards your key
 * and makes no decision of its own, and the same endpoint guard that vets a
 * curl vets the tool call. That is hard to hold from a paragraph and obvious
 * from a diagram — request flows left to right carrying one key, and the
 * boundary sits on the endpoint, not the transport.
 *
 * Theme-aware by construction: every colour is a Tailwind fill-/stroke-
 * utility bound to a CSS variable, so it flips with light / dark / phosphor
 * with no per-theme code here. Desktop-only surface, so it scrolls inside its
 * own container rather than reflowing.
 */
import React from 'react';

const McpFlowDiagram: React.FC = () => (
  <div className="overflow-x-auto">
    <svg
      viewBox="0 0 720 210"
      role="img"
      aria-label="A tool call flows from your AI client through the MCP transport into the agent endpoint, which is the authorization boundary. The transport forwards your API key unchanged and makes no authorization decision."
      className="mx-auto block min-w-[640px] max-w-[720px]"
    >
      <defs>
        <marker
          id="mcpflow-arrow"
          viewBox="0 0 10 10"
          refX="8"
          refY="5"
          markerWidth="7"
          markerHeight="7"
          orient="auto-start-reverse"
        >
          <path d="M0,0 L10,5 L0,10 z" className="fill-muted-foreground" />
        </marker>
      </defs>

      {/* Node A — the client, holding the key */}
      <rect x="8" y="40" width="196" height="96" rx="10" className="fill-card stroke-border" strokeWidth="1.5" />
      <text x="106" y="66" textAnchor="middle" fontSize="13" fontWeight="600" className="fill-foreground">
        Your AI client
      </text>
      <text x="106" y="86" textAnchor="middle" fontSize="10.5" className="fill-muted-foreground font-mono">
        Claude Code · Codex · VS Code
      </text>
      <rect x="42" y="100" width="128" height="22" rx="11" className="fill-info/15 stroke-info/40" strokeWidth="1" />
      <text x="106" y="115" textAnchor="middle" fontSize="10.5" fontWeight="600" className="fill-info font-mono">
        X-API-Key
      </text>

      {/* Node B — the transport, deliberately NOT the boundary (dashed, muted) */}
      <rect
        x="262"
        y="40"
        width="196"
        height="96"
        rx="10"
        className="fill-accent/40 stroke-muted-foreground/50"
        strokeWidth="1.5"
        strokeDasharray="5 4"
      />
      <text x="360" y="66" textAnchor="middle" fontSize="13" fontWeight="600" className="fill-foreground">
        MCP transport
      </text>
      <text x="360" y="86" textAnchor="middle" fontSize="10.5" className="fill-muted-foreground font-mono">
        /api/v1/mcp
      </text>
      <text x="360" y="108" textAnchor="middle" fontSize="10" className="fill-muted-foreground">
        maps tool → endpoint
      </text>
      <text x="360" y="122" textAnchor="middle" fontSize="10" className="fill-muted-foreground">
        forwards key · decides nothing
      </text>

      {/* Node C — the endpoint, the real boundary (emphasised) */}
      <rect x="516" y="40" width="196" height="96" rx="10" className="fill-card stroke-warning" strokeWidth="2" />
      {/* shield glyph */}
      <path
        d="M540 52 l10 -4 l10 4 v6 c0 7 -4 12 -10 15 c-6 -3 -10 -8 -10 -15 z"
        className="fill-warning/20 stroke-warning"
        strokeWidth="1.2"
      />
      <text x="618" y="60" textAnchor="middle" fontSize="13" fontWeight="600" className="fill-foreground">
        Agent endpoint
      </text>
      <text x="614" y="78" textAnchor="middle" fontSize="10.5" className="fill-muted-foreground font-mono">
        /api/v1/agent/*
      </text>
      <text x="614" y="100" textAnchor="middle" fontSize="10" fontWeight="600" className="fill-warning">
        the real boundary
      </text>
      <text x="614" y="122" textAnchor="middle" fontSize="10" className="fill-muted-foreground">
        scope · operator role · audit
      </text>

      {/* Flow arrows */}
      <line x1="206" y1="88" x2="258" y2="88" className="stroke-muted-foreground" strokeWidth="1.6" markerEnd="url(#mcpflow-arrow)" />
      <text x="232" y="80" textAnchor="middle" fontSize="9.5" className="fill-muted-foreground font-mono">
        tools/call
      </text>
      <line x1="460" y1="88" x2="512" y2="88" className="stroke-muted-foreground" strokeWidth="1.6" markerEnd="url(#mcpflow-arrow)" />
      <text x="486" y="80" textAnchor="middle" fontSize="9.5" className="fill-muted-foreground">
        in-process
      </text>

      {/* Under-bracket spanning transport + endpoint */}
      <path d="M262 150 v8 h450 v-8" className="fill-none stroke-border" strokeWidth="1.2" />
      <text x="487" y="176" textAnchor="middle" fontSize="10.5" className="fill-muted-foreground">
        same key, same checks, same audit row as a curl to the endpoint
      </text>
      <text x="487" y="192" textAnchor="middle" fontSize="10.5" fontWeight="600" className="fill-foreground">
        MCP changes how an agent connects — never what it may do
      </text>
    </svg>
  </div>
);

export default McpFlowDiagram;
