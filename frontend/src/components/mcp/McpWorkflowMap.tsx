/**
 * McpWorkflowMap — the four workflows, and where each sits in an engagement.
 *
 * The single most load-bearing fact about this surface is that a key belongs
 * to ONE workflow and sees only that workflow's tools — so "which key do I
 * want?" is the first question, and the answer is a place in the engagement:
 * recon feeds planning, planning feeds execution, and assist reads across all
 * of it at any time. A left-to-right pipeline with an assist band under it
 * says that faster than the prose can.
 *
 * Tool counts are passed in from the live catalog so the picture can't claim a
 * capability the deployment doesn't serve. Theme-aware via fill-/stroke-
 * utilities; desktop-only, so it scrolls rather than reflows.
 */
import React from 'react';

interface WorkflowNode {
  key: string;
  title: string;
  lines: string[];
  /** Tailwind tone classes for the accent bar + count pill. */
  bar: string;
  pillFill: string;
  pillText: string;
}

const NODES: WorkflowNode[] = [
  {
    key: 'recon',
    title: 'Reconnaissance',
    lines: ['Run scanners locally,', 'populate host data'],
    bar: 'fill-info',
    pillFill: 'fill-info/15',
    pillText: 'fill-info',
  },
  {
    key: 'plan_generation',
    title: 'Plan generation',
    lines: ['Propose tests,', 'hand to a human'],
    bar: 'fill-primary',
    pillFill: 'fill-primary/15',
    pillText: 'fill-primary',
  },
  {
    key: 'execution',
    title: 'Execution',
    lines: ['Work the approved plan,', 'record each result'],
    bar: 'fill-warning',
    pillFill: 'fill-warning/15',
    pillText: 'fill-warning',
  },
];

const NODE_W = 212;
const XS = [12, 256, 500];

interface Props {
  /** Live tool count per workflow key; a missing/zero count hides the pill. */
  counts?: Record<string, number>;
}

const countLabel = (n?: number) => (n && n > 0 ? `${n} tool${n === 1 ? '' : 's'}` : null);

const McpWorkflowMap: React.FC<Props> = ({ counts }) => (
  <div className="overflow-x-auto">
    <svg
      viewBox="0 0 724 236"
      role="img"
      aria-label="The engagement runs recon, then plan generation, then execution as a left-to-right pipeline; the assist workflow reads across all of them at any time. A connecting key belongs to one workflow and sees only its tools."
      className="mx-auto block min-w-[660px] max-w-[724px]"
    >
      <defs>
        <marker
          id="mcpmap-arrow"
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

      {NODES.map((node, i) => {
        const x = XS[i];
        const cx = x + NODE_W / 2;
        const label = countLabel(counts?.[node.key]);
        return (
          <g key={node.key}>
            <rect x={x} y={20} width={NODE_W} height={100} rx={10} className="fill-card stroke-border" strokeWidth={1.5} />
            {/* tone accent bar down the left edge */}
            <rect x={x} y={20} width={5} height={100} rx={2.5} className={node.bar} />
            <text x={cx} y={48} textAnchor="middle" fontSize={13} fontWeight={600} className="fill-foreground">
              {node.title}
            </text>
            <text x={cx} y={70} textAnchor="middle" fontSize={10.5} className="fill-muted-foreground">
              {node.lines[0]}
            </text>
            <text x={cx} y={84} textAnchor="middle" fontSize={10.5} className="fill-muted-foreground">
              {node.lines[1]}
            </text>
            {label ? (
              <>
                <rect x={cx - 34} y={96} width={68} height={18} rx={9} className={node.pillFill} />
                <text x={cx} y={109} textAnchor="middle" fontSize={10} fontWeight={600} className={node.pillText}>
                  {label}
                </text>
              </>
            ) : null}
          </g>
        );
      })}

      {/* pipeline arrows */}
      <line x1={224} y1={70} x2={254} y2={70} className="stroke-muted-foreground" strokeWidth={1.6} markerEnd="url(#mcpmap-arrow)" />
      <line x1={468} y1={70} x2={498} y2={70} className="stroke-muted-foreground" strokeWidth={1.6} markerEnd="url(#mcpmap-arrow)" />

      {/* assist band spanning the pipeline */}
      <rect x={12} y={150} width={700} height={64} rx={10} className="fill-success/10 stroke-success/40" strokeWidth={1.5} />
      <rect x={12} y={150} width={5} height={64} rx={2.5} className="fill-success" />
      <text x={28} y={178} fontSize={13} fontWeight={600} className="fill-foreground">
        Assist
      </text>
      <text x={28} y={198} fontSize={10.5} className="fill-muted-foreground">
        Interactive read across the whole inventory, any time — plus three operator-granted writes.
      </text>
      {countLabel(counts?.assist) ? (
        <>
          <rect x={628} y={172} width={72} height={20} rx={10} className="fill-success/20" />
          <text x={664} y={186} textAnchor="middle" fontSize={10} fontWeight={600} className="fill-success">
            {countLabel(counts?.assist)}
          </text>
        </>
      ) : null}

      {/* connective from pipeline down to the assist band */}
      <line x1={362} y1={120} x2={362} y2={148} className="stroke-muted-foreground/50" strokeWidth={1.2} strokeDasharray="4 3" markerEnd="url(#mcpmap-arrow)" />
    </svg>
  </div>
);

export default McpWorkflowMap;
