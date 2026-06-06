/**
 * Network topology (v2.75.0) — a react-flow canvas of the project's
 * scope → subnet hierarchy.  Bounded by design: the backend returns
 * subnet nodes with host counts (no host-level nodes), so the graph stays
 * legible on large estates.  Clicking a node deep-links into the filtered
 * Hosts page (or the scope detail).
 */
import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import ReactFlow, {
  Background,
  Controls,
  Handle,
  Position,
  type Edge,
  type Node,
  type NodeProps,
} from 'reactflow';
import 'reactflow/dist/style.css';
import { Loader2, RefreshCw } from 'lucide-react';
import { TopologyResponse, TopoNode as ApiTopoNode, getTopology } from '../services/api';
import { formatApiError } from '../utils/apiErrors';
import { Alert, AlertDescription } from '../components/ui/alert';
import { Button } from '../components/ui/button';
import { cn } from '../utils/cn';

type NodeData = {
  label: string;
  sub?: string;
  kind: string;
  boxClass: string;
  nav?: string;
  onActivate?: () => void;
  activateLabel?: string;
};

const TopoBox: React.FC<NodeProps<NodeData>> = ({ data }) => {
  const boxCls = cn(
    'min-w-[120px] max-w-[220px] rounded-control border px-sm py-xs text-center shadow-raised',
    data.boxClass,
  );
  const inner = (
    <>
      <Handle type="target" position={Position.Top} className="!bg-border" />
      <div className="truncate text-metadata font-medium" title={data.label}>{data.label}</div>
      {data.sub && <div className="text-caption opacity-80">{data.sub}</div>}
      <Handle type="source" position={Position.Bottom} className="!bg-border" />
    </>
  );
  // Navigable nodes are real <button>s — focusable, Enter/Space-activatable,
  // and named for assistive tech.  Non-navigable nodes (the project root)
  // stay plain content.  The whole box is the button so the click + focus
  // target matches the visual node.
  if (data.onActivate) {
    return (
      <button
        type="button"
        onClick={data.onActivate}
        aria-label={data.activateLabel || data.label}
        className={cn(
          boxCls,
          'block w-full cursor-pointer focus:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-1',
        )}
      >
        {inner}
      </button>
    );
  }
  return <div className={boxCls}>{inner}</div>;
};

const nodeTypes = { topo: TopoBox };

const boxClassFor = (n: ApiTopoNode): string => {
  if (n.type === 'project') return 'bg-primary text-primary-foreground border-primary';
  if (n.type === 'unscoped') return 'bg-muted border-border text-muted-foreground';
  if (n.type === 'subnet') {
    const crit = Number((n.meta as { critical_hosts?: number })?.critical_hosts ?? 0);
    return crit > 0 ? 'bg-card border-destructive text-foreground' : 'bg-card border-border text-foreground';
  }
  return 'bg-card border-border text-foreground'; // scope
};

const SCOPE_GAP = 260;
const SUBNET_GAP = 200;
// Subnets wrap into a bounded grid rather than one row — at the backend's
// 500-subnet cap a single row would be ~100,000px wide and unfittable.
const SUBNET_COLS = 12;
const SUBNET_ROW_H = 110;

/** Layered layout: project (row 0), scopes + unscoped (row 1), subnets (row 2). */
function layout(
  resp: TopologyResponse,
  navFor: (n: ApiTopoNode) => string | undefined,
  onNavigate: (nav: string) => void,
): { nodes: Node<NodeData>[]; edges: Edge[] } {
  const scopes = resp.nodes.filter((n) => n.type === 'scope');
  const unscoped = resp.nodes.filter((n) => n.type === 'unscoped');
  const subnets = resp.nodes.filter((n) => n.type === 'subnet');
  const row1 = [...scopes, ...unscoped];
  const row1Width = Math.max(row1.length, 1) * SCOPE_GAP;
  const subnetCols = Math.min(Math.max(subnets.length, 1), SUBNET_COLS);
  const row2Width = subnetCols * SUBNET_GAP;
  const canvasWidth = Math.max(row1Width, row2Width);

  const pos: Record<string, { x: number; y: number }> = {};
  pos['project'] = { x: canvasWidth / 2, y: 0 };
  row1.forEach((n, i) => {
    pos[n.id] = { x: (i + 0.5) * SCOPE_GAP + (canvasWidth - row1Width) / 2, y: 140 };
  });
  // Subnets cluster under their parent scope, ordered by the scope's
  // *displayed* column (row1 order), not by raw scope_id — otherwise, when
  // scope names and ids sort differently, subnet clusters drift away from
  // their scope node and edges cross needlessly. Tie-break by CIDR.
  const scopeDisplayOrder = new Map<number, number>();
  scopes.forEach((s, i) => {
    const sid = Number((s.meta as { scope_id?: number })?.scope_id ?? 0);
    scopeDisplayOrder.set(sid, i);
  });
  const subnetsByScope = [...subnets].sort((a, b) => {
    const sa = scopeDisplayOrder.get(Number((a.meta as { scope_id?: number })?.scope_id ?? 0)) ?? Number.MAX_SAFE_INTEGER;
    const sb = scopeDisplayOrder.get(Number((b.meta as { scope_id?: number })?.scope_id ?? 0)) ?? Number.MAX_SAFE_INTEGER;
    if (sa !== sb) return sa - sb;
    return (a.label || '').localeCompare(b.label || '');
  });
  subnetsByScope.forEach((n, i) => {
    const col = i % SUBNET_COLS;
    const rowIdx = Math.floor(i / SUBNET_COLS);
    pos[n.id] = {
      x: (col + 0.5) * SUBNET_GAP + (canvasWidth - row2Width) / 2,
      y: 300 + rowIdx * SUBNET_ROW_H,
    };
  });

  const nodes: Node<NodeData>[] = resp.nodes.map((n) => {
    const nav = navFor(n);
    const critical = Number((n.meta as { critical_hosts?: number })?.critical_hosts ?? 0);
    const sub =
      n.type === 'subnet' || n.type === 'unscoped'
        ? `${n.host_count.toLocaleString()} host${n.host_count === 1 ? '' : 's'}` +
          (critical > 0 ? ` · ${critical} critical` : '')
        : undefined;
    // Accessible name for navigable nodes — what + where activation goes.
    let activateLabel: string | undefined;
    if (nav && n.type === 'subnet') {
      activateLabel =
        `Open ${n.label} in Hosts — ${n.host_count} host${n.host_count === 1 ? '' : 's'}` +
        (critical > 0 ? `, ${critical} critical` : '');
    } else if (nav && n.type === 'unscoped') {
      activateLabel = `Open out-of-scope hosts — ${n.host_count} host${n.host_count === 1 ? '' : 's'}`;
    } else if (nav && n.type === 'scope') {
      activateLabel = `Open scope ${n.label}`;
    }
    return {
      id: n.id,
      type: 'topo',
      position: pos[n.id] ?? { x: 0, y: 0 },
      data: {
        label: n.label,
        sub,
        kind: n.type,
        boxClass: boxClassFor(n),
        nav,
        onActivate: nav ? () => onNavigate(nav) : undefined,
        activateLabel,
      },
    };
  });

  const edges: Edge[] = resp.edges.map((e) => ({
    id: e.id,
    source: e.source,
    target: e.target,
    type: 'smoothstep',
  }));

  return { nodes, edges };
}

const NetworkTopology: React.FC = () => {
  const navigate = useNavigate();
  const [resp, setResp] = useState<TopologyResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [nonce, setNonce] = useState(0);

  const navFor = useCallback((n: ApiTopoNode): string | undefined => {
    if (n.type === 'subnet') {
      const cidr = (n.meta as { cidr?: string })?.cidr;
      return cidr ? `/hosts?subnets=${encodeURIComponent(cidr)}` : undefined;
    }
    if (n.type === 'unscoped') return '/hosts?out_of_scope_only=true';
    if (n.type === 'scope') {
      const sid = (n.meta as { scope_id?: number })?.scope_id;
      return sid ? `/scopes/${sid}` : undefined;
    }
    return undefined;
  }, []);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    getTopology()
      .then((r) => {
        if (!cancelled) setResp(r);
      })
      .catch((err) => {
        if (!cancelled) setError(formatApiError(err, 'Failed to load topology.'));
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [nonce]);

  // Navigation is driven by the focusable <button> inside each node
  // (mouse + Enter/Space), so it's keyboard-accessible without a separate
  // mouse-only onNodeClick handler.
  const onNavigate = useCallback((nav: string) => navigate(nav), [navigate]);

  const { nodes, edges } = useMemo(
    () => (resp ? layout(resp, navFor, onNavigate) : { nodes: [], edges: [] }),
    [resp, navFor, onNavigate],
  );

  const isEmpty = !loading && resp && resp.nodes.length <= 1;

  return (
    <div className="flex h-full flex-col p-md md:p-lg">
      <div className="mb-md flex flex-wrap items-center gap-sm">
        <div className="min-w-0 flex-1">
          <h1 className="text-page-title font-semibold">Network Topology</h1>
          <p className="text-metadata text-muted-foreground">
            Scopes and subnets for this project, sized by host count. Click a subnet to open its
            hosts; red borders flag subnets with critical findings.
          </p>
        </div>
        <Button size="sm" variant="outline" onClick={() => setNonce((n) => n + 1)} disabled={loading}>
          <RefreshCw className={cn('size-4', loading && 'animate-spin')} aria-hidden /> Refresh
        </Button>
      </div>

      {error && (
        <Alert variant="destructive" className="mb-md">
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      )}

      {resp?.truncated && (
        <Alert className="mb-md">
          <AlertDescription>
            This project has a large number of subnets — the graph shows the 500 with the most
            hosts.
          </AlertDescription>
        </Alert>
      )}

      {loading ? (
        <div className="flex min-h-[480px] flex-1 items-center justify-center gap-xs rounded-panel border border-border text-muted-foreground">
          <Loader2 className="size-5 animate-spin" aria-hidden /> Loading topology…
        </div>
      ) : isEmpty ? (
        <div className="rounded-panel border border-border py-xl text-center text-muted-foreground">
          No scopes or subnets to map yet. Register a scope and run recon to populate the topology.
        </div>
      ) : (
        // Mount the canvas only once data is present — rendering ReactFlow
        // with an empty node set lets `fitView` fit nothing, leaving large
        // graphs off-screen when data arrives.  Gating on `loading` also
        // remounts (and thus re-fits) after a Refresh.
        <div className="min-h-[480px] flex-1 overflow-hidden rounded-panel border border-border">
          <ReactFlow
            nodes={nodes}
            edges={edges}
            nodeTypes={nodeTypes}
            fitView
            minZoom={0.1}
            nodesDraggable={false}
            nodesConnectable={false}
          >
            <Background />
            <Controls showInteractive={false} />
          </ReactFlow>
        </div>
      )}
    </div>
  );
};

export default NetworkTopology;
