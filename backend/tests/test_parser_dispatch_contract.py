"""Detection ↔ dispatch contract for the upload pipeline.

Two lists must agree for an upload to parse: ``_build_parsing_attempts``
decides *which* parser handles a file, and ``build_parser_dispatch_map``
decides *how* that parser class is constructed.  When a parser is added to the
first but not the second, detection picks it and dispatch rejects it with
"Unsupported parser class" — the file fails to parse with a confusing message.

That is exactly how RDAP (`scripts/rdap-lookup.py` NDJSON) and testssl.sh
broke: both were wired into detection but never registered for dispatch, so
every such upload failed.  The per-parser unit tests didn't catch it because
they call ``parser.parse_file`` directly, bypassing the dispatch layer.

This test drives the *real* detection on representative inputs and asserts
every parser class it can emit is dispatchable.
"""
import json
import os
from types import SimpleNamespace

from app.parsers.rdap_parser import RdapParser
from app.parsers.testssl_parser import TestsslParser
from app.services.ingestion_service import (
    IngestionService,
    build_parser_dispatch_map,
)
from app.services.nessus_integration_service import NessusIntegrationService

RDAP_FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures", "rdap-live.ndjson")

# (filename, sample-bytes) inputs that each trigger a distinct JSON-branch
# detector — the branch where this class of drift happens (every parser there
# needs its own dispatch registration).
TESTSSL_SAMPLE = json.dumps(
    [{"id": "SSLv3", "ip": "web/1.2.3.4", "port": "443", "severity": "OK", "finding": "not offered"}]
).encode()
HTTPX_SAMPLE = b'{"url":"https://x/","tech":["Nginx"],"webserver":"nginx","status_code":200}\n'
DNSX_SAMPLE = b'{"host":"example.com","a":["1.2.3.4"],"resolver":["8.8.8.8:53"]}\n'


def _detected_classes(filename: str, sample: bytes):
    """Parser classes the real detector emits for (filename, sample)."""
    svc = IngestionService()
    job = SimpleNamespace(original_filename=filename)
    return [cls for _file_type, cls, _desc in svc._build_parsing_attempts(job, sample)]


def test_rdap_and_testssl_are_dispatchable():
    """Pins the exact regression: both were detected but not dispatchable."""
    dispatch = build_parser_dispatch_map()
    assert RdapParser in dispatch
    assert TestsslParser in dispatch


def test_every_detected_parser_can_be_dispatched():
    """The general guard — any parser detection can pick must be constructible
    by dispatch.  ``NessusIntegrationService`` is the one detected class the
    executor dispatches on its own path, so it's allowed outside the map."""
    dispatch = build_parser_dispatch_map()
    allowed = set(dispatch) | {NessusIntegrationService}

    with open(RDAP_FIXTURE, "rb") as fh:
        rdap_bytes = fh.read()
    cases = [
        ("rdap.ndjson", rdap_bytes),
        ("scan.json", TESTSSL_SAMPLE),
        ("probe.json", HTTPX_SAMPLE),
        ("resolve.json", DNSX_SAMPLE),
    ]

    for filename, sample in cases:
        detected = _detected_classes(filename, sample)
        assert detected, f"detection found no parser for {filename}"
        for cls in detected:
            assert cls in allowed, (
                f"{cls.__name__} is detected for {filename} but not dispatchable — "
                "add it to build_parser_dispatch_map()"
            )


def test_rdap_ndjson_actually_dispatches_to_the_rdap_parser():
    """End-to-end wiring for the reported file: rdap.ndjson → RdapParser, and
    RdapParser is dispatchable (i.e. no 'Unsupported parser class')."""
    detected = _detected_classes("rdap.ndjson", open(RDAP_FIXTURE, "rb").read())
    assert RdapParser in detected
    assert build_parser_dispatch_map().get(RdapParser) is RdapParser
