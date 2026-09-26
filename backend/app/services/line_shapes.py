"""Redacted line shapes: what a parser did not interpret, safe to share (v2.418.0).

A parser that meets a line it cannot read either drops it or keeps it as the
tool's text.  Both were silent, and without real samples nobody could say which
lines those were.  An import now carries the SHAPES of those lines — the
structure with every value replaced by a placeholder — grouped and counted:

    LDAP <IP> 389 <HOST> [*] <OS> (name:<VALUE>) (domain:<VALUE>) (signing:None)
    MS17-010 <IP> 445 <HOST> [+] <HOST> is VULNERABLE to MS17-010

A shape is what a fixture needs and what a person outside the client network
may see: addresses, names, credentials, hashes and paths are replaced; the
tool's own words, flags, booleans and port numbers are kept.  It is still an
excerpt of client output, so it is scrubbed again before it leaves the
deployment (scripts/collect-logs.sh) — redaction here is best effort, not the
last line of defence.
"""
from __future__ import annotations

import re
from collections import Counter
from typing import Dict, List, Optional, Tuple

SHAPE_MAX_CHARS = 240
MAX_SHAPES = 50

# Values of a "(key:value)" flag that carry meaning, not data.
_SAFE_FLAG_VALUES = {
    "true", "false", "none", "null", "never", "always", "required", "enabled", "disabled",
    "when supported", "yes", "no", "n/a", "unknown",
}

_IPV4 = re.compile(r"(?<![\w.])(?:\d{1,3}\.){3}\d{1,3}(?:/\d{1,2})?(?![\w.])")
_IPV6 = re.compile(r"(?<![\w:])(?:[0-9a-fA-F]{0,4}:){2,7}[0-9a-fA-F]{0,4}(?![\w:])")
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
# DOMAIN\user:secret  /  DOMAIN\user  (a credential or an account)
_DOMAIN_ACCOUNT = re.compile(r"[\w.-]*\\[^\s:]*(?::\S*)?")
_UNC_OR_DRIVE = re.compile(r"\\\\\S+|[A-Za-z]:\\\S*")
_HASH = re.compile(r"\b[0-9a-fA-F]{16,}(?::[0-9a-fA-F]{16,})?\b")
_FQDN = re.compile(r"\b(?:[A-Za-z0-9-]+\.)+[A-Za-z]{2,}\b")
_FLAG = re.compile(r"\(([^():]{1,40}):([^()]*)\)")
_SPACES = re.compile(r"\s+")


def _flag(match: re.Match) -> str:
    key, value = match.group(1), match.group(2).strip()
    if value.lower() in _SAFE_FLAG_VALUES or re.fullmatch(r"[\d.]+", value):
        return f"({key}:{value})"
    return f"({key}:<VALUE>)"


def redact_message(text: str) -> str:
    """The free-text part of a line with its values replaced."""
    text = _EMAIL.sub("<EMAIL>", text)
    text = _UNC_OR_DRIVE.sub("<PATH>", text)
    text = _DOMAIN_ACCOUNT.sub("<ACCOUNT>", text)
    text = _IPV4.sub("<IP>", text)
    text = _IPV6.sub("<IP>", text)
    text = _HASH.sub("<HASH>", text)
    text = _FQDN.sub("<NAME>", text)
    text = _FLAG.sub(_flag, text)
    return text


# `PROTO  ip  port  HOST  [x] message` — nxc's console columns.
_NXC_COLUMNS = re.compile(r"^(\S+)\s+(\S+)\s+(\d+)\s+(\S+)\s+(.*)$")
# After "[+]" / "[-]", the first token is the credential tried.
_LOGIN_TOKEN = re.compile(r"^(\[[+-]\])\s+(\S+)")
_PASSWORD_ONLY = {"vnc"}


def nxc_line_shape(line: str, known_hosts: Tuple[str, ...] = ()) -> str:
    """A NetExec console line's shape: protocol, port and status marker kept;
    address, host column, credential and every value in the message
    replaced.  ``known_hosts`` are names seen in this file's host column,
    replaced wherever they recur in a message."""
    line = _SPACES.sub(" ", line.strip())
    m = _NXC_COLUMNS.match(line)
    if not m:
        shape = redact_message(line)
    else:
        proto, _addr, port, _host, message = m.groups()
        login = _LOGIN_TOKEN.match(message)
        # user:secret — or, for a password-only protocol (VNC), the token
        # itself is the secret.
        # ("Uploaded:" — a label ending in a colon — is an action, not one.)
        token = login.group(2) if login else ""
        if login and ((":" in token and not token.endswith(":")) or proto.lower() in _PASSWORD_ONLY):
            message = f"{login.group(1)} <CREDENTIAL>" + message[login.end():]
        for name in known_hosts:
            if name:
                message = re.sub(rf"(?<![\w-]){re.escape(name)}(?![\w-])", "<HOST>", message, flags=re.IGNORECASE)
        shape = f"{proto} <IP> {port} <HOST> {redact_message(message)}"
    return shape[:SHAPE_MAX_CHARS]


class ShapeTally:
    """Counts line shapes by kind for an import receipt."""

    def __init__(self) -> None:
        self._counts: Counter = Counter()

    def add(self, kind: str, shape: str) -> None:
        self._counts[(kind, shape)] += 1

    @property
    def total(self) -> int:
        return sum(self._counts.values())

    def receipt(self) -> Optional[Dict[str, object]]:
        """``{"total", "shapes": [{kind, shape, count}]}`` — most frequent
        first, at most MAX_SHAPES — or None when every line was read."""
        if not self._counts:
            return None
        shapes: List[Dict[str, object]] = [
            {"kind": kind, "shape": shape, "count": count}
            for (kind, shape), count in self._counts.most_common(MAX_SHAPES)
        ]
        return {"total": self.total, "distinct": len(self._counts), "shapes": shapes}
