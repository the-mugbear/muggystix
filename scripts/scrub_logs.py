#!/usr/bin/env python3
"""Remove identifying information from a collect-logs.sh bundle, in place.

Standard library only — runs on the deployment host with nothing installed.

    scrub_logs.py <dir> --terms <file> [--terms <file> ...] --report <file>

Every ``*.txt`` under ``<dir>`` is rewritten.  Values are replaced by STABLE
pseudonyms (``<ip-7>``, ``<host-3>``, ``<file-2>`` …) so a reader can still
follow one address or one file through the logs without learning what it was.

Two sources of what to remove:

1. **Known values** — the ``--terms`` files, one ``category<TAB>value`` per
   line (``collect-logs.sh`` harvests them from the database and ``.env``:
   project, user, site and label names, e-mails, upload filenames, hostnames,
   NetBIOS and DNS names, scope domains, registrant orgs, AD domains, secrets).
   Operators can add their own (client name, internal domain) with
   ``collect-logs.sh --terms FILE``; a line without a tab is category ``term``.
2. **Shapes** — whatever looks identifying even if the database never saw it:
   IPv4/IPv6/MAC addresses, e-mails, URLs, FQDNs under common or internal TLDs,
   ``DOMAIN\\user``, home-directory paths, JWTs / bearer tokens / ``key=value``
   secrets, 32-hex hashes, query strings of request lines, SQLAlchemy
   ``[parameters: …]`` dumps and PostgreSQL ``STATEMENT`` / ``DETAIL`` values
   (psycopg2 interpolates literals, so both carry row data).

Fails closed: any error exits non-zero and the caller deletes the bundle.
The report lists how many of each kind were replaced — never the values.
"""
from __future__ import annotations

import argparse
import ipaddress
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

# Values too generic to identify anyone, whose replacement would only
# destroy the log (a user called "admin", a host called "localhost").
GENERIC = {
    "admin", "administrator", "root", "test", "tests", "user", "users", "guest",
    "default", "demo", "localhost", "unknown", "none", "null", "true", "false",
    "backend", "frontend", "worker", "report-worker", "db", "postgres", "nginx",
    "networkmapper", "bluestick", "nmapuser", "project", "scan", "host", "hosts",
    "info", "debug", "warning", "error", "http", "https", "www", "workgroup",
}

# Public domains whose names carry debugging value and identify no client.
SAFE_DOMAINS = (
    "nmap.org", "tenable.com", "greenbone.net", "github.com", "githubusercontent.com",
    "docker.io", "docker.com", "pypi.org", "python.org", "npmjs.org", "npmjs.com",
    "jsdelivr.net", "cloudflare.com", "googleapis.com", "gstatic.com", "anthropic.com",
    "claude.ai", "claude.com", "sqlalche.me", "fastapi.tiangolo.com", "quarto.org",
    "mitre.org", "nist.gov", "cve.org", "first.org", "iana.org", "arin.net", "ripe.net",
    "apnic.net", "lacnic.net", "afrinic.net", "projectdiscovery.io", "postgresql.org",
    "example.com", "example.org", "example.net", "ghcr.io", "quay.io", "gcr.io",
    "debian.org", "ubuntu.com", "alpinelinux.org", "nginx.org", "nodejs.org",
)

# Last labels that make a dotted token a hostname.  Deliberately excludes
# TLDs that collide with code (``logger.info``, ``main.py``, ``config.app``).
TLDS = {
    "com", "net", "org", "edu", "gov", "mil", "int", "io", "co", "us", "uk", "ca",
    "au", "nz", "de", "fr", "nl", "be", "ch", "at", "se", "no", "dk", "fi", "ie",
    "es", "it", "pl", "cz", "eu", "jp", "cn", "kr", "in", "sg", "hk", "br", "mx",
    "za", "ru", "biz", "cloud", "online", "tech", "systems", "services", "global",
    "local", "lan", "corp", "internal", "intranet", "intra", "localdomain", "home",
    "priv", "private", "ad", "domain", "office", "arpa",
}

SAFE_IPS = {"127.0.0.1", "0.0.0.0", "::1", "::"}

CATEGORY_LABEL = {
    "project": "project", "user": "user", "email": "email", "fullname": "person",
    "site": "site", "label": "label", "scope": "scope", "client": "client",
    "report": "report", "file": "file", "host": "host", "fqdn": "host",
    "domain": "domain", "org": "org", "secret": "secret", "env": "config",
    "term": "term", "deploydir": "deploydir",
}

# Categories matched as whole TOKENS through a set (they can number in the
# tens of thousands — hostnames, DNS names); the rest are matched as
# SUBSTRINGS through one trie-shaped regex (filenames inside storage paths,
# multi-word names).
TOKEN_CATEGORIES = {"host", "fqdn"}
VOCAB_CATEGORIES = {"host", "fqdn", "user", "label"}


class Pseudonyms:
    def __init__(self) -> None:
        self._maps: dict[str, dict[str, str]] = defaultdict(dict)
        self.counts: Counter = Counter()

    def get(self, kind: str, value: str) -> str:
        m = self._maps[kind]
        key = value.lower()
        if key not in m:
            m[key] = f"<{kind}-{len(m) + 1}>"
        self.counts[kind] += 1
        return m[key]


def _trie_regex(words: list[str]) -> re.Pattern | None:
    """One alternation shaped as a trie — linear in the text, not in the word
    count, so thousands of filenames cost little more than one."""
    trie: dict = {}
    for w in words:
        node = trie
        for ch in w.lower():
            node = node.setdefault(ch, {})
        node[""] = True

    def build(node: dict) -> str:
        end = "" in node
        branches = [re.escape(ch) + build(child) for ch, child in sorted(node.items()) if ch]
        if not branches:
            return ""
        body = branches[0] if len(branches) == 1 else "(?:" + "|".join(branches) + ")"
        return f"(?:{body})?" if end else body

    if not trie:
        return None
    # Not inside a longer word: "acme" must not eat "acmeville", but may sit
    # beside punctuation, underscores or a UUID prefix in a storage path.
    return re.compile(r"(?<![A-Za-z0-9])(?:" + build(trie) + r")(?![A-Za-z0-9])", re.IGNORECASE)


VOCAB_WORD = re.compile(r"[A-Za-z][A-Za-z0-9_-]{2,}")


def load_vocabulary(dirs: list[str]) -> set[str]:
    """Every word in BlueStick's own source.  A value that is product or tool
    vocabulary ("nessus", "status", "tls", "apache") names no client, and
    replacing it would wreck the logs — e.g. a fixture host "status.lab" must
    not turn the word "status" into <host-8> everywhere.  A client's name is
    not in the code."""
    words: set[str] = set()
    for d in dirs:
        root = Path(d)
        if not root.is_dir():
            continue
        for f in root.rglob("*"):
            if f.suffix not in (".py", ".ts", ".tsx", ".json") or "node_modules" in f.parts:
                continue
            try:
                text = f.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            words.update(w.lower() for w in VOCAB_WORD.findall(text))
    return words


def load_terms(paths: list[str], vocabulary: set[str] = frozenset()) -> tuple[dict[str, str], dict[str, str], list[str]]:
    """→ (token → category, phrase → category, domains for suffix matching).
    Single-word values found in ``vocabulary`` are skipped (secrets never)."""
    tokens: dict[str, str] = {}
    phrases: dict[str, str] = {}
    domains: list[str] = []
    for p in paths:
        for raw in Path(p).read_text(encoding="utf-8", errors="replace").splitlines():
            if not raw.strip() or raw.startswith("#"):
                continue
            cat, _, value = raw.partition("\t") if "\t" in raw else ("term", "", raw)
            value = value.strip().strip(".")
            cat = cat.strip() or "term"
            if len(value) < 3 or value.lower() in GENERIC or value.isdigit() or value in SAFE_IPS:
                continue
            # Only machine-ish names get the vocabulary pass.  A project,
            # client, org, person or site name — or anything the operator
            # passed — is removed even when it is an ordinary word.
            if cat in VOCAB_CATEGORIES and value.lower() in vocabulary:
                continue
            if cat == "domain" and "." in value:
                domains.append(value.lower().lstrip("*."))
            if cat in TOKEN_CATEGORIES and not re.search(r"\s", value):
                tokens[value.lower()] = cat
            else:
                phrases.setdefault(value.lower(), cat)
            # A host's short name ("web01" of web01.acme.corp) identifies too.
            if cat in ("host", "fqdn") and "." in value:
                short = value.split(".", 1)[0].lower()
                if (len(short) >= 3 and short not in GENERIC and short not in vocabulary
                        and not short.isdigit()):
                    tokens.setdefault(short, cat)
    return tokens, phrases, domains


# Every pattern is anchored at the start of its run (a lookbehind) or bounded,
# so a multi-kilobyte base64/hex blob in a log line stays linear.
IPV4 = re.compile(r"(?<![\d.])(?:\d{1,3}\.){3}\d{1,3}(?!\d|\.\d)(?:/\d{1,2}(?!\d))?")
IPV6_CANDIDATE = re.compile(r"(?<![\w:.])[0-9A-Fa-f:.]{2,45}(?:%\w+)?(?:/\d{1,3})?(?![\w:])")
MAC = re.compile(r"(?<![\w:-])(?:[0-9A-Fa-f]{2}[:-]){5}[0-9A-Fa-f]{2}(?![\w:-])")
EMAIL = re.compile(r"(?<![A-Za-z0-9._%+-])[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+")
URL = re.compile(r"\b(?:https?|ftp|ldaps?|smb)://[^\s\"'<>)\]]+", re.IGNORECASE)
DOTTED = re.compile(r"(?<![\w.-])(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)+[A-Za-z][A-Za-z0-9-]{1,62}(?![\w-])")
TOKEN = re.compile(r"(?<![\w$-])[A-Za-z0-9][A-Za-z0-9_$-]{2,}(?![\w$-])")
DOMAIN_USER = re.compile(r"\b[A-Z][A-Z0-9-]{1,14}\\{1,2}[A-Za-z0-9._$-]+")
HOME_PATH = re.compile(r"(/home/|/Users/|[A-Za-z]:\\{1,2}Users\\{1,2})([^/\\\s\"']+)")
JWT = re.compile(r"\beyJ[\w-]{8,}\.[\w-]{8,}\.[\w-]{8,}")
BEARER = re.compile(r"(?i)\b(bearer|basic)\s+[A-Za-z0-9._~+/=-]{8,}")
KEYVAL_SECRET = re.compile(
    r"(?i)(?<![\w-])([\w-]*(?:api[_-]?key|token|secret|password|passwd|pwd|credential|authorization)[\w-]*)"
    r"(\s*[:=]\s*|\"\s*:\s*\")([^\s\"',;&}]+)"
)
HEX32 = re.compile(r"(?<![0-9A-Fa-f])[0-9A-Fa-f]{32}(?![0-9A-Fa-f])")
REQUEST_QUERY = re.compile(r"\b(GET|POST|PUT|PATCH|DELETE|OPTIONS|HEAD) (/[^\s?\"]*)\?[^\s\"]*")
SQL_PARAMS = re.compile(r"\[parameters: .*$", re.MULTILINE)
PG_STATEMENT = re.compile(r"(STATEMENT:\s*).*$", re.MULTILINE)
PG_DETAIL_KEY = re.compile(r"(DETAIL:\s*Key \(([^)]*)\)=)\(.*?\)( already exists| is not present| is still referenced)?")
PG_DETAIL_OTHER = re.compile(r"(DETAIL:[ \t]*)(?![ \t]|Key \().*$", re.MULTILINE)


def _is_safe_domain(name: str) -> bool:
    n = name.lower()
    return any(n == d or n.endswith("." + d) for d in SAFE_DOMAINS)


class Scrubber:
    def __init__(self, tokens: dict[str, str], phrases: dict[str, str], domains: list[str]):
        self.p = Pseudonyms()
        self.tokens = tokens
        self.phrase_cat = phrases
        self.phrase_re = _trie_regex(sorted(phrases, key=len, reverse=True))
        self.domains = sorted(set(domains), key=len, reverse=True)

    # -- replacement callbacks -------------------------------------------
    def _ipv4(self, m: re.Match) -> str:
        text = m.group(0)
        addr, _, prefix = text.partition("/")
        try:
            ip = ipaddress.IPv4Address(addr)
        except ValueError:
            return text  # 999.1.2.3 — a version string, not an address
        if addr in SAFE_IPS:
            return text
        # Keep the one fact that matters when debugging reachability.
        kind = "ip" if not ip.is_private else "ip-private"
        return self.p.get(kind, addr) + (f"/{prefix}" if prefix else "")

    def _ipv6(self, m: re.Match) -> str:
        text = m.group(0)
        tail = "." if text.endswith(".") else ""  # end of a sentence
        addr = text[: len(text) - len(tail)].split("/", 1)[0].split("%", 1)[0]
        if addr.count(":") < 2 or addr in SAFE_IPS:
            return text
        try:
            ipaddress.IPv6Address(addr)
        except ValueError:
            return text  # 12:34:56 — a clock, not an address
        return self.p.get("ip6", addr) + tail

    def _url(self, m: re.Match) -> str:
        url = m.group(0)
        host = re.sub(r"^[a-z]+://(?:[^@/]*@)?", "", url, flags=re.IGNORECASE).split("/", 1)[0]
        host = host.rsplit(":", 1)[0].strip("[]")
        if host in ("localhost", "backend", "frontend", "db") or host in SAFE_IPS:
            return url
        if _is_safe_domain(host):
            return url
        return self.p.get("url", url)

    def _dotted(self, m: re.Match) -> str:
        name = m.group(0)
        low = name.lower()
        if _is_safe_domain(low):
            return name
        if low in self.tokens:
            return self.p.get("host", low)
        for d in self.domains:
            if low == d or low.endswith("." + d):
                return self.p.get("host", low)
        if low.rsplit(".", 1)[-1] in TLDS:
            return self.p.get("host", low)
        return name

    def _token(self, m: re.Match) -> str:
        tok = m.group(0)
        cat = self.tokens.get(tok.lower())
        return self.p.get(CATEGORY_LABEL.get(cat, "host"), tok) if cat else tok

    def _phrase(self, m: re.Match) -> str:
        text = m.group(0)
        cat = self.phrase_cat.get(text.lower(), "term")
        return self.p.get(CATEGORY_LABEL.get(cat, "term"), text)

    def _secret(self, m: re.Match) -> str:
        self.p.counts["secret"] += 1
        return f"{m.group(1)}{m.group(2)}<redacted>"

    def _counted(self, kind: str, replacement: str):
        def repl(m: re.Match) -> str:
            self.p.counts[kind] += 1
            return m.expand(replacement)
        return repl

    # -- the pipeline ------------------------------------------------------
    def scrub(self, text: str) -> str:
        # Row data in database errors first: it can hold anything.
        text = SQL_PARAMS.sub(self._counted("sql-parameters", "[parameters: <redacted>]"), text)
        text = PG_STATEMENT.sub(self._counted("pg-statement", r"\1<redacted>"), text)
        text = PG_DETAIL_KEY.sub(self._counted("pg-detail", r"\1(<redacted>)\3"), text)
        text = PG_DETAIL_OTHER.sub(self._counted("pg-detail", r"\1<redacted>"), text)
        # Credentials before anything splits them apart.
        text = JWT.sub(self._counted("secret", "<jwt>"), text)
        text = BEARER.sub(self._counted("secret", r"\1 <redacted>"), text)
        text = KEYVAL_SECRET.sub(self._secret, text)
        text = EMAIL.sub(lambda m: self.p.get("email", m.group(0)), text)
        # Known values (the database's own names) before generic shapes, so a
        # filename is <file-n> rather than a <host-n> of its first dotted part.
        if self.phrase_re is not None:
            text = self.phrase_re.sub(self._phrase, text)
        text = REQUEST_QUERY.sub(self._counted("query-string", r"\1 \2?<query>"), text)
        text = URL.sub(self._url, text)
        text = MAC.sub(lambda m: self.p.get("mac", m.group(0)), text)
        text = IPV4.sub(self._ipv4, text)
        text = IPV6_CANDIDATE.sub(self._ipv6, text)
        text = DOMAIN_USER.sub(lambda m: self.p.get("domain-user", m.group(0)), text)
        text = HOME_PATH.sub(self._counted("home-path", r"\1<user>"), text)
        text = DOTTED.sub(self._dotted, text)
        if self.tokens:
            text = TOKEN.sub(self._token, text)
        text = HEX32.sub(self._counted("hash", "<hash32>"), text)
        return text


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("directory")
    ap.add_argument("--terms", action="append", default=[], help="category<TAB>value per line")
    ap.add_argument("--report", required=True, help="where to write the replacement counts")
    ap.add_argument("--vocabulary", action="append", default=[],
                    help="source directory whose words are never treated as identifying")
    args = ap.parse_args()

    vocabulary = load_vocabulary(args.vocabulary)
    tokens, phrases, domains = load_terms(args.terms, vocabulary)
    scrubber = Scrubber(tokens, phrases, domains)
    files = sorted(Path(args.directory).rglob("*.txt"))
    for f in files:
        # Line by line: bounded memory on a multi-GB combined log.
        tmp = f.with_suffix(f.suffix + ".scrubbing")
        with f.open("r", encoding="utf-8", errors="replace") as src, tmp.open("w", encoding="utf-8") as dst:
            for line in src:
                dst.write(scrubber.scrub(line))
        tmp.replace(f)

    counts = scrubber.p.counts
    lines = [
        "=== ANONYMISATION REPORT ===",
        f"Files scrubbed: {len(files)}",
        f"Known values loaded: {len(tokens)} tokens, {len(phrases)} phrases, {len(domains)} domains"
        f" (vocabulary: {len(vocabulary)} words from BlueStick's source)",
        "",
        "Replacements by kind (values are never listed; the same value keeps the same",
        "pseudonym throughout the bundle):",
    ]
    lines += [f"  {kind:<16} {n}" for kind, n in sorted(counts.items())] or ["  (none)"]
    Path(args.report).write_text("\n".join(lines) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:  # fail closed — the caller discards the bundle
        print(f"scrub_logs.py: {type(exc).__name__}: {exc}", file=sys.stderr)
        sys.exit(1)
