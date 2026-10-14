/**
 * /reports/templates — the installation's report templates (the report
 * workspace, owner 2026-10-14: occasional configuration off the everyday
 * Reports page).  Every template installed on the server, the project's
 * default first, each with the files it uses besides the findings' evidence;
 * a global administrator uploads a template's own files (logo, Word styles)
 * here.  The PROJECT's choice of default template is a report default, on
 * /reports/defaults — the two permissions stay apart.
 */
import React from 'react';
import { useQuery } from '@tanstack/react-query';
import { Link } from 'react-router-dom';
import { ArrowLeft } from 'lucide-react';

import {
  getReportProfile, listReportTemplateProblems, listReportTemplates, type ReportTemplate, type ReportTemplateProblem,
} from '../services/api';
import { useAuth } from '../contexts/AuthContext';
import { useProjectId } from '../hooks/useProjectId';
import { queryErrorText } from '../lib/query';
import PostureSection, { SectionCount } from '../components/posture/PostureSection';
import TemplateImages, { assetCountLabel, missingRequiredAssets } from '../components/reports/TemplateImages';
import { DetailSkeleton } from '../components/PageSkeleton';
import { Badge } from '../components/ui/badge';
import { Button } from '../components/ui/button';

const FORMAT_LABEL: Record<string, string> = { html: 'HTML', docx: 'Word', qmd: 'QMD source' };
const NO_TEMPLATES: ReportTemplate[] = [];
const NO_PROBLEMS: ReportTemplateProblem[] = [];

const ReportTemplates: React.FC = () => {
  const projectId = useProjectId();
  const { user } = useAuth();
  const isAdmin = user?.role === 'admin';
  const templatesQuery = useQuery({
    queryKey: ['listReportTemplates', projectId], queryFn: ({ signal }) => listReportTemplates(projectId, signal),
  });
  const profileQuery = useQuery({
    queryKey: ['getReportProfile', projectId], queryFn: ({ signal }) => getReportProfile(projectId, signal),
  });
  // Only an administrator can put a folder on the server, so only they are
  // told which ones could not be offered.
  const problemsQuery = useQuery({
    queryKey: ['listReportTemplateProblems', projectId],
    queryFn: ({ signal }) => listReportTemplateProblems(projectId, signal),
    enabled: isAdmin,
  });
  const templates = templatesQuery.data ?? NO_TEMPLATES;
  const problems = problemsQuery.data ?? NO_PROBLEMS;
  const defaultName = profileQuery.data?.template ?? null;
  const error = queryErrorText(templatesQuery.error, 'Could not load the templates.');
  if (!templatesQuery.data && !error) return <DetailSkeleton />;
  const ordered = [...templates].sort((a, b) => Number(b.name === defaultName) - Number(a.name === defaultName));

  return (
    <div className="space-y-lg p-md md:p-lg">
      <div>
        <Button asChild variant="ghost" size="sm" className="mb-sm">
          <Link to="/reports"><ArrowLeft className="size-4" aria-hidden /> Reports</Link>
        </Button>
        <h1 className="text-page-title">Report templates</h1>
        <p className="mt-xxs max-w-3xl text-metadata text-muted-foreground">
          The templates installed on this server and their own files. The project’s default template is chosen in{' '}
          <Link to="/reports/defaults" className="text-info hover:underline">Report defaults</Link>.
        </p>
      </div>
      {error && <p className="text-caption text-destructive">{error}</p>}
      <PostureSection
        title={<>Templates<SectionCount>{templates.length} installed</SectionCount></>}
        description="Open a template for its own files (logo, Word styles…)."
      >
        {templates.length === 0 ? (
          <p className="text-caption text-destructive">No report templates are installed, so no report can be rendered.</p>
        ) : (
          <div className="divide-y divide-border">
            {ordered.map((t) => {
              const count = assetCountLabel(t);
              return (
                // Open when something missing blocks a report.
                <details key={t.name} open={missingRequiredAssets(t).length > 0} className="group py-sm first:pt-0">
                  <summary className="flex min-w-0 cursor-pointer flex-wrap items-baseline gap-x-xs gap-y-xxs">
                    <span className="break-words font-medium">{t.title}</span>
                    {t.name === defaultName && <Badge variant="info">Project default</Badge>}
                    <span className="text-caption text-muted-foreground">
                      {t.formats.map((f) => FORMAT_LABEL[f] ?? f).join(', ')}
                      {count && <> · {count}</>}
                    </span>
                  </summary>
                  <div className="mt-xs space-y-xs">
                    {t.description && <p className="max-w-3xl break-words text-caption text-muted-foreground">{t.description}</p>}
                    <TemplateImages template={t} templateName={t.name} showServerPaths={isAdmin} />
                  </div>
                </details>
              );
            })}
          </div>
        )}
        {/* A folder that is there but not offered used to vanish without a word. */}
        {isAdmin && problems.length > 0 && (
          <div className="mt-sm border-l-4 border-l-warning py-xxs pl-sm" role="status">
            <p className="text-caption font-medium text-foreground">
              {problems.length === 1 ? '1 folder is' : `${problems.length} folders are`} in{' '}
              <span className="font-mono">report-templates/</span> but not offered:
            </p>
            <ul className="mt-xxs space-y-xxs">
              {problems.map((p) => (
                <li key={p.name} className="min-w-0 break-words text-caption text-muted-foreground">
                  <span className="font-mono text-foreground">{p.name}</span> — {p.error}
                </li>
              ))}
            </ul>
          </div>
        )}
        {isAdmin && (
          <details className="mt-sm max-w-3xl text-caption text-muted-foreground">
            <summary className="cursor-pointer font-medium text-foreground">Add a template</summary>
            <ol className="mt-xs list-decimal space-y-xxs pl-lg">
              <li>
                Copy an existing template&apos;s folder on the server. <span className="font-mono">remediation-worklist</span>{' '}
                is the starter: the smallest <span className="font-mono">template.json</span>, no images, and a{' '}
                <span className="font-mono">report.qmd</span> whose comments explain each part.
              </li>
              <li>
                Rename the folder (lower-case letters, digits, <span className="font-mono">-</span> or{' '}
                <span className="font-mono">_</span>) and give <span className="font-mono">template.json</span> its own title and
                description — they are what people choose by here and on each draft.
              </li>
              <li>
                Edit <span className="font-mono">report.qmd</span> and render it with its{' '}
                <span className="font-mono">sample-data.json</span> (<span className="font-mono">make</span> in the folder)
                until it reads right.
              </li>
              <li>
                Put the folder in <span className="font-mono">report-templates/</span>. It is listed here on the next reload — no
                rebuild or restart. A folder with a mistake is listed above with what to fix.
              </li>
            </ol>
            <p className="mt-xs">
              The full guide — every <span className="font-mono">template.json</span> key, the data a template receives and the
              rules that keep written text from becoming template code — is{' '}
              <span className="font-mono">report-templates/README.md</span> in the BlueStick source.
            </p>
          </details>
        )}
      </PostureSection>
    </div>
  );
};

export default ReportTemplates;
