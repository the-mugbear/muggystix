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

local function resolve(key)
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
  if type(node) == "string" then return node end
  return nil
end

local WEB = { http = true, https = true, mailto = true }

local function clean(blocks)
  return blocks:walk({
    RawBlock = function(el) return pandoc.CodeBlock(el.text) end,
    RawInline = function(el) return pandoc.Code(el.text) end,
    Image = function(el) return el.caption end,
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
    CodeBlock = function(el)
      local lang = el.classes[1]
      if lang ~= nil and lang:match("^[%w_+-]+$") then
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
      local text = resolve(p)
      if text ~= nil and not text:match("^%s*$") then
        meta[key] = pandoc.Inlines(text)
      else
        meta[key] = nil
      end
    end
  end
  meta["bluestick-meta"] = nil
  return meta
end

function Div(el)
  if not el.classes:includes("bs-md") then return nil end
  local key = el.attributes["key"]
  if key == nil or not key:match("^[%a_][%w_%.]*$") then return {} end
  local text = resolve(key)
  if text == nil or text:match("^%s*$") then return {} end
  local doc = pandoc.read(text, "gfm-raw_html")
  return clean(doc.blocks)
end
