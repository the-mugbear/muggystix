"""Unit tests for nmap SMB message-signing extraction (no DB).

Pins the classification of smb-security-mode / smb2-security-mode host-script
output into the queryable ``Host.smb_signing`` posture used by the systemic
"SMB signing disabled estate-wide" condition.
"""
import pytest
from lxml import etree

from app.parsers.nmap_parser import NmapXMLParser


def _hostscript(script_id: str, output: str) -> etree._Element:
    el = etree.Element("hostscript")
    s = etree.SubElement(el, "script")
    s.set("id", script_id)
    s.set("output", output)
    return el


@pytest.mark.parametrize("script_id,output,expected", [
    # smb-security-mode classic output
    ("smb-security-mode", "\n  message_signing: disabled (dangerous, but default)", "disabled"),
    ("smb-security-mode", "\n  message_signing: required", "required"),
    ("smb-security-mode", "\n  message_signing: supported", "enabled"),
    # smb2-security-mode phrasing
    ("smb2-security-mode", "Message signing enabled but not required", "enabled"),
    ("smb2-security-mode", "Message signing enabled and required", "required"),
    # unrelated script → no signal
    ("smb-os-discovery", "OS: Windows 10", None),
])
def test_detect_smb_signing(script_id, output, expected):
    el = _hostscript(script_id, output)
    assert NmapXMLParser._detect_smb_signing(el) == expected


def test_detect_smb_signing_none_when_no_hostscript():
    assert NmapXMLParser._detect_smb_signing(None) is None
