--[[
BlueStick report fields (v2.381.0) — copied into every render as
_bluestick/fields.lua by app/services/quarto_render.py.

Written Markdown (a finding's description, the executive summary, …) never
enters the .qmd source.  The template emits an empty placeholder:

    ::: {.bs-md key="findings.3.description"}
    :::

and this filter replaces it with that value from data.json, parsed as GitHub
Markdown WITHOUT raw HTML, then cleaned: raw blocks become code, images are
reduced to their alt text (an image path would read a local file), links keep
only http/https/mailto targets, headings become bold paragraphs (the report's
own structure stays the template's), and every attribute is dropped.

NO MATH (review 2026-10-01 S1).  The text is read with the reader's math
extensions OFF (`READER` below), so `$HOME/bin:$PATH` is those characters;
`clean()` turns any Math node that appears all the same back into its source
text; and the templates set `html-math-method: plain` (the renderer passes it
too).  Before, `$…$` in a description became a formula: the HTML report
loaded MathJax from a CDN — a request from the client's browser, and a script
the report never had — which also typesets `\href{javascript:…}{x}`.

ONE image form survives: `![alt](evidence:57)`, when data.json lists 57 for
THAT field of THAT finding (`findings.N.placed.<field>` — the dataset builder
decides which of the finding's images, marked "In report", a field may show;
this filter never trusts the text).  It becomes a BlueStick figure — see
"Figures" below.  Any other `evidence:` reference is alt text like every
other image.

Templates list it AFTER quarto (`filters: [quarto, _bluestick/fields.lua]`),
so Quarto's shortcode handling is over before this text is added: a value
such as "{{< env DATABASE_URL >}}" stays text.
]]

local data = nil

local function load_data()
  if data ~= nil then return data end
  local file = io.open("data.json", "r")
  if file == nil then
    data = {}
    return data
  end
  local content = file:read("a")
  file:close()
  data = pandoc.json.decode(content, false)
  return data
end

local function resolve_any(key)
  local node = load_data()
  for part in key:gmatch("[^%.]+") do
    if type(node) ~= "table" then return nil end
    local index = tonumber(part)
    if index ~= nil then
      node = node[index + 1]
    else
      node = node[part]
    end
  end
  return node
end

local function resolve(key)
  local node = resolve_any(key)
  if type(node) == "string" then return node end
  return nil
end

local WEB = { http = true, https = true, mailto = true }

-- How written text is read: GitHub Markdown without raw HTML and without
-- math (`$…$`, `$$…$$`, and gfm's ```math block and $`…`$).  The names are
-- pandoc's own (`pandoc --list-extensions=gfm`); gfm has no fenced divs,
-- attributes, bracketed spans or raw attributes, so a `::: {.bs-md …}` an
-- author types is text, never one of this filter's placeholders.
local READER = "gfm-raw_html-tex_math_dollars-tex_math_gfm"

-- A Math node as the characters its author typed.
local function math_source(el)
  local mark = el.mathtype == "DisplayMath" and "$$" or "$"
  return pandoc.Str(mark .. el.text .. mark)
end

--[[
Figures.  Every evidence image BlueStick prints is built HERE, from data.json,
as a pandoc Figure of class `bs-figure`: the trailing evidence block's (the
template's `image(e)` emits `::: {.bs-figure file="evidence/57.png"}`) and
the ones an author placed in a written section.  The file must look like
`evidence/<digits>.<png|jpg|gif>` — the renderer put exactly those files in
the work folder — and the caption is plain text made into one Str, so nothing
in it is ever parsed.  `number_figures` (the Pandoc pass at the end) then
writes "Figure N: …" on each, counting in DOCUMENT order, which only this
filter can see: placed images do not exist when Jinja fills the template.
]]
local IMAGE_EXT = { png = true, jpg = true, gif = true }
local CAPTION_MAX = 2000
local DEFAULT_WIDTH = "6in"
local MARK = "bs-placed"

local function plain_text(value)
  if type(value) ~= "string" then return "" end
  local text = value:gsub("[\0-\31\127]", " ")
  text = text:gsub("%s+", " "):gsub("^ ", ""):gsub(" $", "")
  if pandoc.text.len(text) > CAPTION_MAX then text = pandoc.text.sub(text, 1, CAPTION_MAX) end
  return text
end

local function evidence_file(value)
  if type(value) ~= "string" then return nil end
  local ext = value:match("^evidence/%d+%.(%a+)$")
  if ext ~= nil and IMAGE_EXT[ext] then return value end
  return nil
end

local function valid_width(value)
  if type(value) ~= "string" then return DEFAULT_WIDTH end
  local unit = value:match("^%d+%.?%d*(%a+)$") or value:match("^%d+%.?%d*(%%)$")
  if unit == "in" or unit == "cm" or unit == "mm" or unit == "px" or unit == "%" then return value end
  return DEFAULT_WIDTH
end

-- A figure inside a list is indented: half an inch narrower per level, for a
-- width in inches (the shipped default), so it stays inside the margin.
local function narrowed(width, depth)
  local inches = width:match("^(%d+%.?%d*)in$")
  if inches == nil or depth < 1 then return width end
  local value = math.max(tonumber(inches) - 0.5 * depth, 2)
  return string.format("%gin", value)
end

local function figure(file, caption, width)
  local text = plain_text(caption)
  local inlines = pandoc.Inlines({})
  if text ~= "" then inlines:insert(pandoc.Str(text)) end
  local img = pandoc.Image(inlines, file, "", pandoc.Attr("", {}, { width = width }))
  return pandoc.Figure(
    { pandoc.Plain({ img }) },
    { long = { pandoc.Plain(inlines) } },
    pandoc.Attr("", { "bs-figure" })
  )
end

-- file → caption, over every finding's `images` and `evidence` lists.
local captions = nil

local function caption_of(file)
  if captions == nil then
    captions = {}
    local findings = load_data()["findings"]
    if type(findings) == "table" then
      for _, f in ipairs(findings) do
        if type(f) == "table" then
          -- By name, not ipairs over { images, evidence }: a dataset frozen
          -- before `images` existed has a nil there, which ends an ipairs.
          for _, name in ipairs({ "images", "evidence" }) do
            local list = f[name]
            if type(list) == "table" then
              for _, item in ipairs(list) do
                if type(item) == "table" and evidence_file(item["file"]) and captions[item["file"]] == nil then
                  captions[item["file"]] = plain_text(item["caption"])
                end
              end
            end
          end
        end
      end
    end
  end
  return captions[file] or ""
end

-- The images one written field may show: attachment id (string) →
-- { file, caption }, from `<item path>.placed.<field>` in data.json.
local function field_images(key)
  local parent, field = key:match("^(.*)%.([%a_]+)$")
  local out = {}
  if parent == nil then return out end
  local map = resolve_any(parent .. ".placed." .. field)
  if type(map) ~= "table" then return out end
  for id, item in pairs(map) do
    if type(id) == "string" and id:match("^%d+$") and type(item) == "table" and evidence_file(item["file"]) then
      out[id] = { file = item["file"], caption = plain_text(item["caption"]) }
    end
  end
  return out
end

local function is_marked(el)
  return el.t == "Image" and el.classes:includes(MARK)
end

local function blank(inlines)
  for _, el in ipairs(inlines) do
    if el.t ~= "Space" and el.t ~= "SoftBreak" and el.t ~= "LineBreak" then return false end
  end
  return true
end

--[[
Turn the marked images of a cleaned field into figures.  A figure is a BLOCK:
an image on its own line becomes one in place, one in the middle of a
paragraph splits the paragraph around it.  An image where a block cannot go
(inside a link or bold text, or a table cell — a figure in a cell would not be
framed in Word) leaves its caption there as text and the figure follows the
block.  `used` records every id that became a figure.
]]
local function lift(blocks, images, width, used, depth)
  local out = pandoc.List()

  local function make(el)
    local id = el.src
    local entry = images[id]
    used[id] = true
    local caption = pandoc.utils.stringify(el.caption)
    if caption:match("^%s*$") then caption = entry.caption end
    return figure(entry.file, caption, narrowed(width, depth))
  end

  -- Hoist: every marked image inside `node` becomes its alt text; its figure
  -- goes in `pending`.
  local function hoist(node, pending)
    return node:walk({
      Image = function(el)
        if not is_marked(el) then return nil end
        pending:insert(make(el))
        return el.caption
      end,
    })
  end

  for _, block in ipairs(blocks) do
    local t = block.t
    if t == "Para" or t == "Plain" then
      local run = pandoc.Inlines({})
      local pending = pandoc.List()
      local function flush()
        if not blank(run) then
          out:insert(t == "Para" and pandoc.Para(run) or pandoc.Plain(run))
        end
        run = pandoc.Inlines({})
      end
      for _, el in ipairs(block.content) do
        if is_marked(el) then
          flush()
          out:insert(make(el))
        else
          local kept = hoist(pandoc.Plain({ el }), pending)
          run:extend(kept.content)
        end
      end
      flush()
      out:extend(pending)
    elseif t == "BlockQuote" then
      block.content = lift(block.content, images, width, used, depth)
      out:insert(block)
    elseif t == "BulletList" or t == "OrderedList" then
      local items = {}
      for i, item in ipairs(block.content) do
        items[i] = lift(item, images, width, used, depth + 1)
      end
      block.content = items
      out:insert(block)
    elseif t == "Figure" then
      -- The reader's own figure around an image on its own line: ours
      -- replaces it.
      local pending = pandoc.List()
      hoist(pandoc.Blocks({ block }), pending)
      if #pending > 0 then out:extend(pending) else out:insert(block) end
    else
      local pending = pandoc.List()
      -- walk() needs a list for a single block of any other kind.
      local kept = hoist(pandoc.Blocks({ block }), pending)
      out:extend(kept)
      out:extend(pending)
    end
  end
  return out
end

local function clean(blocks, images, drop_images)
  images = images or {}
  return blocks:walk({
    RawBlock = function(el) return pandoc.CodeBlock(el.text) end,
    RawInline = function(el) return pandoc.Code(el.text) end,
    Image = function(el)
      local id = el.src:match("^evidence:(%d+)$")
      if id ~= nil and images[id] ~= nil then
        -- A template that prints no evidence (`md(…, images=false)`): the
        -- placed image goes, caption and all.
        if drop_images then return {} end
        -- Marked for lift(): alt text kept as plain text, the id as the
        -- source, nothing else of the author's image (title, attributes).
        local alt = pandoc.utils.stringify(el.caption)
        return pandoc.Image({ pandoc.Str(alt) }, id, "", pandoc.Attr("", { MARK }))
      end
      return el.caption
    end,
    Link = function(el)
      local scheme = el.target:match("^(%a[%w+.-]*):")
      if scheme ~= nil and WEB[scheme:lower()] then
        return pandoc.Link(el.content, el.target, el.title)
      end
      return el.content
    end,
    Header = function(el) return pandoc.Para({ pandoc.Strong(el.content) }) end,
    Div = function(el) return el.content end,
    Span = function(el) return el.content end,
    -- The second layer: READER parses no math, and if a Math node arrives
    -- anyway it is printed as its source, never as a formula.
    Math = math_source,
    CodeBlock = function(el)
      local lang = el.classes[1]
      -- `math` is not a language: gfm's ```math block is display math.
      if lang ~= nil and lang:match("^[%w_+-]+$") and lang:lower() ~= "math" then
        return pandoc.CodeBlock(el.text, pandoc.Attr("", { lang }))
      end
      return pandoc.CodeBlock(el.text)
    end,
    Code = function(el) return pandoc.Code(el.text) end,
  })
end

--[[
Document metadata (title, subtitle, …) from data.json.  User text must NOT be
written into the YAML front matter: Quarto expands shortcodes in metadata even
when the text is backslash-escaped.  The template names the data instead:

    bluestick-meta:
      title: report.title
      subtitle: report.heading

and this sets each key to that value as plain text, after Quarto's own
filters have run.
]]
function Meta(meta)
  local map = meta["bluestick-meta"]
  if map == nil then return nil end
  for key, path in pairs(map) do
    local p = pandoc.utils.stringify(path)
    if type(key) == "string" and key:match("^[%a][%w-]*$") and p:match("^[%a_][%w_%.]*$") then
      local value = resolve_any(p)
      if type(value) == "string" and not value:match("^%s*$") then
        meta[key] = pandoc.Inlines(value)
      elseif type(value) == "table" and #value > 0 then
        -- A list of strings (the authors): each one plain text.
        local items = pandoc.List()
        for _, v in ipairs(value) do
          if type(v) == "string" and not v:match("^%s*$") then items:insert(pandoc.Inlines(v)) end
        end
        meta[key] = #items > 0 and pandoc.MetaList(items) or nil
      else
        meta[key] = nil
      end
    end
  end
  meta["bluestick-meta"] = nil
  return meta
end

--[[
TODO placeholders (v2.382.0): the template's `todo("…")` prints
`[TODO: …]{.bs-todo}` for a report detail nobody has written.  Highlighted in
every format so it cannot be missed, and always starting with "TODO:" so a
reviewer can search the document for it.  The text is the TEMPLATE's (never
database text), and it is escaped for each format anyway.
]]
local function xml_escape(s)
  return (s:gsub("&", "&amp;"):gsub("<", "&lt;"):gsub(">", "&gt;"):gsub('"', "&quot;"))
end

function Span(el)
  if not el.classes:includes("bs-todo") then return nil end
  local text = pandoc.utils.stringify(el.content)
  if FORMAT:match("docx") then
    return pandoc.RawInline("openxml",
      '<w:r><w:rPr><w:b/><w:highlight w:val="yellow"/></w:rPr><w:t xml:space="preserve">'
      .. xml_escape(text) .. "</w:t></w:r>")
  elseif FORMAT:match("html") then
    return pandoc.RawInline("html", '<mark class="bs-todo"><strong>' .. xml_escape(text) .. "</strong></mark>")
  end
  return pandoc.Strong(el.content)
end

--[[
Verbatim text (review 2026-10-01 B8): the template's `code(c, "command")`
emits

    ::: {.bs-code key="findings.3.confirmations.0.command"}
    :::

for a command line as it was run or a tool's output.  The value becomes ONE
code block built from the string — it is never read as Markdown, so a fence,
a backtick, a shortcode or raw HTML inside it is text.  Control characters
are dropped (one of them makes a Word file unreadable) and the length is
capped here too, whatever the data holds.
]]
local CODE_MAX = 6000

local function verbatim(el)
  local key = el.attributes["key"]
  if key == nil or not key:match("^[%a_][%w_%.]*$") then return {} end
  local text = resolve(key)
  if text == nil or text:match("^%s*$") then return {} end
  text = text:gsub("\r\n?", "\n")
  text = text:gsub("[\0-\8\11\12\14-\31\127]", "")
  -- pandoc.text counts characters, so a cut never splits a UTF-8 sequence.
  if pandoc.text.len(text) > CODE_MAX then text = pandoc.text.sub(text, 1, CODE_MAX) end
  return pandoc.CodeBlock(text)
end

-- The template's `image(e)`: one evidence image as a figure.
local function evidence_figure(el)
  local file = evidence_file(el.attributes["file"])
  if file == nil then return {} end
  return figure(file, caption_of(file), valid_width(el.attributes["width"]))
end

local function fill_field(el)
  if el.classes:includes("bs-code") then return verbatim(el) end
  if el.classes:includes("bs-figure") then return evidence_figure(el) end
  if not el.classes:includes("bs-md") then return nil end
  local key = el.attributes["key"]
  if key == nil or not key:match("^[%a_][%w_%.]*$") then return {} end
  local text = resolve(key)
  if text == nil or text:match("^%s*$") then return {} end
  -- End the text with a newline: without one, pandoc.read's CommonMark
  -- readers turn a table's LAST row into a paragraph ("| March | $420 |"),
  -- and a form field's text usually has no trailing newline (v2.407.0).
  -- Windows line endings are normalised too.
  text = text:gsub("\r\n?", "\n") .. "\n"
  local doc = pandoc.read(text, READER)
  local images = field_images(key)
  local drop = el.attributes["images"] == "none"
  local width = valid_width(el.attributes["image-width"])
  local used = {}
  local blocks = lift(clean(doc.blocks, images, drop), images, width, used, 0)
  if not drop then
    -- The dataset says this field places an image the text did not yield
    -- (the reference sits in a code block, say): it is no longer in the
    -- trailing evidence block, so it prints here rather than nowhere.
    local missed = {}
    for id, _ in pairs(images) do
      if not used[id] then missed[#missed + 1] = id end
    end
    table.sort(missed, function(a, b) return tonumber(a) < tonumber(b) end)
    for _, id in ipairs(missed) do
      blocks:insert(figure(images[id].file, images[id].caption, width))
    end
  end
  return blocks
end

--[[
One pass over the finished document.  The placeholders are filled first, then
every BlueStick figure is numbered in the order a reader meets it: "Figure 3:
caption", or "Figure 3" for an image nobody captioned.  (Tables are numbered
by the template — a Jinja counter — because Jinja writes every one of them.)
]]
function Pandoc(doc)
  doc = doc:walk({ Div = fill_field })
  local n = 0
  return doc:walk({
    traverse = "topdown",
    Figure = function(el)
      if not el.classes:includes("bs-figure") then return nil end
      n = n + 1
      local caption = pandoc.Inlines({})
      if el.caption ~= nil and el.caption.long ~= nil and el.caption.long[1] ~= nil then
        caption = el.caption.long[1].content
      end
      local label = pandoc.Inlines({ pandoc.Str("Figure"), pandoc.Space(), pandoc.Str(tostring(n)) })
      if #caption > 0 then
        label:insert(pandoc.Str(":"))
        label:insert(pandoc.Space())
        label:extend(caption)
      end
      el.caption = { long = { pandoc.Plain(label) } }
      return el, false
    end,
  })
end
