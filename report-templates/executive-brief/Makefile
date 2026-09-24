# Render this template with sample data, the way BlueStick renders it.
#
#   make                — Word and HTML of sample-data.json into _output/
#   make html | docx    (a PDF: export the DOCX from Word)
#   make DATA=my.json   — another dataset (e.g. one saved from a real report)
#   make clean
#
# Needs Quarto and Python with jinja2 on this machine.  Without them, render
# inside the report-worker image from the repository root:
#
#   docker compose run --rm --no-deps --entrypoint "" \
#     -v "$PWD/report-templates:/tpl" report-worker \
#     python /app/app/services/quarto_render.py /tpl/executive-brief \
#       --data /tpl/executive-brief/sample-data.json --out /tpl/executive-brief/_output
#
# The logo, title page image and Word styles are declared in template.json
# under "assets" — the same as the Penetration test report's, installed
# separately for this template.

DATA    ?= sample-data.json
OUT     := _output
RENDER  := python3 ../../backend/app/services/quarto_render.py .

.PHONY: all html docx clean
all:
	$(RENDER) --data $(DATA) --out $(OUT)

html docx:
	$(RENDER) --data $(DATA) --out $(OUT) --to $@

clean:
	rm -rf $(OUT)
