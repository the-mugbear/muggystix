/**
 * Saving a file from the page — the one copy of the object-URL sequence
 * (create, a hidden `<a download>`, click, revoke) every export used to spell
 * out for itself.  Client-free: the API modules and the components both use it.
 */

/** Hand `blob` to the browser as a download named `filename`. */
export function saveBlob(blob: Blob, filename: string): void {
  if (typeof document === 'undefined') return;
  const url = window.URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  window.URL.revokeObjectURL(url);
}

/** How long a tab's object URL is kept: the tab has read the bytes long
 *  before, and revoking it then does not unload the page it shows. */
export const TAB_URL_LIFETIME_MS = 60_000;

/**
 * Show `blob` in `tab` — a tab the click handler opened blank
 * (`window.open('', '_blank')`, the only moment a popup blocker allows one)
 * before the bytes were fetched.  The tab is cut off from this page first
 * (`opener`), then pointed at an object URL of the bytes, typed `type` when
 * the blob carries no type of its own; the URL is revoked after
 * `TAB_URL_LIFETIME_MS`.
 */
export function showBlobInTab(tab: Window, blob: Blob, type?: string): void {
  const typed = type && !blob.type ? new Blob([blob], { type }) : blob;
  const url = window.URL.createObjectURL(typed);
  try {
    tab.opener = null;
  } catch {
    // Not every browser lets the opener be cleared; the URL still opens.
  }
  tab.location.href = url;
  window.setTimeout(() => window.URL.revokeObjectURL(url), TAB_URL_LIFETIME_MS);
}

/** In-memory text (a JSON or Markdown export, recovery codes) as a download. */
export function downloadTextFile(filename: string, text: string, mime = 'text/plain'): void {
  saveBlob(new Blob([text], { type: mime }), filename);
}

/**
 * The server's file name from a `Content-Disposition` header, else `fallback`.
 *
 * The header is RFC 6266's: `filename*=UTF-8''<percent-encoded>` carries the
 * real name when it is not plain ASCII (a scan file called `сканирование.xml`,
 * a site called `東京`) and is preferred; `filename=` beside it is the ASCII
 * stand-in, read up to the next parameter.
 */
export function filenameFromContentDisposition(header: string | null | undefined, fallback: string): string {
  if (!header) return fallback;
  const extended = header.match(/filename\*\s*=\s*UTF-8''([^;]+)/i)?.[1]?.trim();
  if (extended) {
    try {
      const name = decodeURIComponent(extended);
      if (name) return name;
    } catch {
      // Not valid percent-encoding: the plain name beside it is used.
    }
  }
  const plain = header.match(/filename\s*=\s*(?:"([^"]*)"|([^;]+))/i);
  return (plain?.[1] ?? plain?.[2])?.trim() || fallback;
}
