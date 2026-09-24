/**
 * Project settings → Imports (UX review 2026-09-24).
 *
 * "Skip informational Nessus findings" was a switch inside the upload dialog
 * that changed a PROJECT setting for every later upload, by anyone — a side
 * effect nobody expects from a dialog about the files at hand, and worded as
 * "findings" for what are scanner observations. It lives here now; the upload
 * dialog states it in one line and links here.
 */
import React, { useEffect, useRef, useState } from 'react';
import { useLocation } from 'react-router-dom';

import { updateProjectIngestSettings } from '../../services/api';
import { useProject } from '../../contexts/ProjectContext';
import { useToast } from '../../contexts/ToastContext';
import { formatApiError } from '../../utils/apiErrors';
import PostureSection from '../posture/PostureSection';
import { Label } from '../ui/label';
import { Switch } from '../ui/switch';

/** The anchor the upload dialog links to. */
export const IMPORT_SETTINGS_ANCHOR = 'imports';

export const ProjectIngestSettings: React.FC<{ canEdit: boolean }> = ({ canEdit }) => {
  const { currentProject, refreshProjects } = useProject();
  const toast = useToast();
  const [saving, setSaving] = useState(false);
  const [pending, setPending] = useState<boolean | null>(null);
  // The upload dialog's link lands here (`#imports`); the router does not
  // scroll to a hash by itself.
  const { hash } = useLocation();
  const anchorRef = useRef<HTMLDivElement | null>(null);
  const hasProject = currentProject != null;
  useEffect(() => {
    if (hasProject && hash === `#${IMPORT_SETTINGS_ANCHOR}`) anchorRef.current?.scrollIntoView?.({ block: 'start' });
  }, [hash, hasProject]);
  if (!currentProject) return null;
  const effective = pending ?? currentProject.skip_informational_effective ?? false;

  const change = async (next: boolean) => {
    setPending(next);
    setSaving(true);
    try {
      await updateProjectIngestSettings(currentProject.id, { skip_informational_findings: next });
      await refreshProjects();
      toast.success(next
        ? 'Informational Nessus observations will be skipped on later uploads.'
        : 'Informational Nessus observations will be kept on later uploads.');
    } catch (err) {
      toast.error(formatApiError(err, 'Could not save the import setting.'));
    } finally {
      setPending(null);
      setSaving(false);
    }
  };

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
            onCheckedChange={(v) => void change(v === true)}
            disabled={!canEdit || saving}
            aria-label="Skip informational Nessus scanner observations"
          />
        </div>
      </PostureSection>
    </div>
  );
};

export default ProjectIngestSettings;
