/**
 * A URL that came from imported data, usable as an `href` only when it is
 * http(s) (review 2026-09-23 R11).
 *
 * Web paths, web interfaces and tool links are written by parsers from
 * scanner files; a crafted file could carry `javascript:` or `data:` in a URL
 * field, and React only warns about such an href — it still renders it.
 * Returns `undefined` otherwise, so `<a href={safeHttpHref(x)}>` renders the
 * text without a link.
 */
export const safeHttpHref = (url: string | null | undefined): string | undefined => {
  if (!url) return undefined;
  try {
    const parsed = new URL(url.trim());
    return parsed.protocol === 'http:' || parsed.protocol === 'https:' ? url.trim() : undefined;
  } catch {
    return undefined;
  }
};
