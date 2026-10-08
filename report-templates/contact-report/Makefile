# Render this template with sample data, the way BlueStick renders it.
#
#   make                — HTML, DOCX and the QMD source of sample-data.json into _output/
#   make html | docx    (a PDF: export the DOCX from Word)
#   make DATA=my.json   — another dataset (e.g. one saved from a real report)
#   make clean
#
# Needs Quarto (1.10) and Python with jinja2 on this machine.  Without them,
# render inside the report-worker image from the repository root:
#
#   docker compose run --rm --no-deps --entrypoint "" \
#     -v "$PWD/report-templates:/tpl" report-worker \
#     python /app/app/services/quarto_render.py /tpl/contact-report \
#       --data /tpl/contact-report/sample-data.json --out /tpl/contact-report/_output
#
# This template EXTENDS ../pentest (template.json → "extends"): every file it
# does not have itself — the Word styles, the post-processor, the filters, the
# shared partials — is pentest's, so render it from inside report-templates/
# with pentest beside it.  The sample's evidence images are pentest's
# sample-evidence/ (1.png for evidence/1.png in the data); for another
# dataset's images add `--evidence DIR` to RENDER.
#
# The renderer, its escaping rules and the helpers the template uses (md,
# image, plain, asset) are described at the top of report.qmd and in
# backend/app/services/quarto_render.py.
#
# The template's own images (logo …) are declared in template.json under
# "assets"; put each file at its declared path (e.g. img/logo.png).  The
# render prints a note for each one that is not installed, and refuses when a
# REQUIRED one is missing.  Without the optional logo the layout is unchanged.

DATA    ?= sample-data.json
OUT     := _output
RENDER  := python3 ../../backend/app/services/quarto_render.py .

.PHONY: all html docx pdf clean
all:
	$(RENDER) --data $(DATA) --out $(OUT)

html docx pdf:
	$(RENDER) --data $(DATA) --out $(OUT) --to $@

clean:
	rm -rf $(OUT)
