/**
 * Read CSV text into rows of fields (RFC 4180): a field may be quoted, a
 * quoted field may hold commas, line breaks and `""` for one quote; lines end
 * in LF or CRLF.  Fields are returned as written — trimming and skipping
 * blank lines are the caller's decisions.
 */
export function parseCsv(text: string): string[][] {
  const rows: string[][] = [];
  let row: string[] = [];
  let field = '';
  let quoted = false;
  // A field is quoted only when the quote opens it; elsewhere it is a character.
  let atFieldStart = true;

  const endField = () => {
    row.push(field);
    field = '';
    atFieldStart = true;
  };
  const endRow = () => {
    endField();
    rows.push(row);
    row = [];
  };

  for (let i = 0; i < text.length; i++) {
    const ch = text[i];
    if (quoted) {
      if (ch !== '"') {
        field += ch;
      } else if (text[i + 1] === '"') {
        field += '"';
        i++;
      } else {
        quoted = false;
      }
      continue;
    }
    if (ch === '"' && atFieldStart) {
      quoted = true;
      atFieldStart = false;
    } else if (ch === ',') {
      endField();
    } else if (ch === '\n') {
      endRow();
    } else if (ch === '\r' && text[i + 1] === '\n') {
      // The LF that follows ends the row.
    } else {
      field += ch;
      atFieldStart = false;
    }
  }
  // A last line without a line break; a text ending in one adds no empty row.
  if (field !== '' || row.length > 0 || quoted) endRow();
  return rows;
}
