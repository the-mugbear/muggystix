/**
 * Copy text to the clipboard, returning true on success.
 *
 * Centralizes the copy logic that was otherwise re-implemented inline at
 * each call site (Hosts copy-link, per-row copy-IP, bulk copy-IPs).  Adds a
 * legacy `execCommand` fallback for non-secure contexts / older browsers
 * where `navigator.clipboard` is unavailable (e.g. http:// dev deploys),
 * which the ad-hoc copies did not handle — they silently no-op'd.
 */
export async function copyToClipboard(text: string): Promise<boolean> {
  try {
    if (typeof navigator !== 'undefined' && navigator.clipboard) {
      await navigator.clipboard.writeText(text);
      return true;
    }
  } catch {
    // Permission denied / not focused — fall through to the legacy path.
  }
  try {
    if (typeof document === 'undefined') return false;
    const ta = document.createElement('textarea');
    ta.value = text;
    ta.style.position = 'fixed';
    ta.style.opacity = '0';
    document.body.appendChild(ta);
    ta.select();
    const ok = document.execCommand('copy');
    document.body.removeChild(ta);
    return ok;
  } catch {
    return false;
  }
}

/**
 * Trigger a client-side download of in-memory text (JSON / markdown export).
 * Mirrors the blob-download the report endpoints use, for data already on the
 * page that needs no server round-trip.
 */
export function downloadTextFile(filename: string, text: string, mime = 'text/plain'): void {
  if (typeof document === 'undefined') return;
  const url = window.URL.createObjectURL(new Blob([text], { type: mime }));
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  window.URL.revokeObjectURL(url);
  document.body.removeChild(a);
}
