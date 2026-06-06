/**
 * `cn` — class-name composer used by every shadcn-style primitive in
 * src/components/ui/.  Merges clsx's conditional class composition
 * with tailwind-merge's "last conflicting Tailwind wins" semantics so
 * callers can override a base primitive's defaults without writing
 * !important.
 *
 *   <Button className={cn("bg-primary", isDanger && "bg-destructive")} />
 *
 * tailwind-merge sees `bg-primary bg-destructive` and keeps the
 * second.  Without it both classes would emit and the visual outcome
 * depends on Tailwind's source order.
 */

import { type ClassValue, clsx } from 'clsx';
import { twMerge } from 'tailwind-merge';

export function cn(...inputs: ClassValue[]): string {
  return twMerge(clsx(inputs));
}
