/**
 * The catch-all route (v5.290.0).  An unknown URL — a removed page such as
 * /network-topology, a typo, an old bookmark — used to render nothing inside
 * the layout.  Now it says what was asked for and offers the two ways back.
 *
 * `projectsHomePath` is also where /projects redirects: global admins manage
 * every project at /settings/projects; everyone else sees theirs at /portfolio.
 */
import React from 'react';
import { Link, Navigate, useLocation } from 'react-router-dom';

import { useAuth } from '../contexts/AuthContext';

export const projectsHomePath = (isAdmin: boolean) => (isAdmin ? '/settings/projects' : '/portfolio');

/** /projects is an obvious URL to type; send it where the projects are. */
export const ProjectsRedirect: React.FC = () => {
  const { hasPermission } = useAuth();
  return <Navigate to={projectsHomePath(hasPermission('admin'))} replace />;
};

const NotFound: React.FC = () => {
  const { pathname, search } = useLocation();
  const { hasPermission } = useAuth();
  const isAdmin = hasPermission('admin');
  return (
    <div className="space-y-sm p-md md:p-lg">
      <h1 className="text-page-title">Page not found</h1>
      <p className="max-w-3xl text-metadata text-muted-foreground">
        There is no page at{' '}
        <code className="break-all font-mono text-foreground">{pathname}{search}</code>
        . It may have been removed or renamed.
      </p>
      <nav aria-label="Ways back" className="flex flex-wrap gap-x-md gap-y-xs text-metadata">
        <Link to="/operations" className="font-medium text-primary hover:underline">Go to Operations</Link>
        <Link to={projectsHomePath(isAdmin)} className="font-medium text-primary hover:underline">
          {isAdmin ? 'All projects' : 'Your projects (Portfolio)'}
        </Link>
      </nav>
    </div>
  );
};

export default NotFound;
