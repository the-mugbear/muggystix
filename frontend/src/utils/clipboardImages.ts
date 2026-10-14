/**
 * The image files a paste carries — the ONE reader of a pasted image (a
 * finding's comment, a host note, a report text, the project images panel).
 *
 * Browsers and copying apps disagree on where a copied image goes: most put
 * it in `clipboardData.files`, some only in `clipboardData.items` (a file
 * item with no entry in `files`).  Reading one list only made a paste do
 * nothing for some screenshots (walkthrough 2026-10-14).
 */
export const imagesFromClipboard = (data: DataTransfer | null | undefined): File[] => {
  if (!data) return [];
  const files = Array.from(data.files ?? []).filter((f) => f.type.startsWith('image/'));
  if (files.length) return files;
  const fromItems: File[] = [];
  for (const item of Array.from(data.items ?? [])) {
    if (item.kind !== 'file' || !item.type.startsWith('image/')) continue;
    const file = item.getAsFile();
    if (file) fromItems.push(file);
  }
  return fromItems;
};
