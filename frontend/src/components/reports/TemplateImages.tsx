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
 *
 * v5.311.0 — a global administrator uploads them here instead (stored
 * outside the template folder; an upload wins over a server-installed file),
 * and every row says what the file must be: type, minimum pixels, shape and
 * size, from the template's own guidance.  The server checks the bytes; this
 * page checks type and size first so a wrong file fails before it is sent.
 */
import React, { useEffect, useRef, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Link } from 'react-router-dom';

import {
  fetchReportTemplateAssetPreview, removeReportTemplateAsset, uploadReportTemplateAsset,
} from '../../services/api';
import type { ClientReportFormat, ReportTemplate, ReportTemplateAsset } from '../../services/api';
import { Badge } from '../ui/badge';
import { Button } from '../ui/button';

const FORMAT_LABEL: Record<ClientReportFormat, string> = { html: 'HTML', docx: 'Word', qmd: 'QMD source' };

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

const KIND = {
  png: { label: 'PNG', accept: '.png,image/png', mimes: ['image/png'], exts: ['png'] },
  jpeg: { label: 'JPEG', accept: '.jpg,.jpeg,image/jpeg', mimes: ['image/jpeg'], exts: ['jpg', 'jpeg'] },
  docx: {
    label: 'Word document (.docx)', accept: '.docx',
    mimes: ['application/vnd.openxmlformats-officedocument.wordprocessingml.document'], exts: ['docx'],
  },
} as const;
type UploadKind = keyof typeof KIND;
const PREVIEWABLE = new Set(['png', 'jpeg', 'gif', 'webp']);

const megabytes = (bytes: number) => {
  const mb = bytes / 1048576;
  return mb >= 1 ? `${Number.isInteger(mb) ? mb : mb.toFixed(1)} MB` : `${Math.max(1, Math.round(bytes / 1024))} KB`;
};

/** One line saying what the file must be, from the template's guidance
 *  ("PNG · at least 400 × 100 px · shaped 3.4:1 · up to 2 MB"). */
export const assetGuidance = (a: ReportTemplateAsset): string => {
  const kind = (a.kind ?? '') as UploadKind;
  if (!a.uploadable || !(kind in KIND)) {
    return a.kind
      ? `${a.kind.toUpperCase()} — installed on the server by an administrator`
      : 'Installed on the server by an administrator';
  }
  const parts: string[] = [KIND[kind].label];
  if (a.min_width || a.min_height) parts.push(`at least ${a.min_width ?? 1} × ${a.min_height ?? 1} px`);
  if (a.aspect) parts.push(`shaped ${a.aspect}`);
  if (a.max_bytes) parts.push(`up to ${megabytes(a.max_bytes)}`);
  return parts.join(' · ');
};

/** Why a chosen file cannot be this asset (type or size), or null — checked
 *  before it is sent; the server checks the bytes, pixels and shape. */
export const assetFileProblem = (a: ReportTemplateAsset, file: File): string | null => {
  const kind = (a.kind ?? '') as UploadKind;
  if (!(kind in KIND)) return `${a.label} cannot be uploaded here.`;
  const spec = KIND[kind];
  const ext = file.name.includes('.') ? file.name.split('.').pop()!.toLowerCase() : '';
  if (!(spec.exts as readonly string[]).includes(ext) && !(spec.mimes as readonly string[]).includes(file.type)) {
    return `${a.label} must be a ${spec.label}; “${file.name}” is not.`;
  }
  if (a.max_bytes && file.size > a.max_bytes) {
    return `“${file.name}” is ${megabytes(file.size)}; ${a.label} can be at most ${megabytes(a.max_bytes)}.`;
  }
  if (file.size === 0) return `“${file.name}” is empty.`;
  return null;
};

const errorDetail = (err: unknown): string => {
  const detail = (err as { response?: { data?: { detail?: unknown } } })?.response?.data?.detail;
  if (typeof detail === 'string') return detail;
  // A request the server could not read (FastAPI's validation list).
  if (Array.isArray(detail)) {
    const msgs = detail.map((d) => (d as { msg?: string })?.msg).filter(Boolean);
    if (msgs.length) return `The file could not be uploaded: ${msgs.join('; ')}.`;
  }
  return 'The file could not be uploaded.';
};

const uploadLine = (a: ReportTemplateAsset): string | null => {
  const u = a.upload;
  if (!u) return null;
  const bits = [
    u.uploaded_by ? `Uploaded by ${u.uploaded_by}` : 'Uploaded',
    u.uploaded_at ? `on ${new Date(u.uploaded_at).toLocaleDateString()}` : null,
  ].filter(Boolean).join(' ');
  const facts = [
    u.width && u.height ? `${u.width} × ${u.height} px` : null,
    u.size ? megabytes(u.size) : null,
  ].filter(Boolean).join(', ');
  return facts ? `${bits} · ${facts}` : bits;
};

/** The image the render would use, fetched with the session (an <img> cannot
 *  send the token).  Refetched whenever the file changes. */
const AssetThumbnail: React.FC<{ templateName: string; asset: ReportTemplateAsset }> = ({ templateName, asset }) => {
  const version = asset.upload?.sha256 ?? (asset.installed ? 'installed' : '');
  // `version` is in the key for the file it names, not for the request.
  const { data: blob } = useQuery({
    queryKey: ['fetchReportTemplateAssetPreview', templateName, asset.id, version],
    queryFn: () => fetchReportTemplateAssetPreview(templateName, asset.id),
    enabled: asset.present && PREVIEWABLE.has(asset.kind ?? ''),
  });
  // The object URL lives as long as its image is the one shown.
  const [url, setUrl] = useState<string | null>(null);
  useEffect(() => {
    if (!blob) { setUrl(null); return undefined; }
    const objectUrl = URL.createObjectURL(blob);
    setUrl(objectUrl);
    return () => URL.revokeObjectURL(objectUrl);
  }, [blob]);
  if (!url) return null;
  return (
    <img
      src={url}
      alt={`${asset.label} as it will be used`}
      className="max-h-16 max-w-[12rem] rounded border border-border bg-muted object-contain p-xxs"
    />
  );
};

/** Upload / replace / remove one template file (global administrators). */
const AssetUpload: React.FC<{ templateName: string; asset: ReportTemplateAsset }> = ({ templateName, asset }) => {
  const queryClient = useQueryClient();
  const input = useRef<HTMLInputElement>(null);
  // A file refused here, before it is sent.
  const [refused, setRefused] = useState<string | null>(null);
  // One change at a time, upload or removal; the server answers with the
  // template as it now stands, which goes where the template list is read.
  const change = useMutation({
    mutationFn: (file: File | null) => (file
      ? uploadReportTemplateAsset(templateName, asset.id, file)
      : removeReportTemplateAsset(templateName, asset.id)),
    onSuccess: ({ template }) => {
      queryClient.setQueryData<ReportTemplate[]>(['listReportTemplates'],
        (all) => all?.map((x) => (x.name === template.name ? template : x)));
    },
  });
  const busy = change.isPending;
  const error = refused ?? (change.error ? errorDetail(change.error) : null);
  // An upload's warnings; a removal has none to show.
  const warnings = change.isSuccess && change.variables ? change.data.warnings ?? [] : [];
  const kind = (asset.kind ?? '') as UploadKind;
  if (!asset.uploadable || !(kind in KIND)) return null;

  const choose = (file: File | undefined) => {
    if (input.current) input.current.value = '';
    if (!file) return;
    const problem = assetFileProblem(asset, file);
    setRefused(problem);
    if (problem) { change.reset(); return; }
    change.mutate(file);
  };

  const remove = () => {
    setRefused(null);
    change.mutate(null);
  };

  return (
    <div className="space-y-xxs">
      <div className="flex flex-wrap items-center gap-xs">
        <input
          ref={input}
          type="file"
          accept={KIND[kind].accept}
          className="hidden"
          aria-label={`Upload ${asset.label}`}
          onChange={(e) => choose(e.target.files?.[0])}
        />
        <Button size="sm" variant="outline" disabled={busy} onClick={() => input.current?.click()}>
          {busy ? 'Working…' : asset.source === 'uploaded' ? 'Replace' : 'Upload'}
        </Button>
        {asset.source === 'uploaded' && (
          <Button size="sm" variant="ghost" disabled={busy} onClick={remove}>
            Remove upload
          </Button>
        )}
      </div>
      {error && <p role="alert" className="break-words text-caption text-destructive">{error}</p>}
      {warnings.map((w) => <p key={w} className="break-words text-caption text-warning">{w}</p>)}
    </div>
  );
};

const status = (a: ReportTemplateAsset) => {
  if (a.present && a.source === 'uploaded') return <Badge variant="success">Uploaded</Badge>;
  if (a.present) return <Badge variant="success">Installed on server</Badge>;
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
  /** Server paths and install instructions, and upload / replace / remove
   *  (v5.311.0) — only for someone who can put files on the server (a global
   *  admin); everyone else sees the status. */
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

const TemplateImages: React.FC<TemplateImagesProps> = ({
  template, templateName, showServerPaths = false,
}) => {
  const canUpload = showServerPaths;
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
              {/* v5.294.0 (UX review) — the server path is not part of the
                  reading text: it is on the label's tooltip, and listed once
                  under "Where the files go" for the administrator. */}
              <span className="break-words font-medium" title={showServerPaths ? folder + a.path : undefined}>
                {a.label}
              </span>
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
              <p className="break-words text-caption text-foreground" data-testid={`asset-guidance-${a.id}`}>
                {assetGuidance(a)}
              </p>
              {uploadLine(a) && (
                <p className="truncate text-caption text-muted-foreground" title={a.upload?.original_filename ?? undefined}>
                  {uploadLine(a)}
                  {a.upload?.original_filename && <> · {a.upload.original_filename}</>}
                </p>
              )}
              {a.source === 'uploaded' && a.installed && (
                <p className="break-words text-caption text-muted-foreground">
                  Used instead of the file installed on the server; remove the upload to go back to it.
                </p>
              )}
              <AssetThumbnail templateName={template.name} asset={a} />
              {canUpload && <AssetUpload templateName={template.name} asset={a} />}
            </div>
          </li>
        ))}
      </ul>
      <p className="max-w-3xl text-caption text-muted-foreground">
        {!showServerPaths && <>A global administrator uploads these files or installs them on the server. </>}
        {canUpload && <>Files uploaded here are used by every project&apos;s reports from the next preview or issue on;
          reports already issued keep theirs. </>}
        A missing optional image is left out of the layout; a file that replaces one of the template&apos;s own falls back
        to the shipped one.
      </p>
      {showServerPaths && (
        <details className="max-w-3xl text-caption text-muted-foreground">
          <summary className="cursor-pointer text-foreground">Or install them on the server</summary>
          <ul className="mt-xxs space-y-xxs">
            {assets.map((a) => (
              <li key={a.id} className="flex min-w-0 flex-wrap gap-x-xs">
                <span className="shrink-0">{a.label}:</span>
                <span className="min-w-0 max-w-full truncate font-mono" title={folder + a.path}>{folder}{a.path}</span>
              </li>
            ))}
          </ul>
          <p className="mt-xxs">
            Put each file at its path. The template folder is mounted read-only into the backend and the report worker,
            so no rebuild is needed — reload this page to check again.
          </p>
        </details>
      )}
    </div>
  );
};

export default TemplateImages;
