/**
 * Several files named alike read as one pattern (5.304.0): Operations' Blocked
 * strip listed "mssql-privesc--block-04.txt, mssql-privesc--block-03.txt,
 * mssql-privesc--block-02.txt" — three near-identical names that said less
 * than the shape they share, "mssql-privesc--block-*.txt".  Names that share
 * less than a six-character start are listed as they are.
 */
export function filenameSummary(names: string[]): string {
  if (names.length < 2) return names.join(', ');
  let prefix = names[0];
  let suffix = names[0];
  for (const n of names.slice(1)) {
    while (!n.startsWith(prefix)) prefix = prefix.slice(0, -1);
    while (!n.endsWith(suffix)) suffix = suffix.slice(1);
  }
  // Prefix and suffix may overlap on the shortest name; the suffix gives way.
  const shortest = Math.min(...names.map((n) => n.length));
  if (prefix.length + suffix.length > shortest) suffix = suffix.slice(prefix.length + suffix.length - shortest);
  return prefix.length >= 6 ? `${prefix}*${suffix}` : names.join(', ');
}
