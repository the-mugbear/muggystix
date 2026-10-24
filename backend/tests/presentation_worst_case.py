"""A worst-case issued report for presentation mode: what the slide builder
must page and what the browser must draw without overflow (an oversized
paragraph, a 300-line code block, a 16-column table, a 400-character URL,
CJK text, a long title, a long scope, a finding on 500 endpoints with
hostnames that wrap, printed and unprinted images, the longest footer).

Shared by `test_presentation_e2e_fixture.py` (which writes the Playwright
fixture `frontend/e2e/fixtures/presentation-worst-case.json` from it) and
the acceptance seed's worst-case report.
"""
from __future__ import annotations

SENTENCE = ("The service accepts weak ciphers and **legacy protocols**, which lets an attacker "
            "downgrade the session.")
LONG_PARAGRAPH = " ".join([SENTENCE] * 40)
CODE = "```\n" + "\n".join(f"line {i}: " + "x" * (i % 150) for i in range(300)) + "\n```"
WIDE_TABLE = ("| " + " | ".join(f"Col{i}" for i in range(16)) + " |\n|" + "---|" * 16 + "\n"
              + "\n".join("| " + " | ".join(f"value {r}-{c} longer text here" for c in range(16)) + " |"
                          for r in range(30)))
URL = "https://example.com/" + "a" * 400
CJK = "中文测试文本" * 60
LONG_FILTER = "no-such-endpoint-" * 11          # 187 characters: the longest footer

FROZEN_IMAGES = {11}                             # 13 is printed but its copy is gone


def dataset() -> dict:
    return {
        "report": {"title": "Internal penetration test of the Acme corporate network " * 3,
                   "heading": "Acme Corporation", "date": "2026-10-20", "number": 3},
        "counts": {"critical": 1, "high": 2, "medium": 1, "low": 0, "info": 0, "total": 4},
        "executive_summary": LONG_PARAGRAPH,
        "scope": {"subnets": [
            {"cidr": f"10.{i // 250}.{i % 250}.0/24", "site": "DMZ" if i % 3 == 0 else "HQ",
             "description": ("Servers and printers " * (i % 9)).strip() or None}
            for i in range(120)
        ], "domains": [{"domain": "acme.example", "include_subdomains": True}]},
        "findings": [
            {"ref": "F-01", "title": "Weak TLS configuration on externally reachable services " * 4,
             "severity": "critical", "cvss_score": 9.1, "status_note": None,
             "description": LONG_PARAGRAPH + "\n\n![The scan](evidence:11)\n\n" + CODE,
             "impact": WIDE_TABLE, "recommendation": f"See {URL} for details. {CJK}",
             "steps_to_reproduce": "1. Connect.\n2. Offer TLS 1.0.\n\n> The server accepts it.",
             "images": [{"attachment_id": 11, "caption": "The scan " * 30, "printed": True},
                        {"attachment_id": 12, "caption": "Not printed", "printed": False},
                        {"attachment_id": 13, "caption": "Gone", "printed": True}],
             "affected": [{"address": f"10.0.{i // 250}.{i % 250}",
                           "hostname": ("very-long-hostname-" * 6) if (i * i) % 11 == 0 else f"h{i}.acme.example",
                           "name": None, "port": "443/tcp", "state": None} for i in range(500)],
             "affected_count": 500},
            {"ref": "F-02", "title": "Default credentials", "severity": "high", "cvss_score": None,
             "status_note": None, "description": "admin/admin", "impact": None, "recommendation": None,
             "steps_to_reproduce": None, "images": [],
             "affected": [{"address": "10.0.1.5", "hostname": None, "name": None, "port": None, "state": None}],
             "affected_count": 1},
        ],
    }
