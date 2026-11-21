"""the tool catalogue says, for every tool, whether it is intrusive, needs privileges, and what it writes

Decided 2026-11-21: the Tool Reference shows ``intrusive``,
``requires_privileges`` and ``output_format`` where the catalogue states them,
and the missing values are filled in from each tool's own documentation.
Fifty of the sixty-three shipped tools stated none; the thirteen that did
carried ``requires_privileges = true`` as a blanket default (httpx, Nikto and
testssl do not need root).

The values below were taken from each tool's man page, README or Kali page
(2026-11-21) by these definitions:

* ``intrusive`` — its ordinary use can disturb a target or change its state:
  exploit checks, password guessing, wordlist-driven discovery, fuzzing, a
  write to the target.  A client that only does what the operator types is
  not; a scanner that only sends probes is not.
* ``requires_privileges`` — its ordinary use needs root or raw-socket
  capability on the operator's machine.
* ``output_format`` — the structured file it can write for import; NULL for a
  tool that only prints to the terminal or is an interactive client.

Seeding is additive and never overwrites (``tool_registry_service.seed_registry``),
so the seed edit alone reaches only a new installation; this revision gives
existing rows the same values.  Like ``b3d9f1a7c5e2`` it touches only what is
still as shipped: a column is written where it is NULL, or where it still
holds the value the product shipped it with (``_SHIPPED``) — a value an
administrator changed is kept.

**Downgrade.**  Puts back what was shipped — NULL, or the earlier shipped
value — only where a column still holds what the upgrade wrote.

Revision ID: e3f9c1a5b7d4
Revises: d2e8b0f4a6c3
Create Date: 2026-11-21
"""
import sqlalchemy as sa
from alembic import op

revision = "e3f9c1a5b7d4"
down_revision = "d2e8b0f4a6c3"
branch_labels = None
depends_on = None

_COLUMNS = ("intrusive", "requires_privileges", "output_format")

# Frozen copy of the seed's values (app/data/tool_registry_seed.json): a
# revision never reads the application, which moves on after it.
# (tool, intrusive, requires_privileges, output_format)
_FLAGS = (
    ("amap", False, False, "txt"),
    ("amass", False, False, "json"),
    ("bloodhound-python", False, False, "json"),
    ("certipy-ad", True, False, "json"),
    ("crackmapexec", True, False, "txt"),
    ("curl", False, False, None),
    ("dig", False, False, None),
    ("dirb", True, False, "txt"),
    ("dirsearch", True, False, "json"),
    ("dnsenum", False, False, "xml"),
    ("dnsrecon", False, False, "json"),
    ("dnsx", False, False, "jsonl"),
    ("enum4linux", False, False, None),
    ("evil-winrm", False, False, None),
    ("eyewitness", False, False, "json"),
    ("feroxbuster", True, False, "jsonl"),
    ("ffuf", True, False, "json"),
    ("ftp", False, False, None),
    ("gobuster", True, False, "txt"),
    ("httpx", False, False, "jsonl"),
    ("hydra", True, False, "json"),
    ("impacket", True, False, None),
    ("impacket-mssqlclient", False, False, None),
    ("kerbrute", True, False, "txt"),
    ("ldapdomaindump", False, False, "json"),
    ("ldapsearch", False, False, None),
    ("masscan", False, True, "json"),
    ("mongosh", False, False, None),
    ("mysql", False, False, None),
    ("naabu", False, False, "jsonl"),
    ("nc", False, False, None),
    ("netexec", True, False, "txt"),
    ("nikto", True, False, "json"),
    ("nmap", False, True, "xml"),
    ("nuclei", True, False, "json"),
    ("onesixtyone", True, False, "txt"),
    ("openssl", False, False, None),
    ("psql", False, False, None),
    ("redis-cli", False, False, None),
    ("rpcclient", False, False, None),
    ("rustscan", True, False, "txt"),
    ("smbclient", False, False, None),
    ("smbget", False, False, None),
    ("smbmap", True, False, "txt"),
    ("snmp-check", False, False, None),
    ("snmpwalk", False, False, None),
    ("socat", False, False, None),
    ("sqlcmd", False, False, None),
    ("sqsh", False, False, None),
    ("ssh", False, False, None),
    ("ssh-audit", False, False, "json"),
    ("subfinder", False, False, "jsonl"),
    ("telnet", False, False, None),
    ("testssl", False, False, "json"),
    ("unicornscan", False, True, "txt"),
    ("vncviewer", False, False, None),
    ("wafw00f", False, False, "json"),
    ("wfuzz", True, False, "json"),
    ("wget", False, False, None),
    ("whatweb", False, False, "json"),
    ("xfreerdp", False, False, None),
    ("gvm-tools", True, False, "xml"),
    ("gvm-cli", True, False, "xml"),
)

# What the thirteen rows that stated something were shipped with.
_SHIPPED = {
    "eyewitness": (False, True, "json"),
    "httpx": (False, True, "json"),
    "masscan": (True, True, "json"),
    "netexec": (False, True, "json"),
    "nikto": (True, True, "json"),
    "nmap": (False, True, "xml"),
    "nuclei": (True, True, "json"),
    "rustscan": (True, True, "txt"),
    "subfinder": (False, True, "txt"),
    "testssl": (False, True, "json"),
    "whatweb": (False, True, "json"),
    "gvm-tools": (True, False, "xml"),
    "gvm-cli": (True, False, "xml"),
}


def _set(conn, name: str, column: str, value, where_value) -> None:
    """Write ``value`` into ``column`` of ``name`` where it holds ``where_value``
    (NULL included)."""
    if value == where_value:
        return
    held = f"{column} IS NULL" if where_value is None else f"{column} = :held"
    conn.execute(
        sa.text(f"UPDATE tool_registry SET {column} = :value WHERE name = :name AND {held}"),
        {"value": value, "name": name, **({} if where_value is None else {"held": where_value})},
    )


def upgrade() -> None:
    conn = op.get_bind()
    for name, *values in _FLAGS:
        shipped = _SHIPPED.get(name, (None, None, None))
        for column, value, was in zip(_COLUMNS, values, shipped):
            _set(conn, name, column, value, None)        # never stated
            if was is not None:
                _set(conn, name, column, value, was)     # still as shipped


def downgrade() -> None:
    conn = op.get_bind()
    for name, *values in _FLAGS:
        shipped = _SHIPPED.get(name, (None, None, None))
        for column, value, was in zip(_COLUMNS, values, shipped):
            if value is not None:
                _set(conn, name, column, was, value)
