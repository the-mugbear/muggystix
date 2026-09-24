# Render this template with sample data, the way BlueStick renders it.
#
#   make                — HTML, DOCX and the QMD source of sample-data.json into _output/
#   make html | docx
#   make DATA=my.json   — another dataset (e.g. one saved from a real report)
#   make clean
#
# Needs Quarto and Python with jinja2 on this machine.  Without them, render
# inside the report-worker image from the repository root:
#
#   docker compose run --rm --no-deps --entrypoint "" \
#     -v "$PWD/report-templates:/tpl" report-worker \
#     python /app/app/services/quarto_render.py /tpl/remediation-worklist \
#       --data /tpl/remediation-worklist/sample-data.json --out /tpl/remediation-worklist/_output

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
