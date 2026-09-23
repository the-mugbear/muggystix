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

import type { ClientReportFormat, ReportTemplate, ReportTemplateAsset } from '../../services/api';
import { Badge } from '../ui/badge';

const FORMAT_LABEL: Record<ClientReportFormat, string> = { html: 'HTML', docx: 'Word', pdf: 'PDF' };

/** The declared, required images whose file is not installed. */
export const missingRequiredAssets = (template: ReportTemplate | undefined): ReportTemplateAsset[] =>
  (template?.assets ?? []).filter((a) => a.required && !a.present);

/** How many declared files are gaps: not installed, and not a replacement that
 *  simply falls back to the template's shipped file. */
export const missingAssetCount = (template: ReportTemplate | undefined): number =>
  (template?.assets ?? []).filter((a) => !a.present && !a.replaces).length;

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
  return <Badge variant="outline">Missing · optional</Badge>;
};

export interface TemplateImagesProps {
  /** The template as listed by the server; undefined while loading or when unknown. */
  template: ReportTemplate | undefined;
  /** The template's folder name — shown even when it is not listed. */
  templateName: string | null;
}

const TemplateImages: React.FC<TemplateImagesProps> = ({ template, templateName }) => {
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
            <div className="w-40 shrink-0">{status(a)}</div>
            <div className="min-w-0 flex-1 space-y-xxs">
              <div className="flex min-w-0 flex-wrap items-baseline gap-x-xs">
                <span className="break-words font-medium">{a.label}</span>
                <span className="min-w-0 max-w-full truncate font-mono text-caption text-muted-foreground" title={folder + a.path}>
                  {folder}{a.path}
                </span>
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
        Put each file at its path on the server. The template folder is mounted read-only into the backend and the report
        worker, so no rebuild is needed — reload this page to check again. A missing optional image is left out of the layout;
        a file that replaces one of the template&apos;s own falls back to the shipped one.
      </p>
    </div>
  );
};

export default TemplateImages;
