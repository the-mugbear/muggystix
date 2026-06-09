/**
 * SiteManagerDialog — set the criticality tier + expected host count for the
 * project's sites (the metadata the attention model weights by). Sites are
 * created by naming them on subnets (CSV col 4 / inline edit); this edits
 * their metadata, not the name.
 */
import React, { useEffect, useState } from 'react';
import { Loader2 } from 'lucide-react';

import { listSites, updateSite, type Site } from '../services/api';
import { useToast } from '../contexts/ToastContext';
import { formatApiError } from '../utils/apiErrors';
import {
  Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle,
} from './ui/dialog';
import { Input } from './ui/input';
import { Label } from './ui/label';
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from './ui/select';
import {
  Table, TableBody, TableCell, TableHead, TableHeader, TableRow,
} from './ui/table';

interface SiteManagerDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

export const SiteManagerDialog: React.FC<SiteManagerDialogProps> = ({ open, onOpenChange }) => {
  const toast = useToast();
  const [sites, setSites] = useState<Site[]>([]);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (!open) return;
    setLoading(true);
    listSites()
      .then(setSites)
      .catch((e) => toast.error(formatApiError(e, 'Failed to load sites.')))
      .finally(() => setLoading(false));
  }, [open, toast]);

  const patch = async (id: number, payload: Parameters<typeof updateSite>[1]) => {
    try {
      const updated = await updateSite(id, payload);
      setSites((prev) => prev.map((s) => (s.id === id ? updated : s)));
    } catch (e) {
      toast.error(formatApiError(e, 'Failed to update site.'));
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-2xl">
        <DialogHeader>
          <DialogTitle>Manage sites</DialogTitle>
          <DialogDescription>
            Set each site's criticality tier (1 = most critical — weights its exposure on
            the Needs-attention card) and expected host count (drives coverage-gap
            detection). Sites are named by tagging subnets; this edits their metadata.
          </DialogDescription>
        </DialogHeader>
        {loading ? (
          <div className="flex items-center gap-xs py-lg" role="status">
            <Loader2 className="size-4 animate-spin text-muted-foreground" aria-hidden /> Loading…
          </div>
        ) : sites.length === 0 ? (
          <p className="py-lg text-center text-metadata text-muted-foreground">
            No sites yet. Add a site to a subnet (CSV column 4 or the Site cell) to create one.
          </p>
        ) : (
          <div className="overflow-x-auto">
            <Table className="table-fixed">
              <TableHeader>
                <TableRow>
                  <TableHead>Site</TableHead>
                  <TableHead className="w-20">Subnets</TableHead>
                  <TableHead className="w-28">Tier</TableHead>
                  <TableHead className="w-36">Expected hosts</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {sites.map((s) => (
                  <TableRow key={s.id}>
                    <TableCell className="truncate font-medium">{s.name}</TableCell>
                    <TableCell className="text-caption text-muted-foreground">{s.subnet_count}</TableCell>
                    <TableCell>
                      <Select
                        value={String(s.criticality_tier)}
                        onValueChange={(v) => patch(s.id, { criticality_tier: Number(v) })}
                      >
                        <SelectTrigger className="h-8 text-caption" aria-label={`Tier for ${s.name}`}>
                          <SelectValue />
                        </SelectTrigger>
                        <SelectContent>
                          {[1, 2, 3, 4].map((t) => (
                            <SelectItem key={t} value={String(t)}>
                              Tier {t}{t === 1 ? ' (critical)' : t === 4 ? ' (low)' : ''}
                            </SelectItem>
                          ))}
                        </SelectContent>
                      </Select>
                    </TableCell>
                    <TableCell>
                      <Input
                        type="number"
                        min={0}
                        defaultValue={s.expected_host_count ?? ''}
                        aria-label={`Expected host count for ${s.name}`}
                        onBlur={(e) => {
                          const raw = e.target.value.trim();
                          const next = raw === '' ? null : Number(raw);
                          if (next !== s.expected_host_count) {
                            void patch(s.id, { expected_host_count: next });
                          }
                        }}
                      />
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
        )}
      </DialogContent>
    </Dialog>
  );
};

export default SiteManagerDialog;
