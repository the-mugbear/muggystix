/**
 * Tag management (v2.243.0) — rename, recolor, delete.
 *
 * Tags come into existence by being applied (bulk-tag by name), so the list
 * only ever grew: there was no way to fix a typo or retire a tag once it had
 * been used. `PATCH` and `DELETE /hosts/tags/{id}` had shipped and tested for
 * a long time with nothing reaching them. This is that missing surface.
 *
 * Creation deliberately stays where it already works — tagging hosts — which
 * is also what guarantees every tag has at least one host behind it.
 */
import React, { useCallback, useEffect, useState } from 'react';
import { Check, Loader2, Pencil, RefreshCw, Trash2, X } from 'lucide-react';
import {
  HostTagWithCount,
  deleteHostTag,
  listHostTags,
  updateHostTag,
} from '../services/api';
import { useProject } from '../contexts/ProjectContext';
import { useToast } from '../contexts/ToastContext';
import { useConfirm } from '../hooks/useConfirm';
import { formatApiError } from '../utils/apiErrors';
import { safeFallback } from '../utils/uiStyles';
import { Badge } from './ui/badge';
import { Button } from './ui/button';
import { Card, CardContent, CardHeader, CardTitle } from './ui/card';
import { Input } from './ui/input';

const TagManagement: React.FC = () => {
  const { currentProject } = useProject();
  const toast = useToast();
  const [confirmEl, confirm] = useConfirm();
  const [tags, setTags] = useState<HostTagWithCount[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [editingId, setEditingId] = useState<number | null>(null);
  const [draftName, setDraftName] = useState('');
  const [busyId, setBusyId] = useState<number | null>(null);

  const projectId = currentProject?.id;

  const reload = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setTags(await listHostTags());
    } catch (err) {
      setError(formatApiError(err, 'Failed to load tags.'));
      setTags([]);
    } finally {
      setLoading(false);
    }
  }, [projectId]);

  useEffect(() => {
    if (!projectId) return;
    void reload();
  }, [reload, projectId]);

  const startEdit = (tag: HostTagWithCount) => {
    setEditingId(tag.id);
    setDraftName(tag.name);
  };

  const cancelEdit = () => {
    setEditingId(null);
    setDraftName('');
  };

  const saveEdit = async (tag: HostTagWithCount) => {
    const name = draftName.trim();
    if (!name || name === tag.name) {
      cancelEdit();
      return;
    }
    setBusyId(tag.id);
    try {
      await updateHostTag(tag.id, { name });
      toast.success(`Renamed to “${name}”.`);
      cancelEdit();
      await reload();
    } catch (err) {
      // 409 = another tag already owns that name. Surface it rather than
      // silently discarding the edit.
      toast.error(formatApiError(err, 'Failed to rename tag.'));
    } finally {
      setBusyId(null);
    }
  };

  const handleDelete = async (tag: HostTagWithCount) => {
    const ok = await confirm({
      title: `Delete “${tag.name}”?`,
      body:
        tag.host_count > 0
          ? `This tag is on ${tag.host_count} host${tag.host_count === 1 ? '' : 's'}. ` +
            'Deleting it removes the tag from all of them. The hosts themselves are not affected.'
          : 'This tag is not applied to any hosts.',
      severity: 'danger',
      confirmLabel: 'Delete tag',
    });
    if (!ok) return;
    setBusyId(tag.id);
    try {
      await deleteHostTag(tag.id);
      toast.success(`Deleted “${tag.name}”.`);
      await reload();
    } catch (err) {
      toast.error(formatApiError(err, 'Failed to delete tag.'));
    } finally {
      setBusyId(null);
    }
  };

  if (!projectId) return null;

  return (
    <Card className="mb-md">
      {confirmEl}
      <CardHeader className="flex flex-row items-center justify-between gap-sm">
        <CardTitle className="min-w-0">
          Host Tags{currentProject ? ` — ${currentProject.name}` : ''}
        </CardTitle>
        <Button size="sm" variant="outline" onClick={() => void reload()} disabled={loading}>
          <RefreshCw className={`size-4 ${loading ? 'animate-spin' : ''}`} aria-hidden />
          Refresh
        </Button>
      </CardHeader>
      <CardContent>
        <p className="mb-sm text-caption text-muted-foreground">
          Tags are created by applying them to hosts (Hosts → select → Tag). Rename or delete
          them here.
        </p>

        {error && <p className="mb-sm text-metadata text-destructive" role="alert">{error}</p>}

        {loading && tags.length === 0 && (
          <p className="py-md text-center text-metadata text-muted-foreground">
            <Loader2 className="mr-xs inline size-4 animate-spin" aria-hidden />
            Loading tags…
          </p>
        )}

        {!loading && !error && tags.length === 0 && (
          <p className="py-md text-center text-metadata text-muted-foreground">
            No tags yet. Select hosts on the Hosts page and use Tag to create one.
          </p>
        )}

        {tags.length > 0 && (
          <div className="overflow-x-auto">
            <table className="w-full text-metadata" style={{ tableLayout: 'fixed' }}>
              <colgroup>
                <col style={{ width: '55%' }} />
                <col style={{ width: '20%' }} />
                <col style={{ width: '25%' }} />
              </colgroup>
              <thead>
                <tr className="border-b border-border text-left text-caption text-muted-foreground">
                  <th className="py-xs pr-xs font-medium">Tag</th>
                  <th className="py-xs pr-xs font-medium">Hosts</th>
                  <th className="py-xs font-medium" />
                </tr>
              </thead>
              <tbody>
                {tags.map((tag) => (
                  <tr key={tag.id} className="border-b border-border/50">
                    <td className="py-xs pr-xs">
                      {editingId === tag.id ? (
                        <Input
                          value={draftName}
                          autoFocus
                          maxLength={60}
                          onChange={(e) => setDraftName(e.target.value)}
                          onKeyDown={(e) => {
                            if (e.key === 'Enter') void saveEdit(tag);
                            if (e.key === 'Escape') cancelEdit();
                          }}
                          className="h-8"
                          aria-label={`Rename tag ${tag.name}`}
                        />
                      ) : (
                        <Badge
                          variant="outline"
                          className="max-w-full"
                          style={tag.color ? { borderColor: tag.color, color: tag.color } : undefined}
                        >
                          <span className="truncate" title={tag.name}>{safeFallback(tag.name)}</span>
                        </Badge>
                      )}
                    </td>
                    <td className="py-xs pr-xs text-muted-foreground">{tag.host_count ?? 0}</td>
                    <td className="py-xs">
                      <div className="flex justify-end gap-xs">
                        {editingId === tag.id ? (
                          <>
                            <Button
                              size="sm" variant="outline"
                              disabled={busyId === tag.id}
                              onClick={() => void saveEdit(tag)}
                            >
                              {busyId === tag.id
                                ? <Loader2 className="size-3.5 animate-spin" aria-hidden />
                                : <Check className="size-3.5" aria-hidden />}
                              Save
                            </Button>
                            <Button size="sm" variant="ghost" onClick={cancelEdit}>
                              <X className="size-3.5" aria-hidden />
                            </Button>
                          </>
                        ) : (
                          <>
                            <Button
                              size="sm" variant="outline"
                              onClick={() => startEdit(tag)}
                              aria-label={`Rename ${tag.name}`}
                            >
                              <Pencil className="size-3.5" aria-hidden />
                              Rename
                            </Button>
                            <Button
                              size="sm" variant="ghost"
                              disabled={busyId === tag.id}
                              onClick={() => void handleDelete(tag)}
                              aria-label={`Delete ${tag.name}`}
                            >
                              <Trash2 className="size-3.5 text-destructive" aria-hidden />
                            </Button>
                          </>
                        )}
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </CardContent>
    </Card>
  );
};

export default TagManagement;
