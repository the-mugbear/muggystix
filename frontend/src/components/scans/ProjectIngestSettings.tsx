/**
 * Project settings → Imports (UX review 2026-09-24).
 *
 * "Skip informational Nessus findings" was a switch inside the upload dialog
 * that changed a PROJECT setting for every later upload, by anyone — a side
 * effect nobody expects from a dialog about the files at hand, and worded as
 * "findings" for what are scanner observations. It lives here now; the upload
 * dialog states it in one line and links here.
 */
import React, { useEffect, useRef } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { useLocation } from 'react-router-dom';

import { updateProjectIngestSettings } from '../../services/api';
import { useProject } from '../../contexts/ProjectContext';
import { useToast } from '../../contexts/ToastContext';
import { GLOBAL, invalidateReads } from '../../lib/query';
import { formatApiError } from '../../utils/apiErrors';
import PostureSection from '../posture/PostureSection';
import { Label } from '../ui/label';
import { Switch } from '../ui/switch';

/** The anchor the upload dialog links to. */
export const IMPORT_SETTINGS_ANCHOR = 'imports';

export const ProjectIngestSettings: React.FC<{ canEdit: boolean }> = ({ canEdit }) => {
  const { currentProject } = useProject();
  const queryClient = useQueryClient();
  const toast = useToast();
  const save = useMutation({
    mutationFn: ({ projectId, next }: { projectId: number; next: boolean }) =>
      updateProjectIngestSettings(projectId, { skip_informational_findings: next }),
    // The setting is shown from the project (`skip_informational_effective`),
    // so the project list is read again — in place, the page stays on screen
    // — before the save is said to be done.
    onSuccess: async (updated, { next }) => {
      // The server's answer first: a failed re-read must not flip the switch back.
      queryClient.setQueryData<Array<{ id: number }>>([GLOBAL, 'getProjects'], (list) => (
        list?.map((p) => (p.id === updated.id ? { ...p, ...updated } : p))
      ));
      await invalidateReads(queryClient, 'getProjects');
      toast.success(next
        ? 'Informational Nessus observations will be skipped on later uploads.'
        : 'Informational Nessus observations will be kept on later uploads.');
    },
    onError: (err) => toast.error(formatApiError(err, 'Could not save the import setting.')),
  });
  // The upload dialog's link lands here (`#imports`); the router does not
  // scroll to a hash by itself.
  const { hash } = useLocation();
  const anchorRef = useRef<HTMLDivElement | null>(null);
  const hasProject = currentProject != null;
  useEffect(() => {
    if (hasProject && hash === `#${IMPORT_SETTINGS_ANCHOR}`) anchorRef.current?.scrollIntoView?.({ block: 'start' });
  }, [hash, hasProject]);
  if (!currentProject) return null;
  const saving = save.isPending;
  // While the save is in flight the switch shows what was asked for.
  const effective = (saving ? save.variables?.next : undefined) ?? currentProject.skip_informational_effective ?? false;
  const change = (next: boolean) => save.mutate({ projectId: currentProject.id, next });

  return (
    <div id={IMPORT_SETTINGS_ANCHOR} ref={anchorRef} className="scroll-mt-24">
      <PostureSection title="Imports" description="How uploads into this project are read. A change applies to files uploaded after it.">
        <div className="flex min-w-0 items-start justify-between gap-md">
          <div className="min-w-0">
            <Label htmlFor="skip-informational" className="text-metadata font-semibold">
              Skip informational Nessus scanner observations
            </Label>
            <p className="text-caption text-muted-foreground">
              Severity-0 plugins are not stored as scanner observations; the open ports they report are still
              recorded. Each file keeps the setting it was uploaded under.
            </p>
          </div>
          <Switch
            id="skip-informational"
            checked={effective}
            onCheckedChange={(v) => change(v === true)}
            disabled={!canEdit || saving}
            aria-label="Skip informational Nessus scanner observations"
          />
        </div>
      </PostureSection>
    </div>
  );
};

export default ProjectIngestSettings;
