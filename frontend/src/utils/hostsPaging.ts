/**
 * Where the Hosts list keeps its place: the page in the URL (`?page=`, 1-based,
 * left out for the first page) so a reload or a return from a host's own page
 * lands on the same rows, and rows-per-page per viewer in localStorage.  The
 * list works the same when storage is unavailable.
 */
import { userScopedKey } from './scopedStorage';

export const HOSTS_PAGE_PARAM = 'page';
export const HOSTS_PAGE_SIZES = [10, 25, 50, 100];
export const HOSTS_DEFAULT_PAGE_SIZE = 25;

const sizeKey = () => userScopedKey('hosts.rowsPerPage');

export function readHostsPageSize(): number {
  try {
    const n = Number(localStorage.getItem(sizeKey()));
    return HOSTS_PAGE_SIZES.includes(n) ? n : HOSTS_DEFAULT_PAGE_SIZE;
  } catch {
    return HOSTS_DEFAULT_PAGE_SIZE;
  }
}

export function writeHostsPageSize(size: number): void {
  try {
    localStorage.setItem(sizeKey(), String(size));
  } catch {
    /* the choice lasts for this visit only */
  }
}

/** Zero-based page index from the URL; anything but a whole number ≥ 2 is the first page. */
export function hostsPageFromUrl(urlParams: URLSearchParams): number {
  const n = Number(urlParams.get(HOSTS_PAGE_PARAM));
  return Number.isInteger(n) && n > 1 ? n - 1 : 0;
}
