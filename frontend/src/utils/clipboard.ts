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
  if (typeof document === 'undefined') return false;
  const ta = document.createElement('textarea');
  ta.value = text;
  // Read-only: no on-screen keyboard or "input" event for a field that is
  // only there to be selected (5.410.1: the Oversight share dialog's copy,
  // which did this and removed the field even when the copy threw — the one
  // implementation now; review 2026-10-16, N19).
  ta.setAttribute('readonly', '');
  ta.style.position = 'fixed';
  ta.style.opacity = '0';
  document.body.appendChild(ta);
  try {
    ta.select();
    return document.execCommand('copy');
  } catch {
    return false;
  } finally {
    document.body.removeChild(ta);
  }
}

/** Kept for its callers; downloads live in `utils/download`. */
export { downloadTextFile } from './download';
