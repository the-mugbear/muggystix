/**
 * Profile → Profile picture (5.424.0).  The page crops and scales: pick an
 * image, frame a square (zoom, position), and the browser draws it at
 * 256 × 256 on a canvas and sends that PNG — so the server never receives the
 * original file and its metadata (a phone photo's GPS position) is gone.  The
 * server still checks what it receives (square PNG / JPEG, at most 512 KB).
 */
import React, { useEffect, useRef, useState } from 'react';
import { useMutation } from '@tanstack/react-query';
import { ImagePlus, Loader2, Trash2 } from 'lucide-react';

import { removeOwnProfileImage, setOwnProfileImage } from '../services/api';
import { useAuth } from '../contexts/AuthContext';
import { useToast } from '../contexts/ToastContext';
import { useBlobUrl } from '../lib/objectUrls';
import { formatApiError } from '../utils/apiErrors';
import PersonAvatar from './PersonAvatar';
import { Button } from './ui/button';
import { Label } from './ui/label';

export const OUTPUT_SIDE = 256;
const PREVIEW_SIDE = 160;

export interface Framing { zoom: number; x: number; y: number }

/** The source square for a framing: the largest centred square, shrunk by
 *  `zoom`, moved by `x` / `y` (−1…1 of the room left on each axis). */
export const sourceSquare = (width: number, height: number, f: Framing) => {
  const side = Math.min(width, height) / Math.max(1, f.zoom);
  const roomX = (width - side) / 2;
  const roomY = (height - side) / 2;
  return { sx: roomX + f.x * roomX, sy: roomY + f.y * roomY, side };
};

const drawSquare = (img: HTMLImageElement, f: Framing, canvas: HTMLCanvasElement, out: number) => {
  canvas.width = out;
  canvas.height = out;
  const ctx = canvas.getContext('2d');
  if (!ctx) return;
  const { sx, sy, side } = sourceSquare(img.naturalWidth, img.naturalHeight, f);
  ctx.clearRect(0, 0, out, out);
  ctx.drawImage(img, sx, sy, side, side, 0, 0, out, out);
};

const ProfilePictureControl: React.FC = () => {
  const { user, updateUser } = useAuth();
  const toast = useToast();
  const [file, setFile] = useState<File | null>(null);
  const [framing, setFraming] = useState<Framing>({ zoom: 1, x: 0, y: 0 });
  const [image, setImage] = useState<HTMLImageElement | null>(null);
  const preview = useRef<HTMLCanvasElement | null>(null);
  const fileUrl = useBlobUrl(file);

  useEffect(() => {
    if (!fileUrl) return;
    const img = new Image();
    img.onload = () => setImage(img);
    img.onerror = () => toast.error('That file could not be read as an image.');
    img.src = fileUrl;
  }, [fileUrl, toast]);

  useEffect(() => {
    if (image && preview.current) drawSquare(image, framing, preview.current, PREVIEW_SIDE);
  }, [image, framing]);

  const save = useMutation({
    mutationFn: (blob: Blob) => setOwnProfileImage(blob),
    onSuccess: (answer) => {
      updateUser({ profile_image: answer.profile_image });
      setFile(null);
      setImage(null);
      toast.success('Profile picture saved.');
    },
    onError: (err) => toast.error(formatApiError(err, 'Could not save the picture.')),
  });
  const remove = useMutation({
    mutationFn: () => removeOwnProfileImage(),
    onSuccess: () => { updateUser({ profile_image: null }); toast.success('Profile picture removed.'); },
    onError: (err) => toast.error(formatApiError(err, 'Could not remove the picture.')),
  });

  const onSave = () => {
    if (!image) return;
    const canvas = document.createElement('canvas');
    drawSquare(image, framing, canvas, OUTPUT_SIDE);
    canvas.toBlob((blob) => { if (blob) save.mutate(blob); }, 'image/png');
  };

  if (!user) return null;
  const slider = (label: string, key: keyof Framing, min: number, max: number) => (
    <label className="flex items-center gap-xs text-caption">
      <span className="w-16 text-muted-foreground">{label}</span>
      <input type="range" min={min} max={max} step={0.01} value={framing[key]}
        onChange={(e) => setFraming({ ...framing, [key]: Number(e.target.value) })} aria-label={label} />
    </label>
  );

  return (
    <div className="flex flex-col gap-xs" data-testid="profile-picture">
      <Label htmlFor="profile-picture-file">Profile picture</Label>
      <div className="flex flex-wrap items-start gap-md">
        {image ? (
          <canvas ref={preview} width={PREVIEW_SIDE} height={PREVIEW_SIDE}
            className="rounded-full border border-border" aria-label="Preview of the cropped picture" />
        ) : user.profile_image ? (
          <PersonAvatar person={{ id: user.id, name: user.full_name || user.username, profile_image: user.profile_image }} size={64} />
        ) : null}
        <div className="flex flex-col gap-xs">
          {image && (
            <>
              {slider('Zoom', 'zoom', 1, 4)}
              {slider('Across', 'x', -1, 1)}
              {slider('Down', 'y', -1, 1)}
            </>
          )}
          <div className="flex flex-wrap gap-xs">
            <input id="profile-picture-file" type="file" accept="image/png,image/jpeg,image/webp,image/gif"
              className="sr-only" onChange={(e) => {
                const f = e.target.files?.[0] ?? null;
                setFile(f);
                setImage(null);
                setFraming({ zoom: 1, x: 0, y: 0 });
                e.target.value = '';
              }} />
            <Button type="button" variant="outline" size="sm" asChild>
              <label htmlFor="profile-picture-file" className="cursor-pointer">
                <ImagePlus className="size-4" aria-hidden /> {user.profile_image ? 'Change picture' : 'Choose a picture'}
              </label>
            </Button>
            {image && (
              <Button type="button" size="sm" onClick={onSave} disabled={save.isPending}>
                {save.isPending && <Loader2 className="size-4 animate-spin" aria-hidden />} Save picture
              </Button>
            )}
            {image && (
              <Button type="button" variant="ghost" size="sm" onClick={() => { setFile(null); setImage(null); }}>Cancel</Button>
            )}
            {!image && user.profile_image && (
              <Button type="button" variant="ghost" size="sm" onClick={() => remove.mutate()} disabled={remove.isPending}>
                <Trash2 className="size-4" aria-hidden /> Remove
              </Button>
            )}
          </div>
          <p className="text-caption text-muted-foreground">
            Shown in presentation mode, beside your comments and in the user menu — to people who share a project with you.
          </p>
        </div>
      </div>
    </div>
  );
};

export default ProfilePictureControl;
