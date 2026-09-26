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
 *
 * CUSTOM FONT SIZES: our tailwind.config defines `text-page-title`,
 * `text-caption`, `text-body`, … as font-SIZE tokens.  Stock tailwind-merge
 * only knows Tailwind's built-in size names (xs/sm/base/…), so it filed our
 * custom `text-*` sizes under the text-COLOR group and treated them as
 * conflicting with real colours — silently dropping one.  That's what made a
 * `size="sm"` (→ `text-caption`) filled Button lose its `text-primary-foreground`
 * and render illegible inherited text (white-on-green in the phosphor theme).
 * Registering the custom sizes in the font-size group keeps size and colour
 * independent so both survive the merge.
 *
 * CUSTOM SPACING: the spacing scale is named (`p-sm`, `gap-md`, `px-xs`…).
 * tailwind-merge did not recognise those names as spacing, so an override
 * never displaced a primitive's default: `<CardContent className="p-sm">`
 * kept CardContent's `p-md pt-0`, and `pt-0` won — the host inspector's facts
 * box sat its first row on the border.  Registering the scale makes a
 * caller's spacing replace the default as intended.
 */

import { type ClassValue, clsx } from 'clsx';
import { extendTailwindMerge } from 'tailwind-merge';

const twMerge = extendTailwindMerge({
  extend: {
    theme: {
      spacing: ['xxs', 'xs', 'sm', 'md', 'lg', 'xl', 'xxl', 'xxxl'],
    },
    classGroups: {
      'font-size': [
        {
          text: [
            'page-title', 'section-title', 'subheading',
            'body', 'metadata', 'caption', 'micro',
          ],
        },
      ],
    },
  },
});

export function cn(...inputs: ClassValue[]): string {
  return twMerge(clsx(inputs));
}
