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

/** In-memory text (a JSON or Markdown export, recovery codes) as a download. */
export function downloadTextFile(filename: string, text: string, mime = 'text/plain'): void {
  saveBlob(new Blob([text], { type: mime }), filename);
}

/** The server's file name from a `Content-Disposition` header, else `fallback`. */
export function filenameFromContentDisposition(header: string | null | undefined, fallback: string): string {
  return header?.match(/filename="?([^"]+)"?/i)?.[1] || fallback;
}
