import React from 'react';
import { useNavigate } from 'react-router-dom';
import {
  BookOpen,
  Shield,
  KeyRound,
  Terminal,
  FileText,
  FileCode,
  Bot,
  Package,
} from 'lucide-react';
import type { LucideIcon } from 'lucide-react';
import { Card, CardContent } from '../components/ui/card';
import { Badge } from '../components/ui/badge';
import { cn } from '../utils/cn';

type ReferenceSection = {
  title: string;
  description: string;
  Icon: LucideIcon;
  /** Semantic palette token for the left-bar accent. */
  tone: 'primary' | 'secondary' | 'warning' | 'destructive' | 'success' | 'info' | 'muted';
  group: 'Guides' | 'Operational Reference' | 'API Documentation';
  path?: string;
  href?: string;
  /** URL to fetch + trigger as a file download. */
  download?: string;
};

const sections: ReferenceSection[] = [
  {
    title: 'User Guide',
    description: 'Complete walkthrough of BlueStick features, workflows, and best practices.',
    Icon: BookOpen,
    path: '/reference/user-guide',
    tone: 'info',
    group: 'Guides',
  },
  {
    title: 'Risk Assessment',
    description: 'How risk scores are calculated, severity levels explained, and port classifications.',
    Icon: Shield,
    path: '/risk-assessment',
    tone: 'warning',
    group: 'Operational Reference',
  },
  {
    title: 'Default Credentials',
    description: 'Searchable database of vendor default credentials for security testing.',
    Icon: KeyRound,
    path: '/default-credentials',
    tone: 'destructive',
    group: 'Operational Reference',
  },
  {
    title: 'Tool Reference',
    description: 'Curated catalog of pentesting tools organized by category with install commands.',
    Icon: Terminal,
    path: '/tool-reference',
    tone: 'success',
    group: 'Operational Reference',
  },
  {
    title: 'Software Bill of Materials',
    description:
      'Every backend Python and frontend npm package bundled with this build — for vulnerability triage.',
    Icon: Package,
    path: '/reference/sbom',
    tone: 'muted',
    group: 'Operational Reference',
  },
  {
    title: 'AI Agent Guide',
    description:
      'Download AGENTS.md for AI assistants. URLs are pre-configured for this deployment.',
    Icon: Bot,
    download: '/api/v1/agents-guide',
    tone: 'primary',
    group: 'Guides',
  },
  {
    title: 'Swagger UI',
    description: 'Interactive OpenAPI documentation for exploring and testing the API.',
    Icon: FileText,
    href: '/docs',
    tone: 'secondary',
    group: 'API Documentation',
  },
  {
    title: 'ReDoc',
    description:
      'Reference-style API documentation with the full schema and endpoint details.',
    Icon: FileCode,
    href: '/redoc',
    tone: 'info',
    group: 'API Documentation',
  },
];

const GROUPS: Array<ReferenceSection['group']> = [
  'Guides',
  'Operational Reference',
  'API Documentation',
];

const GROUP_DESCRIPTIONS: Record<ReferenceSection['group'], string> = {
  Guides: 'Documentation for people and agents using BlueStick day to day.',
  'Operational Reference': 'Reference material used during triage, validation, and planning work.',
  'API Documentation': 'Browsable API schemas and interactive endpoint documentation.',
};

// Tone → Tailwind border-color class.  We use the left-side accent
// border as a 4px stripe to visually group cards by category without
// loading the design with full background colour.
const TONE_ACCENT: Record<ReferenceSection['tone'], string> = {
  primary: 'border-l-primary',
  secondary: 'border-l-secondary',
  warning: 'border-l-warning',
  destructive: 'border-l-destructive',
  success: 'border-l-success',
  info: 'border-l-info',
  muted: 'border-l-muted-foreground',
};

const TONE_ICON: Record<ReferenceSection['tone'], string> = {
  primary: 'text-primary',
  secondary: 'text-secondary',
  warning: 'text-warning',
  destructive: 'text-destructive',
  success: 'text-success',
  info: 'text-info',
  muted: 'text-muted-foreground',
};

const Reference: React.FC = () => {
  const navigate = useNavigate();

  const handleAction = (section: ReferenceSection) => {
    if (section.path) {
      navigate(section.path);
      return;
    }
    if (section.download) {
      const a = document.createElement('a');
      a.href = section.download;
      a.download = 'AGENTS.md';
      document.body.appendChild(a);
      a.click();
      a.remove();
    }
  };

  return (
    <div className="p-md md:p-lg">
      <h1 className="mb-md text-page-title">Reference</h1>

      <div className="flex flex-col gap-lg">
        {GROUPS.map((group) => {
          const groupSections = sections.filter((s) => s.group === group);
          if (groupSections.length === 0) return null;

          return (
            <section key={group}>
              <div className="mb-sm flex flex-col gap-xs sm:flex-row sm:items-center sm:justify-between">
                <div>
                  <h2 className="text-subheading font-semibold">{group}</h2>
                  <p className="text-metadata text-muted-foreground">
                    {GROUP_DESCRIPTIONS[group]}
                  </p>
                </div>
                <Badge variant="outline">
                  {groupSections.length} item{groupSections.length === 1 ? '' : 's'}
                </Badge>
              </div>

              <div className="grid grid-cols-1 gap-md md:grid-cols-2">
                {groupSections.map((section) => {
                  const InternalNavigation = section.path || section.download;
                  const Icon = section.Icon;
                  const Wrapper: React.ElementType = section.href ? 'a' : 'button';
                  const wrapperProps = section.href
                    ? {
                        href: section.href,
                        target: '_blank',
                        rel: 'noreferrer',
                      }
                    : {
                        type: 'button',
                        onClick: () => handleAction(section),
                      };

                  return (
                    <Card
                      key={section.title}
                      className={cn(
                        'border-l-4 transition-colors hover:bg-accent/50 focus-within:bg-accent/50',
                        TONE_ACCENT[section.tone],
                      )}
                    >
                      <Wrapper
                        {...(wrapperProps as Record<string, unknown>)}
                        className={cn(
                          'block h-full w-full rounded-panel text-left focus:outline-none focus-visible:ring-2 focus-visible:ring-ring',
                        )}
                      >
                        <CardContent className="p-md pt-md">
                          <div className="flex items-start gap-md">
                            <Icon className={cn('size-8 shrink-0', TONE_ICON[section.tone])} aria-hidden />
                            <div className="min-w-0">
                              <h3 className="mb-xxs text-subheading font-semibold">{section.title}</h3>
                              <p className="text-metadata text-muted-foreground">
                                {section.description}
                              </p>
                              <Badge variant="outline" className="mt-sm">
                                {section.group}
                              </Badge>
                            </div>
                          </div>
                          {InternalNavigation ? null : null}
                        </CardContent>
                      </Wrapper>
                    </Card>
                  );
                })}
              </div>
            </section>
          );
        })}
      </div>
    </div>
  );
};

export default Reference;
