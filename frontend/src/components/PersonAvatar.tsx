/**
 * A person's profile picture, or their initials (5.424.0).  The picture is
 * read by its SHA-256 (`GET /users/{id}/profile-image/{sha}`): the address
 * names the bytes, so the read is remembered for good per (user, sha) and a
 * replaced picture is simply another key.  Bytes come back as a Blob shown
 * through an object URL (`useBlobUrl`), never a bare `<img src>` to the API.
 */
import React from 'react';
import { useQuery } from '@tanstack/react-query';

import { fetchProfileImage } from '../services/api';
import { useBlobUrl } from '../lib/objectUrls';
import { rememberFor } from '../lib/query';
import { cn } from '../utils/cn';

export interface AvatarPerson { id: number; name: string | null; profile_image: string | null }

export const initialsOf = (name: string | null | undefined): string => {
  const parts = (name ?? '').trim().split(/\s+/).filter(Boolean);
  if (parts.length === 0) return '?';
  const first = Array.from(parts[0])[0] ?? '';
  const last = parts.length > 1 ? Array.from(parts[parts.length - 1])[0] ?? '' : '';
  return (first + last).toUpperCase();
};

interface Props {
  person: AvatarPerson | null | undefined;
  size?: number;
  className?: string;
}

const PersonAvatar: React.FC<Props> = ({ person, size = 24, className }) => {
  const sha = person?.profile_image ?? null;
  const userId = person?.id ?? 0;
  const picture = useQuery({
    queryKey: ['fetchProfileImage', userId, sha],
    queryFn: ({ signal }) => fetchProfileImage(userId, sha ?? '', signal),
    enabled: Boolean(sha && userId),
    // The bytes of one sha never change.
    ...rememberFor(Infinity),
  });
  const url = useBlobUrl(picture.data);
  const name = person?.name ?? 'Someone';
  const style = { width: size, height: size, fontSize: Math.max(9, Math.round(size * 0.4)) };
  if (url) {
    return (
      <img src={url} alt="" title={name} style={style}
        className={cn('inline-block shrink-0 rounded-full object-cover', className)} />
    );
  }
  return (
    <span aria-hidden title={name} style={style}
      className={cn('inline-flex shrink-0 items-center justify-center rounded-full bg-muted font-medium text-muted-foreground', className)}>
      {initialsOf(person?.name)}
    </span>
  );
};

export default PersonAvatar;
