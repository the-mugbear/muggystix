-- spacers.lua
-- Inserts OpenXML spacing paragraphs for DOCX output only.
-- Usage in qmd: <!-- spacer --> or <!-- spacer:120 -->
-- Ignored in HTML, PDF, and other formats.

local spacer_default = "60"

function RawBlock(el)
  if el.format ~= "html" then return nil end

  local attr = el.text:match("^<!%-%-%s*(spacer:?%d*)%s*%-%->$")
  if not attr then return nil end

  -- Only emit for docx output
  if not quarto.doc.isFormat("docx") then return nil end

  local after_val = attr:match("^spacer:(%d+)$") or spacer_default
  local xml = string.format(
    '<w:p><w:pPr><w:spacing w:after="%s" />'
    .. '<w:rPr><w:sz w:val="2" /></w:rPr>'
    .. '</w:pPr></w:p>',
    after_val
  )

  return pandoc.RawBlock("openxml", xml)
end

