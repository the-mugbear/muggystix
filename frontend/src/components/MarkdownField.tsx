/**
 * MarkdownField (5.293.0) — a textarea for text the client report prints as
 * Markdown, with the help an author needs to get it right:
 *
 *   - a toolbar (bold, italic, code, link, lists, code block, table) that
 *     writes the Markdown, so nobody has to remember the syntax;
 *   - Write / Preview: the preview is SafeMarkdown, under the report's rules
 *     (tables included), so what it shows is what the report prints;
 *   - a short guide to what the report accepts (no images or HTML, headings
 *     print as bold text);
 *   - a warning, with a one-click fix, for a table written straight after a
 *     line of text — the report joins it into that paragraph;
 *   - where the images can take one (`images.upload`: the System
 *     description), a screenshot pasted into the text is uploaded and placed
 *     where it was pasted, as on a finding's comment (owner, 2026-10-14).
 *
 * The label stays the caller's (`<Label htmlFor={id}>`), so the textarea is
 * found by its label as before.
 */
import React, { useEffect, useLayoutEffect, useRef, useState } from 'react';
import {
  Bold, CircleHelp, Code, ImageOff, ImagePlus, Italic, Link2, List, ListOrdered, Loader2, SquareCode, Table,
} from 'lucide-react';

import type { EvidenceImages, ImageSource } from '../utils/evidenceImages';
import {
  CODE_TEMPLATE,
  Edit,
  TABLE_TEMPLATE,
  insertBlock,
  insertLink,
  prefixLines,
  tablesAfterText,
  wrapSelection,
} from '../utils/markdownEditing';
import { formatApiError } from '../utils/apiErrors';
import { MarkdownImages, ReportImageProblem, imageReference } from '../utils/reportImages';
import { Button } from './ui/button';
import { Popover, PopoverContent, PopoverTrigger } from './ui/popover';
import { Textarea } from './ui/textarea';
import SafeMarkdown from './SafeMarkdown';

interface Props {
  id: string;
  /** Names the toolbar for assistive technology ("Description formatting"). */
  label: string;
  value: string;
  onChange: (value: string) => void;
  rows?: number;
  maxLength?: number;
  disabled?: boolean;
  /**
   * A finding's report section: the finding's images ticked "In report" —
   * or the report's System description: the project's own report images
   * (`useReportTextImages`; the resolver's `source` chooses the words).
   * Adds "Insert image" (it writes an empty reference, `![](evidence:<id>)`, on a line of
   * its own) and shows placed images in the preview.  Left out — the
   * executive summary, any other text — the field takes no images.
   */
  images?: MarkdownImages;
}

/** What the picker says about the images it offers — by whose they are
 *  (`images.resolver.source`): a finding's, or the report's own text's. */
const IMAGE_WORDS: Record<ImageSource, { none: string; title: string; picker: string; guide: string }> = {
  finding: {
    none: 'No image to insert: attach one to a comment below and tick “In report”.',
    title: 'Insert one of this finding’s “In report” images',
    picker: 'This finding’s images ticked “In report”. An image placed in the text prints there with its caption; '
      + 'the rest print under Evidence, each after the text of the comment or note it is attached to.',
    guide: 'only this finding’s own, ticked “In report”',
  },
  report: {
    none: 'No image to insert: paste a screenshot into the text, or upload one below.',
    title: 'Insert one of the report’s own images',
    picker: 'Images uploaded for the report’s own text, below. An image placed in the text prints there with its caption.',
    guide: 'only the ones uploaded for the report’s own text, below, or pasted into it (Ctrl/Cmd+V)',
  },
};

const GUIDE: Array<[string, string]> = [
  ['**bold**  _italic_  `code`', 'Emphasis and inline code'],
  ['- item  /  1. item', 'Lists — one item per line'],
  ['```  …  ```', 'A code block (commands, output) on lines of its own'],
  ['| A | B |\n| - | - |\n| 1 | 2 |', 'A table — leave a blank line before it'],
  ['Table: caption', 'On the line after a table (blank line between): its numbered caption, printed under it'],
  ['Listing: caption', 'On the line after a code block (blank line between): its numbered caption, printed under it'],
  ['[text](https://…)', 'A link — web and mail links only'],
  ['blank line', 'Starts a new paragraph (a single line break is a space)'],
];

const MarkdownField: React.FC<Props> = ({ id, label, value, onChange, rows = 4, maxLength, disabled, images }) => {
  const ref = useRef<HTMLTextAreaElement>(null);
  const [preview, setPreview] = useState(false);
  const [pickerOpen, setPickerOpen] = useState(false);
  // The caret when the picker opened: the popover takes the focus, and the
  // image goes where the author was writing.
  const caret = useRef<{ start: number; end: number } | null>(null);
  const placeable = images?.placeable ?? [];
  // Thumbnails are asked for when the picker OPENS, never before; the
  // resolver's cache fetches them a few at a time and shares each with the
  // preview and the comment thread.  One that could not be fetched says so,
  // and is tried again the next time the picker opens.
  const source: EvidenceImages | undefined = images?.resolver;
  const ensureImage = source?.ensure;
  const words = IMAGE_WORDS[source?.source ?? 'finding'];
  const sourceRef = useRef(source);
  sourceRef.current = source;
  useEffect(() => {
    if (!pickerOpen || !ensureImage) return;
    placeable.forEach((img) => {
      const current = sourceRef.current;
      if (current?.failed?.(img.id) && current.retry) current.retry(img.id);
      else ensureImage(img.id);
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pickerOpen, ensureImage, placeable.map((i) => i.id).join(',')]);
  // The selection to restore once React has written the edited value.
  const pendingSelection = useRef<{ start: number; end: number } | null>(null);

  useLayoutEffect(() => {
    const sel = pendingSelection.current;
    const el = ref.current;
    if (!sel || !el) return;
    pendingSelection.current = null;
    el.focus();
    el.setSelectionRange(sel.start, sel.end);
  }, [value, preview]);

  const apply = (edit: (e: Edit) => Edit, at?: { start: number; end: number } | null) => {
    const el = ref.current;
    const current: Edit = at
      ? { value, start: Math.min(at.start, value.length), end: Math.min(at.end, value.length) }
      : el
        ? { value, start: el.selectionStart, end: el.selectionEnd }
        : { value, start: value.length, end: value.length };
    const next = edit(current);
    if (maxLength !== undefined && next.value.length > maxLength) return;
    pendingSelection.current = { start: next.start, end: next.end };
    setPreview(false);
    onChange(next.value);
  };

  const tools: Array<{ label: string; icon: React.ElementType; run: (e: Edit) => Edit; keys?: string }> = [
    { label: 'Bold', icon: Bold, run: (e) => wrapSelection(e, '**', 'bold text'), keys: 'Ctrl+B' },
    { label: 'Italic', icon: Italic, run: (e) => wrapSelection(e, '_', 'italic text'), keys: 'Ctrl+I' },
    { label: 'Inline code', icon: Code, run: (e) => wrapSelection(e, '`', 'code') },
    { label: 'Link', icon: Link2, run: insertLink },
    { label: 'Bulleted list', icon: List, run: (e) => prefixLines(e, false) },
    { label: 'Numbered list', icon: ListOrdered, run: (e) => prefixLines(e, true) },
    { label: 'Code block', icon: SquareCode, run: (e) => insertBlock(e, CODE_TEMPLATE, 'command or output') },
    { label: 'Table', icon: Table, run: (e) => insertBlock(e, TABLE_TEMPLATE, 'Column') },
  ];

  const onKeyDown = (event: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (!(event.ctrlKey || event.metaKey) || event.altKey || event.shiftKey) return;
    const key = event.key.toLowerCase();
    if (key === 'b' || key === 'i') {
      event.preventDefault();
      apply(key === 'b' ? tools[0].run : tools[1].run);
    }
  };

  // A screenshot pasted into the text (where `images.upload` exists — the
  // report's own images): stored, then its reference written where it was
  // pasted, on a line of its own, as "Insert image" writes it.  The text may
  // have changed while it uploaded, so the reference goes into the text as it
  // is THEN, at the paste's place (or its end, if that text is now shorter).
  const latest = useRef(value);
  latest.current = value;
  const [pasting, setPasting] = useState(0);
  const [pasteError, setPasteError] = useState<string | null>(null);
  const upload = images?.upload;
  const onPaste = (event: React.ClipboardEvent<HTMLTextAreaElement>) => {
    if (!upload) return;
    const files = Array.from(event.clipboardData.files).filter((f) => f.type.startsWith('image/'));
    if (files.length === 0) return;
    event.preventDefault();
    const at = { start: event.currentTarget.selectionStart, end: event.currentTarget.selectionEnd };
    setPasteError(null);
    setPasting((n) => n + files.length);
    void (async () => {
      const ids: number[] = [];
      for (const file of files) {
        try {
          ids.push(await upload(file));
        } catch (err) {
          setPasteError(err instanceof ReportImageProblem
            ? err.message : formatApiError(err, 'Could not upload the pasted image.'));
        }
      }
      setPasting((n) => n - files.length);
      if (ids.length === 0) return;
      const current = latest.current;
      const edit = insertBlock(
        { value: current, start: Math.min(at.start, current.length), end: Math.min(at.end, current.length) },
        ids.map((id) => imageReference(id)).join('\n\n'),
      );
      if (maxLength !== undefined && edit.value.length > maxLength) {
        setPasteError('The image was uploaded, but its reference would make the text too long — insert it from Insert image after shortening the text.');
        return;
      }
      pendingSelection.current = { start: edit.start, end: edit.end };
      onChange(edit.value);
    })();
  };

  const joined = tablesAfterText(value);
  const fixTables = () => {
    const lines = value.split('\n');
    // Insert from the bottom so earlier line numbers stay right.
    for (const n of [...joined].reverse()) lines.splice(n - 1, 0, '');
    onChange(lines.join('\n'));
  };

  return (
    <div className="min-w-0 space-y-xxs">
      <div className="flex flex-wrap items-center gap-xxs" role="toolbar" aria-label={`${label} formatting`}>
        <div className="flex rounded-control border border-border p-px" role="group" aria-label="Mode">
          {(['Write', 'Preview'] as const).map((mode) => {
            const active = (mode === 'Preview') === preview;
            return (
              <button key={mode} type="button" aria-pressed={active} disabled={disabled}
                onClick={() => setPreview(mode === 'Preview')}
                className={`rounded-sm px-xs py-px text-caption ${active ? 'bg-muted font-medium text-foreground' : 'text-muted-foreground hover:text-foreground'}`}>
                {mode}
              </button>
            );
          })}
        </div>
        {tools.map((t) => (
          // size="icon" (no side padding), made 28px: a padded small button
          // squeezed the icon to 4px wide.
          <Button key={t.label} type="button" variant="ghost" size="icon" className="size-7"
            aria-label={t.label} title={t.keys ? `${t.label} (${t.keys})` : t.label}
            disabled={disabled || preview} onClick={() => apply(t.run)}>
            <t.icon className="size-4 shrink-0" aria-hidden />
          </Button>
        ))}
        {images && (placeable.length === 0 ? (
          // A disabled button shows no tooltip in every browser: the span
          // carries the reason, and assistive technology reads it.
          <span title={words.none}>
            <Button type="button" variant="ghost" size="icon" className="size-7" disabled
              aria-label="Insert image" aria-describedby={`${id}-no-images`}>
              <ImagePlus className="size-4 shrink-0" aria-hidden />
            </Button>
            <span id={`${id}-no-images`} className="sr-only">{words.none}</span>
          </span>
        ) : (
          <Popover open={pickerOpen} onOpenChange={(open) => {
            if (open) {
              const el = ref.current;
              caret.current = el ? { start: el.selectionStart, end: el.selectionEnd } : null;
            }
            setPickerOpen(open);
          }}>
            <PopoverTrigger asChild>
              <Button type="button" variant="ghost" size="icon" className="size-7"
                aria-label="Insert image" title={words.title}
                disabled={disabled || preview}>
                <ImagePlus className="size-4 shrink-0" aria-hidden />
              </Button>
            </PopoverTrigger>
            <PopoverContent className="w-96 max-w-[90vw] space-y-xs text-caption">
              <p className="font-medium">Place an image here</p>
              <p className="text-muted-foreground">{words.picker}</p>
              <ul className="max-h-72 space-y-xxs overflow-y-auto" aria-label="Images to insert">
                {placeable.map((img) => {
                  const src = images.urls[img.id];
                  const loadFailed = !src && (source?.failed?.(img.id) ?? false);
                  const caption = img.caption?.trim() || '';
                  return (
                    <li key={img.id}>
                      <button type="button"
                        className="flex w-full min-w-0 items-center gap-xs rounded-control p-xxs text-left hover:bg-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                        aria-label={`Insert image ${img.id}: ${caption || img.filename}`}
                        onClick={() => {
                          setPickerOpen(false);
                          // An EMPTY reference (owner, 2026-10-02): the image's
                          // stored caption then always prints, so there is one
                          // caption to maintain.  Typed alt text still
                          // overrides it for that placement.
                          apply((e) => insertBlock(e, imageReference(img.id)), caret.current);
                        }}>
                        <span className="flex size-12 shrink-0 items-center justify-center overflow-hidden rounded-control border border-border bg-muted">
                          {src
                            ? <img src={src} alt="" className="size-full object-cover" />
                            : loadFailed
                              ? <ImageOff className="size-4 text-warning" aria-hidden />
                              : <Loader2 className="size-4 animate-spin text-muted-foreground" aria-hidden />}
                        </span>
                        <span className="min-w-0 flex-1">
                          <span className="line-clamp-2 block [overflow-wrap:anywhere]">
                            {caption || <span className="italic text-muted-foreground">No caption — {img.filename}</span>}
                          </span>
                          <span className="block truncate text-muted-foreground">
                            Image {img.id}
                            {loadFailed && <span className="text-warning"> — the picture could not be loaded; it can still be placed</span>}
                          </span>
                        </span>
                      </button>
                    </li>
                  );
                })}
              </ul>
            </PopoverContent>
          </Popover>
        ))}
        <Popover>
          <PopoverTrigger asChild>
            <Button type="button" variant="ghost" size="sm" className="h-7 px-xs text-caption text-muted-foreground">
              <CircleHelp className="size-4" aria-hidden /> Markdown help
            </Button>
          </PopoverTrigger>
          <PopoverContent className="w-96 max-w-[90vw] space-y-xs text-caption">
            <p className="font-medium">What the report prints</p>
            <dl className="grid grid-cols-[minmax(0,9rem)_minmax(0,1fr)] gap-x-sm gap-y-xxs">
              {GUIDE.map(([syntax, meaning]) => (
                <React.Fragment key={syntax}>
                  <dt className="whitespace-pre-wrap break-words font-mono">{syntax}</dt>
                  <dd className="min-w-0 break-words text-muted-foreground">{meaning}</dd>
                </React.Fragment>
              ))}
            </dl>
            {images ? (
              <p className="text-muted-foreground">
                Images: {words.guide} — use Insert image, which writes{' '}
                <span className="font-mono">![](evidence:57)</span>. Empty brackets print the image&apos;s own
                caption; text in the brackets replaces it for that place. Any other image prints as its
                text. Not printed: HTML (shown as text). A heading prints as bold text — the report&apos;s sections
                are the template&apos;s.
              </p>
            ) : (
              <p className="text-muted-foreground">
                Not printed: images (attach screenshots to the finding and mark them &quot;In report&quot;),
                HTML (shown as text). A heading prints as bold text — the report&apos;s sections are the template&apos;s.
              </p>
            )}
          </PopoverContent>
        </Popover>
      </div>
      {preview ? (
        <div className="min-h-16 rounded-control border border-input bg-card px-sm py-xs text-body" data-testid={`${id}-preview`}>
          {value.trim()
            ? <SafeMarkdown text={value} evidence={images?.resolver} />
            : <span className="text-muted-foreground">Nothing written yet</span>}
        </div>
      ) : (
        <Textarea ref={ref} id={id} rows={rows} maxLength={maxLength} value={value} disabled={disabled}
          onKeyDown={onKeyDown} onPaste={onPaste} onChange={(e) => onChange(e.target.value)} className="font-mono" />
      )}
      {upload && !preview && (pasting > 0 || pasteError) && (
        <p role={pasteError ? 'alert' : 'status'}
          className={`text-caption [overflow-wrap:anywhere] ${pasteError ? 'text-destructive' : 'text-muted-foreground'}`}>
          {pasting > 0
            ? <><Loader2 className="mr-xxs inline size-3 animate-spin" aria-hidden />Uploading the pasted image…</>
            : pasteError}
        </p>
      )}
      {joined.length > 0 && (
        <p className="flex flex-wrap items-center gap-xs text-caption text-warning">
          <span className="min-w-0 break-words">
            {joined.length === 1 ? `The table on line ${joined[0]} follows` : `The tables on lines ${joined.join(', ')} follow`}{' '}
            a line of text with no blank line between — the report prints {joined.length === 1 ? 'it' : 'them'} as text.
          </span>
          <Button type="button" variant="outline" size="sm" className="h-6 px-xs" onClick={fixTables} disabled={disabled}>
            Add the blank line{joined.length === 1 ? '' : 's'}
          </Button>
        </p>
      )}
    </div>
  );
};

export default MarkdownField;
