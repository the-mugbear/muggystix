/**
 * Where the Hosts list keeps its place: the page in the URL (`?page=`, 1-based,
 * left out for the first page) so a reload or a return from a host's own page
 * lands on the same rows, and rows-per-page per viewer in localStorage.  The
 * list works the same when storage is unavailable.  The page is READ from the
 * address by the one reading every list uses (`hooks/useUrlPage`
 * `pageFromParams`).
 */
import { userScopedKey } from './scopedStorage';

export const HOSTS_PAGE_PARAM = 'page';
/** The host open in the side sheet (`?host=<id>`), so a reload or a shared
 *  link opens it again — as Names keeps its sheet (`?name_id=`). */
export const HOSTS_OPEN_HOST_PARAM = 'host';
/** The ONE reading of `?host=`: a positive whole number, else no host. */
export const openHostFromParams = (params: URLSearchParams): number | null => {
  const raw = params.get(HOSTS_OPEN_HOST_PARAM) ?? '';
  return /^[1-9]\d{0,9}$/.test(raw) ? Number(raw) : null;
};
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
