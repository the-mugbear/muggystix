/**
 * A scanner's references for an observation (URLs, advisories, CVE ids) — one
 * list, shown by the host inspector's observation rows and by the Scanner
 * observations inspector (5.404.0).  A URL is a link that opens a new tab;
 * anything else is text.  Each line truncates: a reference is unbounded.
 */
import React from 'react';

import { safeHttpHref } from '../../utils/safeHref';

export const ScannerReferences: React.FC<{ references: string[] }> = ({ references }) => {
  if (references.length === 0) return null;
  return (
    <div className="pt-xxs">
      <p className="text-caption font-semibold text-foreground">
        References ({references.length})
      </p>
      <ul className="space-y-0">
        {references.map((ref, i) => {
          // An http(s) URL is a link (`safeHttpHref`, the one test of that).
          const href = safeHttpHref(ref);
          // Only show the native tooltip when the value is likely to
          // truncate — 60 ≈ the column at its narrowest.
          const titleAttr = ref.length > 60 ? ref : undefined;
          return (
            <li key={`${i}-${ref}`} className="min-w-0">
              {href ? (
                <a
                  href={href}
                  target="_blank"
                  rel="noopener noreferrer"
                  title={titleAttr}
                  className="block truncate text-caption text-primary underline-offset-4 hover:underline"
                >
                  {ref}
                </a>
              ) : (
                <span title={titleAttr} className="block truncate text-caption text-muted-foreground">
                  {ref}
                </span>
              )}
            </li>
          );
        })}
      </ul>
    </div>
  );
};

export default ScannerReferences;
