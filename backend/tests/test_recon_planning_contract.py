"""What the recon surface promises an agent must match what it does.

v2.314.0.  An agent ran the recon workflow end to end (10 tools, one full
session against a /24) and found four issues. Like the assist evaluation before
it, none were reachable by testing one function: each is a disagreement between
two places that describe the same thing — an MCP schema vs the planner that
reads it, a tool catalog vs the fallback table, a service observation vs the URL
derived from it.
"""

import pytest


# ---------------------------------------------------------------------------
# 1. The tools_status wire shape is a contract with the planner.
# ---------------------------------------------------------------------------

def test_the_mcp_probe_schema_matches_what_the_planner_reads():
    """v2.313.0 shipped an invented `tools_status` item shape — `detail`
    instead of `issue`, and free-form statuses like `wrong-binary`.

    It type-checked and planned WRONGLY: `_env_tool_unavailable` treats only
    `warn` and `missing` as a problem, so a tool an agent honestly reported as
    `wrong-binary` fell through the test and was planned around as working. The
    schema is what agents obey, so it has to name the exact vocabulary the
    planner branches on.
    """
    from app.api.v1.endpoints.mcp_tools import _PROBE_PROPERTIES

    item = _PROBE_PROPERTIES["tools_status"]["items"]
    props = item["properties"]

    assert "issue" in props, (
        "the planner reads `issue` for the swap reason; a schema that "
        "advertises a different field name yields empty swap_reasons"
    )
    assert "detail" not in props, "`detail` is not the field the planner reads"
    assert set(props["status"]["enum"]) == {"ok", "warn", "missing", "info"}


@pytest.mark.parametrize(
    "status, expected_unavailable",
    [("ok", False), ("info", False), ("warn", True), ("missing", True)],
)
def test_every_advertised_status_decides_the_planner(status, expected_unavailable):
    """Each value the schema offers must actually mean something downstream.

    This is the half that would have caught the bad schema: `wrong-binary` was
    advertised, and `_env_tool_unavailable` silently returned None for it.
    """
    from app.api.v1.endpoints.mcp_tools import _PROBE_PROPERTIES
    from app.services.recon_planning_service import _env_tool_unavailable

    assert status in _PROBE_PROPERTIES["tools_status"]["items"]["properties"]["status"]["enum"]
    env = {"tools_status": [{"name": "httpx", "status": status, "issue": "why"}]}
    assert bool(_env_tool_unavailable(env, "httpx")) is expected_unavailable


def test_the_issue_string_reaches_the_agent_verbatim():
    """`issue` is surfaced as the step's swap_reason — an agent reads it to tell
    the operator which steps deviated and why."""
    from app.services.recon_planning_service import _env_tool_unavailable

    env = {"tools_status": [
        {"name": "httpx", "status": "warn",
         "issue": "Python httpx CLI shadows ProjectDiscovery httpx"},
    ]}
    reason = _env_tool_unavailable(env, "httpx")
    assert "Python httpx CLI shadows ProjectDiscovery httpx" in reason


# ---------------------------------------------------------------------------
# 2. The catalog and the fallback table must name the same tools.
# ---------------------------------------------------------------------------

def test_the_always_available_web_fallback_is_offered_when_the_step_blocks():
    """The catalog calls nmap the "always-available" httpx fallback; the blocked
    step offered only curl and whatweb, so an agent with httpx and eyewitness
    both unusable was steered away from the option the catalog told it to
    expect — and toward two tools BlueStick ingests less well."""
    from app.services.recon_planning_service import _SWAP_RULES

    rule = next(r for r in _SWAP_RULES if r.name == "httpx_to_eyewitness")
    offered = [f["tool"] for f in rule.acceptable_fallbacks_when_blocked or []]
    assert "nmap" in offered, (
        f"httpx's blocked step offers {offered}; the catalog markets nmap as "
        "the always-available fallback for exactly this case"
    )
    assert offered[0] == "nmap", "the always-available option should lead"


# ---------------------------------------------------------------------------
# 3. A step whose tool is known-missing must not read as runnable.
# ---------------------------------------------------------------------------

def _sequence(env):
    from app.services.recon_planning_service import build_recommended_sequence
    return build_recommended_sequence(
        subnet_cidrs=["192.168.7.0/24"],
        scope_size={"total_addresses": 256},
        known_hosts_with_ports=0,
        environment=env,
    )


def test_the_optional_screenshot_step_blocks_when_eyewitness_is_missing():
    """The swap rules only cover steps that HAVE a fallback. Everything else was
    emitted verbatim, so the optional step-4 screenshot pass kept advertising an
    eyewitness command on a host whose own preflight said eyewitness was
    missing. The agent had no way to know until it ran it."""
    env = {"tools_status": [
        {"name": "eyewitness", "status": "missing", "issue": "not on PATH"},
    ]}
    steps = _sequence(env)
    shots = [s for s in steps if s.get("phase") == "web_screenshot"]
    assert shots, "expected the optional screenshot step in this sequence"
    step = shots[0]
    assert step.get("blocked_reason") == "tool_unavailable", (
        f"screenshot step still reads as runnable: {step.get('command')!r}"
    )
    assert step.get("command") is None
    assert step.get("unavailable_tool") == "eyewitness"


def test_a_runnable_sequence_is_left_alone():
    """The blocking sweep must not fire on a healthy environment — every step
    keeps its command."""
    env = {"tools_status": [
        {"name": "nmap", "status": "ok"},
        {"name": "httpx", "status": "ok"},
        {"name": "eyewitness", "status": "ok"},
    ]}
    for step in _sequence(env):
        assert step.get("blocked_reason") is None
        assert step.get("command")


def test_every_step_names_its_tool():
    """The sweep can only ask "can this run here?" of a step that says which
    tool it needs. A step without one is invisible to it — which is exactly how
    the screenshot step slipped through."""
    for step in _sequence(None):
        if step.get("phase") == "optional":
            continue  # a prose "iterate based on findings" placeholder
        assert step.get("tool"), f"step {step.get('step')} names no tool"


# ---------------------------------------------------------------------------
# 4. A TLS-wrapped service must not be advertised as plaintext.
# ---------------------------------------------------------------------------

def _brief(port, service, tunnel=None):
    from app.api.v1.endpoints.agent_schemas import ReconHostBrief, ReconPortBrief
    return ReconHostBrief(
        host_id=1, ip_address="192.168.7.245", hostname=None,
        open_ports=[ReconPortBrief(port=port, service=service, tunnel=tunnel)],
    )


def test_an_ssl_http_service_derives_an_https_url():
    """The reported bug. nmap identified 192.168.7.245:3000 as `ssl/http`
    twice, and the derived web target came back `http://…:3000/`.

    nmap's XML reports that service as name="http" tunnel="ssl" — the `ssl/`
    prefix exists only in its text output — and the parser dropped `tunnel`, so
    the stored service was the bare string "http". Nothing downstream could
    tell TLS from plaintext.
    """
    from app.services.recon_summary_service import web_targets_from_hosts

    targets = web_targets_from_hosts([_brief(3000, "http", tunnel="ssl")])
    assert len(targets) == 1
    assert targets[0].protocol == "https"
    assert targets[0].url == "https://192.168.7.245:3000/"


def test_observed_service_beats_the_port_number_guess():
    """Ordering, which was backwards. A port in the well-known table took its
    scheme from the table and never consulted the service — so an HTTPS service
    on 8080 was called http, with -sV output sitting right there saying
    otherwise."""
    from app.services.recon_summary_service import web_targets_from_hosts

    tls_on_8080 = web_targets_from_hosts([_brief(8080, "http", tunnel="ssl")])
    assert tls_on_8080[0].protocol == "https"

    # And the reverse: plaintext on 443 is reported as what it is.
    plain_on_443 = web_targets_from_hosts([_brief(443, "http")])
    assert plain_on_443[0].protocol == "http"


def test_the_port_table_still_covers_unprobed_ports():
    """The table is the fallback for ports nothing service-probed — a discovery
    sweep with no -sV still yields usable targets."""
    from app.services.recon_summary_service import web_targets_from_hosts

    targets = web_targets_from_hosts([_brief(443, None)])
    assert targets[0].protocol == "https"
    assert targets[0].url == "https://192.168.7.245/"


def test_the_parser_keeps_the_tunnel_attribute(db_session):
    """Root cause: without this the column is always NULL and every fix above
    degrades back to guessing from the port number."""
    from lxml import etree
    from app.parsers.nmap_parser import NmapXMLParser

    port_xml = etree.fromstring(
        b'<port protocol="tcp" portid="3000">'
        b'<state state="open" reason="syn-ack"/>'
        b'<service name="http" product="nginx" tunnel="ssl" method="probed" conf="10"/>'
        b'</port>'
    )
    data = NmapXMLParser(db_session)._extract_port_data(port_xml)
    assert data["service_tunnel"] == "ssl"
    assert data["service_name"] == "http"
