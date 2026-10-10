/**
 * Which formats a format chooser offers FIRST for a file (owner decision
 * 2026-10-10): the ones a file with that extension can be, the rest behind
 * "Show all formats".  The chooser listed every parser for any file — JSON
 * and CSV parsers for an `.xml`.
 *
 * The chooser's list is the server's (`FormatOption`: `file_type`, `label`,
 * `family`), and the server does not say which extensions a format arrives
 * as, so that is declared here, per `file_type`, from the dispatcher
 * (`_build_parsing_attempts` in `backend/app/services/ingestion_service.py`
 * routes by extension first).  `tests/formatChoices.test.ts` pins the keys to
 * the server's registry (`format_registry.FORMATS`) and the extensions to
 * the dropzone's (`data/uploadFormats.ACCEPTED_EXTENSION_LIST`), so a format
 * added on the server fails that test until it is declared.
 *
 * (hazard — removable: the server sends each format's extensions on
 * `FormatOption`; this table and its test then go.)
 *
 * It only ORDERS a choice, it never refuses one: a format this table does not
 * know, a file with no extension or one no format declares, and the format
 * already selected are always offered; "Show all formats" offers everything.
 */
import type { FormatOption } from '../services/api';

const XML = ['.xml'] as const;
const JSON_LINES = ['.json', '.jsonl', '.ndjson'] as const;
const CSV = ['.csv'] as const;
const TEXT = ['.txt'] as const;

/** The extensions a file of each format arrives with. */
export const FORMAT_EXTENSIONS: Record<string, readonly string[]> = {
  nmap_xml: XML,
  nmap_gnmap: ['.gnmap'],
  gnmap_txt: TEXT,
  masscan_xml: XML,
  masscan_json: JSON_LINES,
  masscan_list: TEXT,
  naabu_json: JSON_LINES,
  naabu_output: TEXT,
  rustscan_output: TEXT,
  nessus_xml: ['.nessus', '.xml'],
  openvas_xml: XML,
  nikto_json: JSON_LINES,
  nikto_csv: CSV,
  nikto_output: TEXT,
  nuclei_json: JSON_LINES,
  httpx_json: JSON_LINES,
  whatweb_json: JSON_LINES,
  testssl_json: JSON_LINES,
  eyewitness_json: JSON_LINES,
  eyewitness_csv: CSV,
  eyewitness_zip: ['.zip'],
  dirbuster_json: JSON_LINES,
  dirbuster_csv: CSV,
  dirbuster_output: TEXT,
  dns_csv: CSV,
  dnsx_json: JSON_LINES,
  amass_json: JSON_LINES,
  amass_output: TEXT,
  rdap_json: JSON_LINES,
  netexec_json: JSON_LINES,
  netexec_output: TEXT,
  smbmap_json: JSON_LINES,
  smbmap_output: TEXT,
  bloodhound_json: JSON_LINES,
};

/** A file name's extension, lower-cased with its dot; null when it has none. */
export const fileExtension = (filename: string): string | null => {
  const name = filename.trim().toLowerCase();
  const dot = name.lastIndexOf('.');
  // A leading dot is a hidden file's name, not an extension.
  if (dot <= 0 || dot === name.length - 1) return null;
  return name.slice(dot);
};

export interface FormatChoices {
  /** The formats to offer, in the order given. */
  shown: FormatOption[];
  /** How many are held back behind "Show all formats". */
  hidden: number;
  /** The extension the list was narrowed to; null when it was not narrowed. */
  extension: string | null;
}

/**
 * The formats to offer for `filename` out of `formats`.
 *
 * `showAll` is the reader's "Show all formats"; `keep` is the format selected
 * now, which is never held back.
 */
export const formatChoices = (
  formats: FormatOption[],
  filename: string,
  { showAll = false, keep = null }: { showAll?: boolean; keep?: string | null } = {},
): FormatChoices => {
  const all: FormatChoices = { shown: formats, hidden: 0, extension: null };
  if (showAll) return all;
  const extension = fileExtension(filename);
  if (extension == null) return all;
  const declares = (fileType: string): boolean | null => {
    const declared = FORMAT_EXTENSIONS[fileType];
    return declared ? declared.includes(extension) : null;
  };
  // An extension no offered format declares (`.log`, or a list this table
  // does not know): nothing to put first, so everything is offered.
  if (!formats.some((f) => declares(f.file_type) === true)) return all;
  const shown = formats.filter((f) => declares(f.file_type) !== false || f.file_type === keep);
  return { shown, hidden: formats.length - shown.length, extension };
};
