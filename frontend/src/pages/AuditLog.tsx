/**
 * Administration → Audit log (5.350.0): the deployment's audit trail on its
 * own page.  It was the last section of System settings, under the user
 * table — a long list below a long list, on a page about settings.
 */
import React from 'react';

import AuditLogViewer from '../components/AuditLogViewer';

const AuditLog: React.FC = () => (
  <div className="space-y-lg p-md md:p-lg">
    <header className="min-w-0">
      <h1 className="text-page-title">Audit log</h1>
    </header>
    <AuditLogViewer />
  </div>
);

export default AuditLog;
