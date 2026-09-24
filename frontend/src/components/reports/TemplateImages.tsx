/**
 * The images a report template expects besides the findings' evidence — a
 * logo, cover art — as declared in its template.json, each marked installed
 * or missing, so an operator can put the branding in place before generating
 * a report instead of discovering it in the rendered file.
 *
 * A REQUIRED image that is missing blocks preview, issue and render (the
 * server refuses too); an optional one is simply left out of the layout.
 * The files live on the server in `report-templates/<name>/`, mounted
 * read-only into the backend and report worker — adding one needs no rebuild.
 */
import React from 'react';
import { Link } from 'react-router-dom';

import type { ClientReportFormat, ReportTemplate, ReportTemplateAsset } from '../../services/api';
import { Badge } from '../ui/badge';

const FORMAT_LABEL: Record<ClientReportFormat, string> = { html: 'HTML', docx: 'Word', pdf: 'PDF', qmd: 'QMD source' };

/** The declared, required images whose file is not installed. */
export const missingRequiredAssets = (template: ReportTemplate | undefined): ReportTemplateAsset[] =>
  (template?.assets ?? []).filter((a) => a.required && !a.present);

/** The optional files that are not installed and have no shipped fallback —
 *  left out of the layout, never a block. */
const optionalNotInstalled = (template: ReportTemplate | undefined): ReportTemplateAsset[] =>
  (template?.assets ?? []).filter((a) => !a.present && !a.required && !a.replaces);

/** The count beside a "Template files" heading. Only a required file is
 *  "missing" (it blocks rendering); an optional one is "not installed" — an
 *  optional logo read as a problem when both were counted as missing. A
 *  replacement that falls back to the shipped file is not counted at all. */
export const assetCountLabel = (template: ReportTemplate | undefined): string | null => {
  const required = missingRequiredAssets(template).length;
  if (required) return `${required} required missing`;
  const optional = optionalNotInstalled(template).length;
  return optional ? `${optional} optional not installed` : null;
};

/** One sentence naming what blocks a render, for a disabled button's title. */
export const missingAssetsReason = (template: ReportTemplate | undefined): string | undefined => {
  const missing = missingRequiredAssets(template);
  if (!missing.length || !template) return undefined;
  return `The template needs ${missing.map((a) => a.label).join(', ')} — see Template files`;
};

const status = (a: ReportTemplateAsset) => {
  if (a.present) return <Badge variant="success">Installed</Badge>;
  if (a.required) return <Badge variant="destructive">Missing · required</Badge>;
  // A replacing file that is absent is not a gap: the shipped one is used.
  if (a.replaces) return <Badge variant="outline">Not installed · shipped used</Badge>;
  // "Not installed", as the section's count says (v5.288.0): "Missing"
  // is kept for the required files that block a render.
  return <Badge variant="outline">Not installed · optional</Badge>;
};

export interface TemplateImagesProps {
  /** The template as listed by the server; undefined while loading or when unknown. */
  template: ReportTemplate | undefined;
  /** The template's folder name — shown even when it is not listed. */
  templateName: string | null;
  /** Server paths and install instructions — only for someone who can put
   *  files on the server (a global admin); everyone else sees the status. */
  showServerPaths?: boolean;
}

/**
 * One line for a draft when nothing blocks rendering: which template files
 * are not installed and what happens instead. The full list is on the
 * Reports page; a draft shows it only when a required file is missing.
 */
export const TemplateFilesLine: React.FC<{ template: ReportTemplate | undefined }> = ({ template }) => {
  const assets = template?.assets ?? [];
  if (!template || !assets.length) return null;
  const absent = assets.filter((a) => !a.present);
  return (
    <p className="break-words text-caption text-muted-foreground">
      {absent.length === 0
        ? `All ${assets.length} template file${assets.length === 1 ? '' : 's'} installed.`
        : absent.map((a) => `${a.label}: ${a.replaces ? 'not installed, the shipped file is used' : 'not installed (optional, left out)'}`).join(' · ')}
      {' '}
      <Link to="/reports" className="text-info hover:underline">Details on the Reports page</Link>
    </p>
  );
};

const TemplateImages: React.FC<TemplateImagesProps> = ({ template, templateName, showServerPaths = false }) => {
  if (!templateName) {
    return <p className="text-caption text-muted-foreground">No template chosen.</p>;
  }
  if (!template) {
    return (
      <p className="break-words text-caption text-muted-foreground">
        The template “{templateName}” is not installed, so its images cannot be checked.
      </p>
    );
  }
  const assets = template.assets ?? [];
  if (!assets.length) {
    return (
      <p className="text-caption text-muted-foreground">
        This template uses no images of its own — only the findings&apos; evidence.
      </p>
    );
  }
  const folder = `report-templates/${template.name}/`;
  return (
    <div className="space-y-sm">
      <ul className="divide-y divide-border">
        {assets.map((a) => (
          <li key={a.id} className="flex min-w-0 flex-col gap-xxs py-xs first:pt-0 sm:flex-row sm:items-baseline sm:gap-md">
            {/* Wide enough for the longest status ("Not installed · shipped
                used") on one line. */}
            <div className="w-56 shrink-0 whitespace-nowrap">{status(a)}</div>
            <div className="min-w-0 flex-1 space-y-xxs">
              <div className="flex min-w-0 flex-wrap items-baseline gap-x-xs">
                <span className="break-words font-medium">{a.label}</span>
                {showServerPaths && (
                  <span className="min-w-0 max-w-full truncate font-mono text-caption text-muted-foreground" title={folder + a.path}>
                    {folder}{a.path}
                  </span>
                )}
              </div>
              {a.replaces && (
                <p className="break-words text-caption text-muted-foreground">
                  {a.present ? 'Used' : 'When installed, used'} in place of the template&apos;s own{' '}
                  <span className="font-mono">{a.replaces}</span>.
                </p>
              )}
              {a.description && <p className="break-words text-caption text-muted-foreground">{a.description}</p>}
              {a.note && <p className="break-words text-caption text-muted-foreground">{a.note}</p>}
              {a.formats.length > 0 && (
                <p className="text-caption text-muted-foreground">
                  Used in {a.formats.map((f) => FORMAT_LABEL[f] ?? f).join(', ')}
                </p>
              )}
            </div>
          </li>
        ))}
      </ul>
      <p className="max-w-3xl text-caption text-muted-foreground">
        {showServerPaths
          ? <>Put each file at its path on the server. The template folder is mounted read-only into the backend and the report
            worker, so no rebuild is needed — reload this page to check again. </>
          : <>An administrator installs these files on the server. </>}
        A missing optional image is left out of the layout; a file that replaces one of the template&apos;s own falls back
        to the shipped one.
      </p>
    </div>
  );
};

export default TemplateImages;
