"""The "What BlueStick reads" data may never claim more than the parsers do
(v2.411.0).  See app/services/parser_coverage.py for what each check guards."""
from __future__ import annotations

import importlib
import inspect
import json
from pathlib import Path

import pytest

from app.db.session import Base
from app.services.format_registry import FORMATS
from app.services.parser_coverage import LEVEL_IDS, coverage_payload, load_coverage
from app.services.tool_output_contract import TOOL_OUTPUT_CONTRACT

SEED = Path(__file__).resolve().parent.parent / "app" / "data" / "tool_registry_seed.json"
# A vulnerability write, however the parser reaches it.
_OBSERVATION_WRITERS = ("upsert_vulnerability", "VulnerabilityService", "Vulnerability(", "record_misconfig")


def _model_attributes():
    import app.db.model_registry  # noqa: F401 — every model module registered

    attrs = {}
    for mapper in Base.registry.mappers:
        attrs[mapper.class_.__name__] = set(mapper.attrs.keys())
    return attrs


def test_every_format_belongs_to_exactly_one_tool():
    owners = {}
    for tool in load_coverage():
        for file_type in tool.formats:
            assert file_type not in owners, f"{file_type} is listed under {owners[file_type]} and {tool.id}"
            owners[file_type] = tool.id
    missing = sorted(set(FORMATS) - set(owners))
    unknown = sorted(set(owners) - set(FORMATS))
    assert not missing, f"formats with no coverage entry (document the new parser): {missing}"
    assert not unknown, f"coverage names formats the registry does not know: {unknown}"


def test_tool_ids_are_unique_and_every_tool_says_something():
    ids = [tool.id for tool in load_coverage()]
    assert len(ids) == len(set(ids))
    for tool in load_coverage():
        assert tool.signals, f"{tool.id} lists nothing it reads"
        assert tool.accepted_input.strip()


def test_every_stored_as_is_a_real_model_attribute():
    attrs = _model_attributes()
    bad = []
    for tool in load_coverage():
        for signal in tool.signals:
            for ref in signal.stored_as:
                model, _, attr = ref.partition(".")
                if attr not in attrs.get(model, set()):
                    bad.append(f"{tool.id}: {signal.what!r} → {ref}")
    assert not bad, "stored_as names no mapped attribute:\n" + "\n".join(bad)


def test_levels_are_consistent():
    problems = []
    for tool in load_coverage():
        for signal in tool.signals:
            label = f"{tool.id}: {signal.what!r}"
            assert signal.level in LEVEL_IDS
            if signal.level == "discarded" and signal.stored_as:
                problems.append(f"{label} is discarded but names storage")
            if signal.level != "discarded" and not signal.stored_as:
                problems.append(f"{label} is {signal.level} but names no storage")
            if signal.level == "observation" and not any(r.startswith("Vulnerability.") for r in signal.stored_as):
                problems.append(f"{label} is an observation but not stored on Vulnerability")
            if signal.level in ("text", "stored") and any(r.startswith("Vulnerability.") for r in signal.stored_as):
                problems.append(f"{label} lands on Vulnerability, so it is an observation")
            if signal.level in ("observation", "field", "text") and not (signal.shown or "").strip():
                problems.append(f"{label} is {signal.level} but says nowhere it is shown")
    assert not problems, "\n".join(problems)


def _parser_source(tool) -> str:
    """The parser modules behind a tool's formats (and the parser a service
    wrapper delegates to)."""
    sources = []
    for file_type in tool.formats:
        module = importlib.import_module(FORMATS[file_type].module)
        sources.append(inspect.getsource(module))
    return "\n".join(sources)


def test_observation_claims_match_what_the_parser_writes():
    """A tool that claims scanner observations has a parser that writes them,
    and a parser that writes them has them documented."""
    for tool in load_coverage():
        claims = any(s.level == "observation" for s in tool.signals)
        source = _parser_source(tool)
        writes = any(marker in source for marker in _OBSERVATION_WRITERS)
        assert claims == writes, (
            f"{tool.id}: coverage {'claims' if claims else 'does not claim'} observations but the parser "
            f"{'writes' if writes else 'does not write'} them"
        )


def test_every_ingestible_tool_maps_to_a_coverage_entry():
    """The output contract and the registry seed call these tools ingestible;
    each must lead to a documented parser (nuclei did not, v2.411.0)."""
    mapped = {name for tool in load_coverage() for name in tool.registry_tools}
    seed = json.loads(SEED.read_text())
    ingestible = {row["name"] for row in seed if row.get("ingestible")}
    missing = sorted((set(TOOL_OUTPUT_CONTRACT) | ingestible) - mapped)
    assert not missing, f"called ingestible but not mapped to a coverage entry: {missing}"
    unknown = sorted(mapped - {row["name"] for row in seed} - set(TOOL_OUTPUT_CONTRACT))
    assert not unknown, f"registry_tools names no known tool: {unknown}"


def test_payload_labels_formats_and_defines_levels():
    payload = coverage_payload()
    assert [level["id"] for level in payload["levels"]] == list(LEVEL_IDS)
    nmap = next(t for t in payload["tools"] if "nmap_xml" in [f["file_type"] for f in t["formats"]])
    assert {"file_type": "nmap_xml", "label": "Nmap XML"} in nmap["formats"]


@pytest.mark.parametrize("tool_id", ["netexec", "nmap"])
def test_known_gaps_stay_documented_until_fixed(tool_id):
    """The gaps that prompted this page (VNC no-auth from nxc; NSE output kept
    as text) are stated, not implied."""
    tool = next(t for t in load_coverage() if t.id == tool_id)
    assert tool.gaps


def test_endpoint_serves_the_page_data(client):
    response = client.get("/api/v1/references/parser-coverage")
    assert response.status_code == 200
    body = response.json()
    assert body["tools"] and body["levels"]
