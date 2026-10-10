"""the agent call log forgets test-plan entries; the tool registry carries run commands

Two unrelated small changes of one batch (owner decisions of 2026-10-10), in one
revision so the batch has one migration.

1. ``agent_api_calls.referenced_entry_ids`` is dropped.  It held the test-plan
   entry ids a call named, and test plans were removed in v2.442.0: nothing has
   written a value since, and no route names an entry.  **The stored values are
   discarded** (ids of rows that no longer exist).  The downgrade re-adds the
   column as it was — JSON, nullable — and empty.

2. ``tool_registry`` gains ``run_command`` and ``run_note``: for a tool whose
   output BlueStick parses, the invocation that writes a file it can ingest and
   one line about what to upload.  Until now that was a table typed into the
   Tool reference page (``RUN_COMMANDS``) beside the registry the server owns.
   Seeding is additive and never overwrites, so an existing deployment's rows
   are filled here; a fresh one gets them from the seed file.  The upgrade only
   fills a row whose ``run_command`` is NULL — on a new column that is every
   row, and it keeps the statement safe to read.  The downgrade drops both
   columns (the commands live in this file and in the seed).

Revision ID: f5c2a0d7b4e6
Revises: e4b1f9c6a3d5
Create Date: 2026-10-10
"""
import sqlalchemy as sa
from alembic import op

revision = "f5c2a0d7b4e6"
down_revision = "e4b1f9c6a3d5"
branch_labels = None
depends_on = None


# name -> (run_command, run_note): what the Tool reference page showed up to
# frontend 5.364.0, frozen here.  `<target>` and the list files are
# placeholders the operator fills.
_RUN_COMMANDS = {
    "nmap": (
        "nmap -sV -sC -O -oX scan.xml <target>",
        "Upload scan.xml (-oX). Use -oG for a .gnmap instead.",
    ),
    "masscan": (
        "sudo masscan -p1-65535 --rate=1000 -oX masscan.xml <target>",
        "Upload the XML (-oX), JSON (-oJ), or list (-oL).",
    ),
    "rustscan": (
        "rustscan -a <target> -- -sV -oX scan.xml",
        "Pipes into nmap — upload the resulting nmap scan.xml.",
    ),
    "naabu": (
        "naabu -host <target> -json -o naabu.json",
        'Upload naabu.json (-json). Include "naabu" in the filename.',
    ),
    "httpx": (
        "httpx -l targets.txt -sc -title -tech-detect -favicon -json -o httpx.jsonl",
        "Upload httpx.jsonl. Call ProjectDiscovery's binary by path if the Python httpx CLI shadows it.",
    ),
    "whatweb": (
        "whatweb -a 3 --input-file=targets.txt --log-json=whatweb.json --no-errors",
        "Upload whatweb.json (--log-json).",
    ),
    "eyewitness": (
        "eyewitness --web -f urls.txt -d eyewitness_report --no-prompt",
        'Upload the JSON/CSV report (filename must contain "eyewitness" or "report").',
    ),
    "nikto": (
        "nikto -h <target> -Format json -o nikto.json",
        "Upload nikto.json (-Format json).",
    ),
    "nuclei": (
        "nuclei -l targets.txt -je nuclei.json",
        "Upload nuclei.json (-je writes the JSON export).",
    ),
    "smbmap": (
        "smbmap -H <target> | tee smbmap.txt",
        'Upload smbmap.txt — keep the "[+] <ip>" host lines.',
    ),
    "netexec": (
        "netexec smb <target> -u '' -p '' --shares",
        "Upload the --json output or the standard text report.",
    ),
    "bloodhound-python": (
        "bloodhound-python -d <domain> -u <user> -p <pass> -c All -ns <dc-ip>",
        "Upload the extracted JSON files, not the ZIP bundle.",
    ),
    "amass": (
        "amass enum -d <domain> -json amass.json",
        "Upload amass.json — best results include resolved IPs.",
    ),
    "subfinder": (
        "subfinder -d <domain> -oJ -o subfinder.json",
        "Upload subfinder.json (-oJ) with resolved IPs.",
    ),
    "dnsx": (
        "dnsx -j -resp -l ips.txt -r resolvers.txt -ptr -a -aaaa -cname -mx -ns -txt -o dnsx-output.json",
        "Upload dnsx-output.json (-j). PTR answers feed hostnames.",
    ),
    "gobuster": (
        "gobuster dir -u http://<target> -w wordlist.txt -o gobuster.txt",
        "Upload gobuster.txt (.json/.csv/.txt all parse).",
    ),
    "feroxbuster": (
        "feroxbuster -u http://<target> --json -o feroxbuster.json",
        "Upload feroxbuster.json (--json).",
    ),
    "ffuf": (
        "ffuf -u http://<target>/FUZZ -w wordlist.txt -of json -o ffuf.json",
        "Upload ffuf.json (-of json).",
    ),
    "dirsearch": (
        "dirsearch -u http://<target> --format json -o dirsearch.json",
        "Upload dirsearch.json (--format json).",
    ),
    "dirb": (
        "dirb http://<target> wordlist.txt -o dirb.txt",
        "Upload dirb.txt.",
    ),
    "wfuzz": (
        "wfuzz -w wordlist.txt -f wfuzz.json,json http://<target>/FUZZ",
        "Upload wfuzz.json (-f … ,json).",
    ),
}


def upgrade():
    op.drop_column("agent_api_calls", "referenced_entry_ids")

    op.add_column("tool_registry", sa.Column("run_command", sa.Text(), nullable=True))
    op.add_column("tool_registry", sa.Column("run_note", sa.Text(), nullable=True))
    fill = sa.text(
        "UPDATE tool_registry SET run_command = :run, run_note = :note "
        "WHERE name = :name AND run_command IS NULL"
    )
    conn = op.get_bind()
    for name, (run, note) in _RUN_COMMANDS.items():
        conn.execute(fill, {"name": name, "run": run, "note": note})


def downgrade():
    op.drop_column("tool_registry", "run_note")
    op.drop_column("tool_registry", "run_command")

    op.add_column(
        "agent_api_calls",
        sa.Column("referenced_entry_ids", sa.JSON(), nullable=True),
    )
