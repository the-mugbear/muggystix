/**
 * HubRedirect — a hub path (/inventory, /workflows, /collaboration,
 * /settings) sends the user straight to a child page instead of rendering an
 * interim card-grid landing.  That landing was redundant: the secondary-nav
 * tab strip already lists the same children on every child page, so the
 * landing was a second copy of the tab strip and one extra click.
 *
 * Target selection: the hub's designated `defaultChildPath` when the user's
 * role can see it, otherwise the first role-visible child in manifest order.
 * Falls back to /operations if the role can see no child in this hub.
 *
 * Loop-safety: this is NOT the removed FRX·M5 auto-redirect (which lived in
 * the landing page and could bounce).  It redirects with `replace` (the dead
 * hub URL never lands in history) and only ever targets a DIFFERENT, real
 * flat child route (e.g. /inventory → /hosts), so it cannot ping-pong with
 * the hub path.
 */
import React from 'react';
import { Navigate } from 'react-router-dom';

import { useAuth } from '../contexts/AuthContext';
import { HUBS, HubId } from '../config/navigation';

const HubRedirect: React.FC<{ hubId: HubId }> = ({ hubId }) => {
  const { hasPermission } = useAuth();
  const hub = HUBS.find((h) => h.id === hubId);
  const visible = hub
    ? hub.children.filter((c) => hasPermission(c.requiredRole))
    : [];
  const target =
    visible.find((c) => c.path === hub?.defaultChildPath)?.path
    ?? visible[0]?.path
    ?? '/operations';
  return <Navigate to={target} replace />;
};

export default HubRedirect;
