import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { Loader2, Pencil, Plus, Trash2, X as CloseIcon } from 'lucide-react';

import {
  listSubnetLabels,
  createSubnetLabel,
  updateSubnetLabel,
  deleteSubnetLabel,
  replaceSubnetLabels,
  SubnetLabelWithCounts,
  SubnetLabelInfo,
} from '../services/api';
import { useToast } from '../contexts/ToastContext';
import { useConfirm } from '../hooks/useConfirm';
import { formatApiError } from '../utils/apiErrors';

import { Badge } from './ui/badge';
import { Button } from './ui/button';
import { Combobox } from './ui/combobox';
import { Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle } from './ui/dialog';
import { Input } from './ui/input';
import { Label } from './ui/label';
import { Popover, PopoverContent, PopoverTrigger } from './ui/popover';
import { Tooltip, TooltipContent, TooltipTrigger } from './ui/tooltip';

/**
 * Subnet-label management (v2.86.0).
 *
 * Exports two surfaces:
 *   - {@link SubnetLabelManagerDialog}: project-wide label CRUD invoked
 *     from the Scope detail page's "Manage labels" button.
 *   - {@link SubnetLabelEditorPopover}: per-subnet inline editor — a
 *     popover containing a multi-select combobox that PUTs the desired
 *     label set on close.
 *
 * Kept in one file so the colour-palette constants and the
 * label-fetching pattern are shared without a separate utility file
 * (we mirror the host-tag flavour deliberately rather than creating a
 * cross-cutting `labelColor` module that would tie host tags and
 * subnet labels together — see plan vocabulary decision A).
 */

// --- Colour palette -------------------------------------------------------

// Palette keys + their badge dot classes.  Identical to the host-tag
// palette (useHostColumns.tsx) so an operator who has internalised "red
// = critical" carries that intuition across both vocabularies.  Unknown
// / null colours fall back to a neutral dot.
const PALETTE: Array<{ key: string; dotClass: string; label: string }> = [
  { key: 'red',    dotClass: 'bg-destructive',         label: 'Red'    },
  { key: 'orange', dotClass: 'bg-warning',             label: 'Orange' },
  { key: 'yellow', dotClass: 'bg-warning',             label: 'Yellow' },
  { key: 'green',  dotClass: 'bg-success',             label: 'Green'  },
  { key: 'teal',   dotClass: 'bg-success',             label: 'Teal'   },
  { key: 'blue',   dotClass: 'bg-info',                label: 'Blue'   },
  { key: 'purple', dotClass: 'bg-info',                label: 'Purple' },
  { key: 'pink',   dotClass: 'bg-destructive',         label: 'Pink'   },
  { key: 'gray',   dotClass: 'bg-muted-foreground/50', label: 'Gray'   },
];
const PALETTE_BY_KEY: Record<string, string> = Object.fromEntries(
  PALETTE.map((p) => [p.key, p.dotClass]),
);

export const subnetLabelDotClass = (color?: string | null): string =>
  (color && PALETTE_BY_KEY[color.toLowerCase()]) || 'bg-muted-foreground/50';

// Render one label as a small pill — reused on the subnets table row, in
// the manager dialog list, and in the popover's "selected" preview.
export const SubnetLabelChip: React.FC<{ label: SubnetLabelInfo }> = ({ label }) => (
  <span
    className="inline-flex max-w-full items-center gap-xxs rounded-chip border border-border bg-muted/40 px-xs py-px text-caption"
    title={label.name}
  >
    <span
      className={`inline-block size-1.5 shrink-0 rounded-full ${subnetLabelDotClass(label.color)}`}
      aria-hidden
    />
    <span className="truncate">{label.name}</span>
  </span>
);

// --- Colour picker (used inside the manager dialog) -----------------------

const ColorPicker: React.FC<{
  value: string | null;
  onChange: (next: string | null) => void;
}> = ({ value, onChange }) => (
  <div className="flex flex-wrap gap-xxs" role="radiogroup" aria-label="Label colour">
    <button
      type="button"
      role="radio"
      aria-checked={!value}
      onClick={() => onChange(null)}
      className={`rounded-control border px-xs py-xxs text-caption ${!value ? 'border-primary' : 'border-border'}`}
    >
      <span className="mr-xxs inline-block size-1.5 rounded-full bg-muted-foreground/50" aria-hidden />
      None
    </button>
    {PALETTE.map((p) => (
      <button
        key={p.key}
        type="button"
        role="radio"
        aria-checked={value === p.key}
        onClick={() => onChange(p.key)}
        className={`rounded-control border px-xs py-xxs text-caption ${value === p.key ? 'border-primary' : 'border-border'}`}
        title={p.label}
      >
        <span className={`mr-xxs inline-block size-1.5 rounded-full ${p.dotClass}`} aria-hidden />
        {p.label}
      </button>
    ))}
  </div>
);

// --- Project-wide label CRUD dialog --------------------------------------

interface SubnetLabelManagerDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  // Called whenever the catalogue changes so the parent can refresh
  // anything that displays labels (e.g. the subnets table chips).
  onCatalogueChange?: () => void;
}

export const SubnetLabelManagerDialog: React.FC<SubnetLabelManagerDialogProps> = ({
  open, onOpenChange, onCatalogueChange,
}) => {
  const toast = useToast();
  const [confirmDialog, confirm] = useConfirm();
  const [labels, setLabels] = useState<SubnetLabelWithCounts[]>([]);
  const [loading, setLoading] = useState(false);

  // Create form
  const [newName, setNewName] = useState('');
  const [newColor, setNewColor] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);

  // Edit-in-place state — at most one label is editable at a time.
  const [editingId, setEditingId] = useState<number | null>(null);
  const [editName, setEditName] = useState('');
  const [editColor, setEditColor] = useState<string | null>(null);
  const [savingEdit, setSavingEdit] = useState(false);

  const fetchLabels = useCallback(async () => {
    setLoading(true);
    try {
      const rows = await listSubnetLabels();
      setLabels(rows);
    } catch (err) {
      toast.error(formatApiError(err, 'Failed to load subnet labels.'));
    } finally {
      setLoading(false);
    }
  }, [toast]);

  useEffect(() => {
    if (open) {
      fetchLabels();
    }
  }, [open, fetchLabels]);

  const handleCreate = async () => {
    const name = newName.trim();
    if (!name) return;
    setCreating(true);
    try {
      await createSubnetLabel(name, newColor);
      setNewName('');
      setNewColor(null);
      await fetchLabels();
      onCatalogueChange?.();
      toast.success(`Created label "${name}".`);
    } catch (err) {
      toast.error(formatApiError(err, 'Failed to create label.'));
    } finally {
      setCreating(false);
    }
  };

  const startEdit = (label: SubnetLabelWithCounts) => {
    setEditingId(label.id);
    setEditName(label.name);
    setEditColor(label.color ?? null);
  };
  const cancelEdit = () => {
    setEditingId(null);
    setEditName('');
    setEditColor(null);
  };
  const saveEdit = async () => {
    if (editingId == null) return;
    const name = editName.trim();
    if (!name) return;
    setSavingEdit(true);
    try {
      await updateSubnetLabel(editingId, { name, color: editColor });
      cancelEdit();
      await fetchLabels();
      onCatalogueChange?.();
    } catch (err) {
      toast.error(formatApiError(err, 'Failed to update label.'));
    } finally {
      setSavingEdit(false);
    }
  };

  const handleDelete = async (label: SubnetLabelWithCounts) => {
    // Inline browser confirm — keeps the dialog footprint small.  The
    // server cascade detaches all assignments; the warning explains
    // the impact so a misclick doesn't quietly orphan subnets.
    const msg = label.subnet_count > 0
      ? `It's attached to ${label.subnet_count} subnet${label.subnet_count === 1 ? '' : 's'} and will be detached from all of them.`
      : 'This label is not attached to any subnet.';
    const ok = await confirm({
      title: `Delete label "${label.name}"?`,
      body: msg,
      resourceName: label.name,
      severity: 'danger',
      confirmLabel: 'Delete',
    });
    if (!ok) return;
    try {
      await deleteSubnetLabel(label.id);
      await fetchLabels();
      onCatalogueChange?.();
      toast.success(`Deleted "${label.name}".`);
    } catch (err) {
      toast.error(formatApiError(err, 'Failed to delete label.'));
    }
  };

  return (
    <>
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-2xl">
        <DialogHeader>
          <DialogTitle>Manage subnet labels</DialogTitle>
        </DialogHeader>

        {/* Create row */}
        <div className="flex flex-col gap-sm rounded-control border border-border p-sm">
          <Label htmlFor="new-label-name">New label</Label>
          <div className="flex flex-col gap-xs sm:flex-row sm:items-center">
            <Input
              id="new-label-name"
              value={newName}
              onChange={(e) => setNewName(e.target.value)}
              placeholder="Label name (e.g. internet-facing, PCI)"
              maxLength={60}
            />
            <Button
              onClick={handleCreate}
              disabled={creating || !newName.trim()}
            >
              {creating ? <Loader2 className="size-4 animate-spin" aria-hidden /> : <Plus className="size-4" aria-hidden />}
              Create
            </Button>
          </div>
          <ColorPicker value={newColor} onChange={setNewColor} />
        </div>

        {/* Existing labels list */}
        <div className="max-h-96 space-y-xs overflow-y-auto">
          {loading ? (
            <p className="text-center text-metadata text-muted-foreground">Loading…</p>
          ) : labels.length === 0 ? (
            <p className="text-center text-metadata text-muted-foreground">No labels yet — create one above.</p>
          ) : (
            labels.map((label) => {
              const isEditing = editingId === label.id;
              return (
                <div
                  key={label.id}
                  className="flex flex-col gap-xs rounded-control border border-border p-sm"
                >
                  {isEditing ? (
                    <>
                      <Input
                        value={editName}
                        onChange={(e) => setEditName(e.target.value)}
                        maxLength={60}
                        autoFocus
                      />
                      <ColorPicker value={editColor} onChange={setEditColor} />
                      <div className="flex justify-end gap-xxs">
                        <Button variant="ghost" size="sm" onClick={cancelEdit}>Cancel</Button>
                        <Button size="sm" onClick={saveEdit} disabled={savingEdit || !editName.trim()}>
                          {savingEdit && <Loader2 className="size-4 animate-spin" aria-hidden />}
                          Save
                        </Button>
                      </div>
                    </>
                  ) : (
                    <div className="flex items-center justify-between gap-sm">
                      <div className="flex flex-1 items-center gap-xs">
                        <SubnetLabelChip label={{ id: label.id, name: label.name, color: label.color }} />
                        <Badge variant="muted">{label.subnet_count} subnet{label.subnet_count === 1 ? '' : 's'}</Badge>
                        <Badge variant="muted">{label.host_count} host{label.host_count === 1 ? '' : 's'}</Badge>
                      </div>
                      <div className="flex gap-xxs">
                        <Tooltip>
                          <TooltipTrigger asChild>
                            <Button variant="ghost" size="icon" onClick={() => startEdit(label)} aria-label={`Edit label ${label.name}`}>
                              <Pencil className="size-4" aria-hidden />
                            </Button>
                          </TooltipTrigger>
                          <TooltipContent>Edit</TooltipContent>
                        </Tooltip>
                        <Tooltip>
                          <TooltipTrigger asChild>
                            <Button
                              variant="ghost"
                              size="icon"
                              onClick={() => handleDelete(label)}
                              aria-label={`Delete label ${label.name}`}
                              className="text-muted-foreground hover:text-destructive"
                            >
                              <Trash2 className="size-4" aria-hidden />
                            </Button>
                          </TooltipTrigger>
                          <TooltipContent>Delete</TooltipContent>
                        </Tooltip>
                      </div>
                    </div>
                  )}
                </div>
              );
            })
          )}
        </div>

        <DialogFooter>
          <Button variant="ghost" onClick={() => onOpenChange(false)}>Close</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
    {confirmDialog}
    </>
  );
};


// --- Per-subnet inline label editor (popover) ----------------------------

interface SubnetLabelEditorPopoverProps {
  subnetId: number;
  subnetCidr: string;
  currentLabels: SubnetLabelInfo[];
  // Project label catalogue — owner-supplied so we don't re-fetch on
  // every popover open.  Refresh by calling the manager dialog's
  // `onCatalogueChange`.
  catalogue: SubnetLabelWithCounts[];
  onSaved: (next: SubnetLabelInfo[]) => void;
  // Anchor element (the small "Edit labels" button rendered in the row).
  children: React.ReactNode;
}

export const SubnetLabelEditorPopover: React.FC<SubnetLabelEditorPopoverProps> = ({
  subnetId, subnetCidr, currentLabels, catalogue, onSaved, children,
}) => {
  const toast = useToast();
  const [open, setOpen] = useState(false);
  const [pending, setPending] = useState(false);
  const [selected, setSelected] = useState<string[]>(() => currentLabels.map((l) => String(l.id)));

  // Re-sync when the popover (re)opens or the upstream label set
  // changes externally (e.g. after a bulk-apply elsewhere).
  useEffect(() => {
    setSelected(currentLabels.map((l) => String(l.id)));
  }, [currentLabels, open]);

  const options = useMemo(
    () => catalogue.map((lbl) => ({
      value: String(lbl.id),
      label: lbl.name,
    })),
    [catalogue],
  );

  const handleSave = async () => {
    setPending(true);
    try {
      const ids = selected.map((s) => Number(s)).filter((n) => Number.isFinite(n));
      const next = await replaceSubnetLabels(subnetId, ids);
      onSaved(next);
      setOpen(false);
    } catch (err) {
      toast.error(formatApiError(err, `Failed to update labels for ${subnetCidr}.`));
    } finally {
      setPending(false);
    }
  };

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>{children}</PopoverTrigger>
      <PopoverContent className="w-80 space-y-sm" align="start">
        <div className="flex items-center justify-between">
          <p className="text-metadata font-semibold">Labels for {subnetCidr}</p>
          <button
            type="button"
            onClick={() => setOpen(false)}
            aria-label="Close label editor"
            className="text-muted-foreground hover:text-foreground"
          >
            <CloseIcon className="size-4" aria-hidden />
          </button>
        </div>
        <Combobox
          multiple
          options={options}
          values={selected}
          onValuesChange={setSelected}
          placeholder="Select labels…"
          emptyMessage={catalogue.length === 0
            ? 'No labels in this project yet. Open Manage labels to create one.'
            : 'No matching labels.'}
        />
        <div className="flex justify-end gap-xxs">
          <Button variant="ghost" size="sm" onClick={() => setOpen(false)} disabled={pending}>Cancel</Button>
          <Button size="sm" onClick={handleSave} disabled={pending}>
            {pending && <Loader2 className="size-4 animate-spin" aria-hidden />}
            Save
          </Button>
        </div>
      </PopoverContent>
    </Popover>
  );
};
