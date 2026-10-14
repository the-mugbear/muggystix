/**
 * "Project images…" — the images of the project's report text in a side
 * panel, said to be SHARED: the project's report defaults and every draft
 * place the same ones (owner, 2026-10-14; the report workspace keeps their
 * maintenance off the draft's own form).  Upload, caption and delete are the
 * images' own writes, outside the draft's Save; the panel says so.
 */
import React, { useState } from 'react';
import { Images } from 'lucide-react';

import type { ReportTextImagesState } from '../../../hooks/useReportTextImages';
import { Button } from '../../ui/button';
import {
  SideSheet, SideSheetBody, SideSheetContent, SideSheetHeader, SideSheetTitle,
} from '../../ui/side-sheet';
import ReportTextImages from '../ReportTextImages';

const ProjectImagesPanel: React.FC<{
  state: ReportTextImagesState;
  idPrefix: string;
}> = ({ state, idPrefix }) => {
  const [open, setOpen] = useState(false);
  const count = state.images.length;
  return (
    <>
      <Button type="button" variant="outline" size="sm" onClick={() => setOpen(true)}>
        <Images className="size-4" aria-hidden /> Project images…{count ? ` (${count})` : ''}
      </Button>
      <SideSheet open={open} onOpenChange={setOpen}>
        <SideSheetContent width="lg" overlay>
          <SideSheetHeader>
            <SideSheetTitle>Project images</SideSheetTitle>
            <p className="text-caption text-muted-foreground">
              Shared by the project’s report defaults and every draft. An upload, a caption or a delete here is
              saved at once, for all of them — not with a draft’s Save.
            </p>
          </SideSheetHeader>
          <SideSheetBody>
            <ReportTextImages idPrefix={idPrefix} state={state} />
          </SideSheetBody>
        </SideSheetContent>
      </SideSheet>
    </>
  );
};

export default ProjectImagesPanel;
