#!/usr/bin/env python3
"""
Post-process the Word report — the two things Word styles cannot do:

1. A tight border around every screenshot.  A paragraph border (a style
   setting) spans the whole text width, and a DrawingML outline (a:ln) sits on
   the shape boundary and renders unevenly on pictures, so each image
   paragraph is wrapped in a single-cell table sized to the image, whose cell
   border frames it exactly.

2. The template's own images (v2.407.0).  The shipped reference.docx carries
   placeholder pictures in its headers and footers, named `bluestick-<asset
   id>` (the drawing's name, not its alt text): YOUR LOGO → `logo`, the title
   page's COVER IMAGE → `cover`.  When that asset is installed (template.json
   → assets, e.g. img/cover.jpg), it takes the placeholder's place, scaled to
   fit the placeholder's box without distortion.  An operator's own Word
   styles file (branding/reference.docx) has no such names, so its pictures
   are left alone.

3. Table alignment (v2.410.1).  The `Table` style in reference.docx centres
   tables, but viewers ignored a style-level table alignment, so each table
   without its own gets the alignment its style declares.

4. The classification.  The shipped reference.docx prints the word
   CLASSIFICATION in the header and footer of every page; the report's own
   (data.json → engagement.classification, beside this script when BlueStick
   runs it) is written in its place.  Before this the word itself was issued.

6. Bookmarks inside their headings (owner, 2026-10-14).  Pandoc puts a
   section's bookmark around the whole section at body level; Word sent
   every link to one (the summary tables' links to the findings) to the top
   of the document.  Each is moved into its heading paragraph, Word's shape.

5. Table captions under their tables (owner, 2026-10-14) — and a code
   block's "Listing N" caption under its code the same way.  A "Table N: …"
   caption follows its table; the table's last row keeps with it, and the
   caption itself does not keep with what follows (the Table Caption style
   was made for a caption above).  Per paragraph, because which side of its
   table a caption is on is not something a style can know.

Everything else the original prototype's script did is now either a style in
reference.docx or no longer needed (checked by rendering the template with
screenshots, 2026-09-23):
  - flattening Quarto's wrapper tables around cross-referenced tables, figures
    and listings, moving their captions out and pulling images out of them —
    the template uses no #tbl-/#fig-/#lst- blocks, so Quarto emits no wrappers
    (every one of those steps changed nothing);
  - centring captions and images — the ImageCaption, TableCaption, Figure and
    Captioned Figure styles in reference.docx are centred;
  - left-aligning every table cell — Pandoc already aligns each column as the
    Markdown table says, and forcing everything left undid the columns the
    template centres (the severity counts);
  - left-aligning code blocks — Source Code is left-aligned by default.

Usage:
    python fix-docx-report.py <input.docx> [-o OUTPUT] [-v]
"""

import argparse
import json
import os
import re
import shutil
import struct
import sys
import tempfile
import xml.etree.ElementTree as ET
import zipfile

WML = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
NS = f"{{{WML}}}"
DML = "http://schemas.openxmlformats.org/drawingml/2006/main"

# Keep Word's conventional prefixes when ElementTree writes document.xml back
# (otherwise every element comes out as ns0:… — valid, but not what Word or
# other tools write, and some tools expect w:).
for _prefix, _uri in {
    "w": WML,
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
    "wp": "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing",
    "a": DML,
    "pic": "http://schemas.openxmlformats.org/drawingml/2006/picture",
    "m": "http://schemas.openxmlformats.org/officeDocument/2006/math",
    "v": "urn:schemas-microsoft-com:vml",
    "o": "urn:schemas-microsoft-com:office:office",
    "w10": "urn:schemas-microsoft-com:office:word",
    "wp14": "http://schemas.microsoft.com/office/word/2010/wordprocessingDrawing",
    "mc": "http://schemas.openxmlformats.org/markup-compatibility/2006",
}.items():
    ET.register_namespace(_prefix, _uri)


def fill_table_of_contents(body):
    """Write the table of contents' entries into its field, so it is never
    empty in a viewer that does not compute fields.

    Pandoc writes the table as a TOC field with no result.  Word fills it
    only when it updates fields (`ask_word_to_update_fields`) — which it does
    not do in Protected View, where a downloaded file opens — and LibreOffice
    may not at all: the report opened with an empty table of contents (owner,
    2026-10-14).  Here the field's result is the document's level-1 and
    level-2 headings, one TOC1 / TOC2 paragraph each, linked to the heading's
    bookmark when it has one.  No page numbers (only a layout engine knows
    them); Word replaces the whole result with its own, numbered, when it
    updates the field.  Returns the number of entries written."""
    sdt = None
    for candidate in body.iter(f"{NS}sdt"):
        gallery = candidate.find(f".//{NS}docPartGallery")
        if gallery is not None and gallery.get(f"{NS}val") == "Table of Contents":
            sdt = candidate
            break
    if sdt is None:
        return 0
    content = sdt.find(f"{NS}sdtContent")
    field_p = None
    for p in content.findall(f"{NS}p"):
        instr = "".join(t.text or "" for t in p.iter(f"{NS}instrText"))
        if instr.strip().startswith("TOC"):
            field_p = p
            break
    if field_p is None:
        return 0
    # Begin, instruction and separate stay where they are; the END marker —
    # in the same run as the others, as Pandoc writes it — moves to a run in a
    # paragraph of its own after the entries, so the entries are the field's
    # result (Word replaces them, rather than adding a second table).
    end_fld = end_holder = None
    for run in field_p.findall(f"{NS}r"):
        for fld in run.findall(f"{NS}fldChar"):
            if fld.get(f"{NS}fldCharType") == "end":
                end_fld, end_holder = fld, run
    if end_fld is None:
        return 0
    # A heading's bookmark is the body-level element just before it.
    entries, pending = [], None
    for child in list(body):
        if child.tag == f"{NS}bookmarkStart":
            pending = child.get(f"{NS}name")
            continue
        if child.tag != f"{NS}p":
            pending = None
            continue
        style = child.find(f"{NS}pPr/{NS}pStyle")
        level = {"Heading1": 1, "Heading2": 2}.get(style.get(f"{NS}val") if style is not None else "")
        mark = child.find(f"{NS}bookmarkStart")
        anchor = mark.get(f"{NS}name") if mark is not None else pending
        pending = None
        text = "".join(t.text or "" for t in child.iter(f"{NS}t")).strip() if level else ""
        if text:
            entries.append((level, text, anchor))
    if not entries:
        return 0
    end_holder.remove(end_fld)
    end_run = ET.Element(f"{NS}r")
    end_run.append(end_fld)
    position = list(content).index(field_p) + 1
    for level, text, anchor in entries:
        para = ET.Element(f"{NS}p")
        ppr = ET.SubElement(para, f"{NS}pPr")
        ET.SubElement(ppr, f"{NS}pStyle", {f"{NS}val": f"TOC{level}"})
        holder = ET.SubElement(para, f"{NS}hyperlink", {f"{NS}anchor": anchor}) if anchor else para
        run = ET.SubElement(holder, f"{NS}r")
        t = ET.SubElement(run, f"{NS}t", {"{http://www.w3.org/XML/1998/namespace}space": "preserve"})
        t.text = text
        content.insert(position, para)
        position += 1
    closing = ET.Element(f"{NS}p")
    closing.append(end_run)
    content.insert(position, closing)
    return len(entries)


#: What Word accepts as a bookmark name: a letter first, then letters, digits
#: and underscores, at most 40 characters.
_WORD_BOOKMARK = re.compile(r"[A-Za-z][A-Za-z0-9_]{0,39}")


def word_safe_bookmark_names(body):
    """Give every bookmark a name Word accepts, and point every link at it.

    Pandoc names a heading's bookmark after its id — `sec-finding-11`,
    `project-information` — with hyphens, which a Word bookmark name may not
    hold (letters, digits and underscores, a letter first, at most 40
    characters).  In Word every link from the summary tables to a finding went
    to the top of the document (owner, 2026-10-14, and again 2026-10-21 after
    the bookmarks were moved into their headings).  Each name Word would not
    accept becomes one it does (`sec_finding_11`; a number on the end keeps two
    apart), and every `w:hyperlink w:anchor` and REF / PAGEREF field naming it
    follows.  A name Word accepts is left as it is.  Returns the number
    renamed."""
    starts = list(body.iter(f"{NS}bookmarkStart"))
    taken = {s.get(f"{NS}name") for s in starts if _WORD_BOOKMARK.fullmatch(s.get(f"{NS}name") or "")}
    renamed = {}
    for start in starts:
        name = start.get(f"{NS}name") or ""
        if _WORD_BOOKMARK.fullmatch(name) or name.startswith("_"):
            continue   # Word's own hidden bookmarks (_Toc…, _GoBack) start with "_"
        if name in renamed:
            start.set(f"{NS}name", renamed[name])
            continue
        base = re.sub(r"[^A-Za-z0-9_]", "_", name)
        if not base[:1].isalpha():
            base = "b_" + base
        base = base[:40]
        candidate, n = base, 1
        while candidate in taken:
            n += 1
            suffix = f"_{n}"
            candidate = base[:40 - len(suffix)] + suffix
        taken.add(candidate)
        renamed[name] = candidate
        start.set(f"{NS}name", candidate)
    if not renamed:
        return 0
    for link in body.iter(f"{NS}hyperlink"):
        anchor = link.get(f"{NS}anchor")
        if anchor in renamed:
            link.set(f"{NS}anchor", renamed[anchor])
    field = re.compile(r"(\b(?:PAGEREF|REF)\s+)(\S+)")
    for instr in body.iter(f"{NS}instrText"):
        if instr.text:
            instr.text = field.sub(lambda m: m.group(1) + renamed.get(m.group(2), m.group(2)), instr.text)
    return len(renamed)


def put_bookmarks_in_their_paragraphs(body):
    """Pandoc writes a section's bookmark at BODY level: a `bookmarkStart`
    before the heading paragraph and its `bookmarkEnd` after the section's
    last element.  Word does not take such a bookmark as a place to go: in
    Word every link to a finding from the summary tables went to the top of
    the document (owner, 2026-10-14).  Word's own shape is a bookmark inside
    the heading paragraph, around its text; each body-level bookmark is moved
    there — into the first paragraph that follows it (a table's first
    paragraph when a table comes first), its end at that paragraph's end.  A
    bookmark with nothing after it to hold it is left where it is.  Returns
    the number moved."""
    ends = {}
    for end in body.iter(f"{NS}bookmarkEnd"):
        ends.setdefault(end.get(f"{NS}id"), end)
    parents = {child: parent for parent in body.iter() for child in parent}
    moved = 0
    children = list(body)
    for index, start in enumerate(children):
        if start.tag != f"{NS}bookmarkStart":
            continue
        holder = None
        for following in children[index + 1:]:
            if following.tag == f"{NS}p":
                holder = following
            elif following.tag != f"{NS}bookmarkStart":
                holder = following.find(f".//{NS}p")
            if holder is not None or following.tag != f"{NS}bookmarkStart":
                break
        if holder is None:
            continue
        end = ends.get(start.get(f"{NS}id"))
        body.remove(start)
        if end is not None and end in parents:
            parents[end].remove(end)
        ppr = holder.find(f"{NS}pPr")
        holder.insert(list(holder).index(ppr) + 1 if ppr is not None else 0, start)
        holder.append(end if end is not None else ET.Element(f"{NS}bookmarkEnd", {f"{NS}id": start.get(f"{NS}id")}))
        moved += 1
    return moved


def add_image_borders(body):
    """Wrap each top-level image paragraph in a single-cell table with a tight
    border.  The cell border sits outside the cell content, giving the
    tightest possible frame — unlike a:ln, which is centred on the shape
    boundary and can render asymmetrically on pictures."""
    targets = []
    for idx, child in enumerate(list(body)):
        if child.tag != f"{NS}p":
            continue
        drawing = child.find(f".//{NS}drawing")
        if drawing is None:
            continue

        # The image's size from wp:extent (EMU → twips).
        width_emu = height_emu = None
        for extent in drawing.iter():
            if extent.tag.endswith("}extent") and extent.get("cx") and extent.get("cy"):
                width_emu, height_emu = int(extent.get("cx")), int(extent.get("cy"))
                break
        if width_emu is None:
            continue

        # No other outline or paragraph border competing with the frame.
        for spPr in drawing.iter():
            if spPr.tag.endswith("}spPr"):
                for ln in spPr.findall(f"{{{DML}}}ln"):
                    spPr.remove(ln)
                break
        pPr = child.find(f"{NS}pPr")
        if pPr is None:
            pPr = ET.SubElement(child, f"{NS}pPr")
        for pBdr in pPr.findall(f"{NS}pBdr"):
            pPr.remove(pBdr)
        # No paragraph spacing, so the cell fits the image exactly.
        for sp in pPr.findall(f"{NS}spacing"):
            pPr.remove(sp)
        spacing = ET.SubElement(pPr, f"{NS}spacing")
        spacing.set(f"{NS}before", "0")
        spacing.set(f"{NS}after", "0")

        targets.append((idx, child, width_emu // 635, height_emu // 635))

    children = list(body)
    for idx, img_p, width, height in targets:
        tbl = ET.Element(f"{NS}tbl")
        tblPr = ET.SubElement(tbl, f"{NS}tblPr")
        tblW = ET.SubElement(tblPr, f"{NS}tblW")
        tblW.set(f"{NS}w", str(width))
        tblW.set(f"{NS}type", "dxa")
        ET.SubElement(tblPr, f"{NS}jc").set(f"{NS}val", "center")
        borders = ET.SubElement(tblPr, f"{NS}tblBorders")
        for side in ("top", "left", "bottom", "right"):
            b = ET.SubElement(borders, f"{NS}{side}")
            b.set(f"{NS}val", "single")
            b.set(f"{NS}sz", "6")  # 3/4 pt
            b.set(f"{NS}color", "000000")
            b.set(f"{NS}space", "0")
        margins = ET.SubElement(tblPr, f"{NS}tblCellMar")  # 2 twips ≈ 0.035 mm
        for side in ("top", "left", "bottom", "right"):
            m = ET.SubElement(margins, f"{NS}{side}")
            m.set(f"{NS}w", "2")
            m.set(f"{NS}type", "dxa")
        ET.SubElement(ET.SubElement(tbl, f"{NS}tblGrid"), f"{NS}gridCol").set(f"{NS}w", str(width))
        tr = ET.SubElement(tbl, f"{NS}tr")
        # An exact row height matching the image removes the baseline gap.
        tr_height = ET.SubElement(ET.SubElement(tr, f"{NS}trPr"), f"{NS}trHeight")
        tr_height.set(f"{NS}val", str(height))
        tr_height.set(f"{NS}hRule", "exact")
        ET.SubElement(tr, f"{NS}tc").append(img_p)
        children[idx] = tbl

    for child in list(body):
        body.remove(child)
    for elem in children:
        body.append(elem)
    return len(targets)


# --- 2. the template's own images --------------------------------------------

# Where each asset is when the template folder has no readable template.json
# (the report's source bundle leaves it out).
DEFAULT_ASSETS = {"logo": "img/logo.png", "cover": "img/cover.jpg"}
CONTENT_TYPES = {"png": "image/png", "jpeg": "image/jpeg", "gif": "image/gif"}
_JPEG_SOF = {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}


def image_info(data):
    """(kind, width, height) of a PNG, JPEG or GIF, else None — read from the
    file's header, so no imaging library is needed."""
    if data[:8] == b"\x89PNG\r\n\x1a\n" and len(data) >= 24:
        w, h = struct.unpack(">II", data[16:24])
        return ("png", w, h) if w and h else None
    if data[:6] in (b"GIF87a", b"GIF89a") and len(data) >= 10:
        w, h = struct.unpack("<HH", data[6:10])
        return ("gif", w, h) if w and h else None
    if data[:2] == b"\xff\xd8":
        i = 2
        while i + 9 < len(data):
            if data[i] != 0xFF:
                i += 1
                continue
            marker = data[i + 1]
            if marker in _JPEG_SOF:
                h, w = struct.unpack(">HH", data[i + 5:i + 9])
                return ("jpeg", w, h) if w and h else None
            if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
                i += 2
                continue
            i += 2 + struct.unpack(">H", data[i + 2:i + 4])[0]
    return None


def installed_assets(base):
    """{asset id: (kind, width, height, bytes)} for each installed image."""
    paths = dict(DEFAULT_ASSETS)
    try:
        with open(os.path.join(base, "template.json"), encoding="utf-8") as f:
            for a in json.load(f).get("assets") or []:
                if isinstance(a, dict) and a.get("id") and a.get("path") and not a.get("replaces"):
                    paths[str(a["id"])] = str(a["path"])
    except (OSError, ValueError):
        pass
    found = {}
    for asset_id, rel in paths.items():
        path = os.path.join(base, rel)
        if os.path.isfile(path) and not os.path.islink(path):
            with open(path, "rb") as f:
                data = f.read()
            info = image_info(data)
            if info:
                found[asset_id] = (*info, data)
    return found


def apply_template_images(tmpdir, assets):
    """Put each installed asset in place of the placeholder drawings named
    `bluestick-<id>` in the headers and footers.  Returns the parts to add
    to the package, the placeholder pictures removed from it (nothing points
    at them any more) and the number of drawings replaced."""
    word = os.path.join(tmpdir, "word")
    new_parts, removed, replaced = [], [], 0
    if not assets:
        return new_parts, removed, replaced
    replaced_targets = set()  # the placeholder pictures' media parts
    for part in sorted(os.listdir(word)):
        if not re.match(r"^(header|footer)\d+\.xml$", part):
            continue
        rels_path = os.path.join(word, "_rels", part + ".rels")
        if not os.path.isfile(rels_path):
            continue
        with open(os.path.join(word, part), encoding="utf-8") as f:
            xml = f.read()
        with open(rels_path, encoding="utf-8") as f:
            rels = f.read()
        count = 0

        def swap(m):
            nonlocal rels, count
            block = m.group(0)
            name = re.search(r'<wp:docPr [^>]*\bname="bluestick-([A-Za-z0-9_-]+)"', block)
            embed = re.search(r'r:embed="([^"]+)"', block)
            extent = re.search(r'<wp:extent cx="(\d+)" cy="(\d+)"', block)
            if not (name and embed and extent) or name.group(1) not in assets:
                return block
            kind, width, height, data = assets[name.group(1)]
            media = f"media/bluestick-{name.group(1)}.{kind}"
            target = os.path.join(word, media)
            if not os.path.exists(target):
                with open(target, "wb") as f:
                    f.write(data)
                new_parts.append(f"word/{media}")
            def retarget(r):
                replaced_targets.add(r.group(2))
                return r.group(1) + media + r.group(3)
            rels = re.sub(
                r'(<Relationship\b[^>]*\bId="%s"[^>]*?\bTarget=")([^"]*)(")' % re.escape(embed.group(1)),
                retarget, rels,
            )
            # Fit the placeholder's box, keeping the image's proportions.
            box_w, box_h = int(extent.group(1)), int(extent.group(2))
            scale = min(box_w / width, box_h / height)
            cx, cy = str(int(width * scale)), str(int(height * scale))
            block = re.sub(r'<wp:extent cx="\d+" cy="\d+"', f'<wp:extent cx="{cx}" cy="{cy}"', block)
            block = re.sub(r'<a:ext cx="\d+" cy="\d+"', f'<a:ext cx="{cx}" cy="{cy}"', block)
            # A floating picture (the title page image) stays centred in the
            # placeholder's box — a portrait photo in a landscape box would
            # otherwise sit against the box's left edge.
            for axis, spare in (("H", box_w - int(cx)), ("V", box_h - int(cy))):
                block = re.sub(
                    r"(<wp:position%s\b[^>]*>\s*<wp:posOffset>)(-?\d+)(</wp:posOffset>)" % axis,
                    lambda p: f"{p.group(1)}{int(p.group(2)) + spare // 2}{p.group(3)}", block,
                )
            count += 1
            return block

        xml = re.sub(r"<w:drawing>.*?</w:drawing>", swap, xml, flags=re.S)
        if count:
            with open(os.path.join(word, part), "w", encoding="utf-8") as f:
                f.write(xml)
            with open(rels_path, "w", encoding="utf-8") as f:
                f.write(rels)
            replaced += count

    if new_parts:
        # A placeholder picture nothing points at any more leaves the package.
        rels_dir = os.path.join(word, "_rels")
        still_used = "".join(
            open(os.path.join(rels_dir, n), encoding="utf-8").read() for n in os.listdir(rels_dir) if n.endswith(".rels")
        )
        for target in sorted(replaced_targets):
            part = f"word/{target}"
            if target.startswith("media/") and f'Target="{target}"' not in still_used:
                os.remove(os.path.join(tmpdir, part))
                removed.append(part)

        # Every image kind used needs a content type; a removed part's
        # override goes with it.
        ct_path = os.path.join(tmpdir, "[Content_Types].xml")
        with open(ct_path, encoding="utf-8") as f:
            ct = f.read()
        for part in new_parts:
            ext = part.rsplit(".", 1)[1]
            if not re.search(r'<Default Extension="%s"' % ext, ct, re.I):
                ct = ct.replace("</Types>", f'<Default Extension="{ext}" ContentType="{CONTENT_TYPES[ext]}"/></Types>')
        for part in removed:
            ct = re.sub(r'<Override PartName="/%s"[^>]*/>' % re.escape(part), "", ct)
        with open(ct_path, "w", encoding="utf-8") as f:
            f.write(ct)
    return new_parts, removed, replaced


# CT_TblPr's child order (ECMA-376): jc goes after these, before the rest.
_BEFORE_JC = ("tblStyle", "tblpPr", "tblOverlap", "bidiVisual", "tblStyleRowBandSize",
              "tblStyleColBandSize", "tblW")


CLASSIFICATION_PLACEHOLDER = "CLASSIFICATION"
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")


def report_classification(base):
    """The report's classification (data.json → engagement.classification),
    as one line of plain text, or '' when none is written."""
    try:
        with open(os.path.join(base, "data.json"), encoding="utf-8") as fh:
            value = (json.load(fh).get("engagement") or {}).get("classification")
    except (OSError, ValueError, AttributeError):
        return ""
    if not isinstance(value, str):
        return ""
    return " ".join(_CONTROL.sub(" ", value).split())[:100]


def apply_classification(tmpdir, classification):
    """Write the report's classification where the shipped reference.docx
    prints the word CLASSIFICATION — the header and footer of every page.
    Only a run whose whole text is that word is changed, so an operator's own
    Word styles file (which says something else there) is left alone; with no
    classification written the placeholder stays, a gap a reviewer can see.
    Returns the number of places changed."""
    if not classification:
        return 0
    text = (classification.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))
    pattern = re.compile(r"(<w:t(?: [^>]*)?>)" + CLASSIFICATION_PLACEHOLDER + r"(</w:t>)")
    changed = 0
    word = os.path.join(tmpdir, "word")
    for name in sorted(os.listdir(word)):
        if not re.fullmatch(r"(header|footer)\d*\.xml", name):
            continue
        path = os.path.join(word, name)
        with open(path, encoding="utf-8") as fh:
            xml = fh.read()
        new, n = pattern.subn(lambda m: m.group(1) + text + m.group(2), xml)
        if n:
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(new)
            changed += n
    return changed


def align_tables_like_their_style(body, styles_path):
    """Give every table the alignment its table style declares (v2.410.1).

    reference.docx centres tables in the `Table` style, so a narrow table sits
    under its centred "Table N" caption.  LibreOffice ignores a style-level
    table alignment, and so did the viewer of the first report rendered with
    it — the tables stayed at the margin — so it is copied onto each table
    that has none of its own.
    The style stays the source of truth: an operator's Word styles file whose
    table style is not centred leaves its tables where they are."""
    try:
        styles = ET.parse(styles_path).getroot()
    except (OSError, ET.ParseError):
        return 0
    style_jc = {}
    for style in styles.findall(f"{NS}style"):
        if style.get(f"{NS}type") != "table":
            continue
        jc = style.find(f"{NS}tblPr/{NS}jc")
        if jc is not None and jc.get(f"{NS}val"):
            style_jc[style.get(f"{NS}styleId")] = jc.get(f"{NS}val")
    count = 0
    for tbl in body.iter(f"{NS}tbl"):
        pr = tbl.find(f"{NS}tblPr")
        if pr is None or pr.find(f"{NS}jc") is not None:
            continue
        style = pr.find(f"{NS}tblStyle")
        val = style_jc.get(style.get(f"{NS}val")) if style is not None else None
        if not val:
            continue
        at = 0
        for i, child in enumerate(list(pr)):
            if child.tag.split("}")[-1] in _BEFORE_JC:
                at = i + 1
        jc = ET.Element(f"{NS}jc")
        jc.set(f"{NS}val", val)
        pr.insert(at, jc)
        count += 1
    return count


# CT_PPrBase's child order (ECMA-376), as far as `spacing`.
_PPR_ORDER = ("pStyle", "keepNext", "keepLines", "pageBreakBefore", "framePr", "widowControl", "numPr",
              "suppressLineNumbers", "pBdr", "shd", "tabs", "suppressAutoHyphens", "kinsoku", "wordWrap",
              "overflowPunct", "topLinePunct", "autoSpaceDE", "autoSpaceDN", "bidi", "adjustRightInd",
              "snapToGrid", "spacing")


def _set_paragraph_property(p, element):
    """Put `element` in the paragraph's pPr in its schema place, replacing
    one of the same name."""
    pPr = p.find(f"{NS}pPr")
    if pPr is None:
        pPr = ET.Element(f"{NS}pPr")
        p.insert(0, pPr)
    for old in pPr.findall(element.tag):
        pPr.remove(old)
    name = element.tag.split("}")[-1]
    earlier = set(_PPR_ORDER[:_PPR_ORDER.index(name)])
    at = 0
    for i, child in enumerate(list(pPr)):
        if child.tag.split("}")[-1] in earlier:
            at = i + 1
    pPr.insert(at, element)


def _table_caption_style(styles_path):
    """(style ids, {before/after attribute: value}) of the "Table Caption"
    paragraph style: the ids it goes by, and the spacing it declares."""
    try:
        styles = ET.parse(styles_path).getroot()
    except (OSError, ET.ParseError):
        return set(), {}
    ids, spacing = set(), {}
    for style in styles.findall(f"{NS}style"):
        name = style.find(f"{NS}name")
        if style.get(f"{NS}type") != "paragraph" or name is None or name.get(f"{NS}val") != "Table Caption":
            continue
        ids.add(style.get(f"{NS}styleId"))
        own = style.find(f"{NS}pPr/{NS}spacing")
        if own is not None:
            spacing = {k: v for k, v in own.attrib.items() if re.fullmatch(r"\{.*\}(before|after)(Lines|Autospacing)?", k)}
    return ids, spacing


def _is_code(element):
    """A code block's paragraph (Pandoc's Source Code style)."""
    if element.tag != f"{NS}p":
        return False
    style = element.find(f"{NS}pPr/{NS}pStyle")
    return style is not None and style.get(f"{NS}val") == "SourceCode"


def keep_table_captions_with_their_tables(body, styles_path):
    """A table's caption is printed UNDER it (owner, 2026-10-14), and must not
    start a page its table ended on the one before.

    The Table Caption style in the shipped reference.docx was made for a
    caption ABOVE its table: it keeps with the next paragraph and has its
    space before.  Under a table that would tie the caption to whatever
    follows, so each caption that follows a table is mirrored, on the
    paragraph itself: no keep-with-next, and the style's space before and
    after swapped (an operator's styles file is otherwise left to say how a
    caption looks — and a caption of theirs ABOVE a table keeps the style as
    it is).  The table's LAST ROW keeps with next instead — Word's way of
    holding a table's end and the paragraph after it on one page — so the
    caption takes at least that row with it; a long table still breaks
    across pages as before.  A code block's caption ("Listing N", in the same
    style) is treated alike: the code paragraph keeps with it.  Returns the
    number of captions."""
    ids, spacing = _table_caption_style(styles_path)
    if not ids:
        return 0
    mirrored = {}
    for key, value in spacing.items():
        ns, local = key.split("}")
        other = local.replace("before", "\0").replace("after", "before").replace("\0", "after")
        mirrored[f"{ns}}}{other}"] = value
    count = 0
    for parent in [body, *body.iter(f"{NS}tc")]:
        children = list(parent)
        for table, after in zip(children, children[1:]):
            if after.tag != f"{NS}p" or not (table.tag == f"{NS}tbl" or _is_code(table)):
                continue
            style = after.find(f"{NS}pPr/{NS}pStyle")
            if style is None or style.get(f"{NS}val") not in ids:
                continue
            if table.tag == f"{NS}p":
                # A code block: one Source Code paragraph.
                _set_paragraph_property(table, ET.Element(f"{NS}keepNext"))
            rows = table.findall(f"{NS}tr")
            if rows:
                for p in rows[-1].iter(f"{NS}p"):
                    _set_paragraph_property(p, ET.Element(f"{NS}keepNext"))
            off = ET.Element(f"{NS}keepNext")
            off.set(f"{NS}val", "false")
            _set_paragraph_property(after, off)
            if mirrored:
                sp = ET.Element(f"{NS}spacing")
                for key, value in mirrored.items():
                    sp.set(key, value)
                _set_paragraph_property(after, sp)
            count += 1
    return count


def ask_word_to_update_fields(tmpdir):
    """Have Word fill in the document's fields when it opens it.

    Pandoc writes the table of contents as a Word FIELD with no result: it is
    computed by the program that opens the file, and Word computes it only
    when asked — so every report opened with an empty table of contents
    (owner, 2026-10-14).  `w:updateFields` in settings.xml is that request:
    Word fills the table (with page numbers) on opening, after asking once.
    Returns True when the setting was added."""
    path = os.path.join(tmpdir, "word", "settings.xml")
    try:
        with open(path, encoding="utf-8") as fh:
            xml = fh.read()
    except OSError:
        return False
    if "w:updateFields" in xml:
        return False
    new, n = re.subn(r"(<w:settings\b[^>]*>)", r'\1<w:updateFields w:val="true"/>', xml, count=1)
    if not n:
        return False
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(new)
    return True


def fix_document(docx_path, verbose=False, assets_dir="."):
    tmpdir = tempfile.mkdtemp()
    try:
        with zipfile.ZipFile(docx_path, "r") as zf:
            names = zf.namelist()
            zf.extractall(tmpdir)

        doc_xml = os.path.join(tmpdir, "word", "document.xml")
        tree = ET.parse(doc_xml)
        body = tree.getroot().find(f"{NS}body")
        # Before the image frames: those carry their own centred alignment.
        styles_path = os.path.join(tmpdir, "word", "styles.xml")
        aligned = align_tables_like_their_style(body, styles_path)
        kept = keep_table_captions_with_their_tables(body, styles_path)
        count = add_image_borders(body)
        bookmarks = put_bookmarks_in_their_paragraphs(body)
        renamed = word_safe_bookmark_names(body)
        fill_table_of_contents(body)
        tree.write(doc_xml, xml_declaration=True, encoding="UTF-8")

        new_parts, removed, replaced = apply_template_images(tmpdir, installed_assets(assets_dir))
        classified = apply_classification(tmpdir, report_classification(assets_dir))
        ask_word_to_update_fields(tmpdir)

        # Same parts, same order ([Content_Types].xml first, as Word expects).
        with zipfile.ZipFile(docx_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for name in [n for n in names if n not in removed] + new_parts:
                zf.write(os.path.join(tmpdir, name), name)

        if verbose:
            print(f"  Aligned {aligned} table(s) as their style says, kept {kept} caption(s) with their table, "
                  f"added a border to {count} image(s), put {bookmarks} bookmark(s) in their heading, "
                  f"gave {renamed} bookmark(s) a name Word accepts, "
                  f"placed {replaced} template image(s), wrote the classification in {classified} place(s) "
                  f"→ {docx_path}")
        return count
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def parse_args():
    parser = argparse.ArgumentParser(
        description="Frame every screenshot in a Quarto-generated docx and put the template's installed images in place of its placeholders.")
    parser.add_argument("input", help="The docx Quarto produced.")
    parser.add_argument("-o", "--output", default=None, help="Output path (default: overwrite the input).")
    parser.add_argument("--assets-dir", default=".",
                        help="The template folder holding template.json and the images (default: the current folder).")
    parser.add_argument("-v", "--verbose", action="store_true", help="Say what was changed.")
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    if not os.path.exists(args.input):
        print(f"Error: {args.input} not found")
        sys.exit(1)
    output_path = args.output or args.input
    if output_path != args.input:
        shutil.copy2(args.input, output_path)
    fix_document(output_path, verbose=args.verbose, assets_dir=args.assets_dir)
