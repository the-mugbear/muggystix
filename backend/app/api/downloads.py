"""The ``Content-Disposition`` header of a download — one implementation.

A header value is Latin-1 on the wire: a file name holding anything else
(``сканирование.xml``, a site called ``東京``) made the response fail to
encode — a 500 with nothing in the body — and an unquoted comma made Chrome
refuse the download as "multiple Content-Disposition headers" (review
2026-10-10 H19).  RFC 6266 / RFC 5987 say how a name outside ASCII travels:

    Content-Disposition: attachment; filename=xml_hosts.txt;
                         filename*=UTF-8''%D1%81%D0%BA%D0%B0%D0%BD.xml_hosts.txt

``filename`` is an ASCII stand-in every client understands; ``filename*`` is
the real name, percent-encoded, which every current browser prefers.  A name
that is already plain ASCII is sent as ``filename`` alone, unchanged.

Every route that names a download builds its header here (``attachment``).
``FileResponse(filename=…)`` is Starlette's own implementation of the same
RFC and needs nothing from this module.
"""
from __future__ import annotations

import re
import unicodedata
from typing import Dict
from urllib.parse import quote

# What an ASCII stand-in may hold: an HTTP token with no character that needs
# quoting, so the header never needs a quoted-string.
_NOT_PLAIN = re.compile(r"[^A-Za-z0-9._-]+")
# The real name loses only what no file name may hold.
_CONTROL = re.compile(r"[\x00-\x1f\x7f]+")
_MAX_NAME = 180


def _ascii_stand_in(name: str, fallback: str) -> str:
    """``name`` with every run of other characters as one underscore.  When
    nothing but the extension survives, ``fallback`` takes the stem's place."""
    # Accents first: "résumé.csv" keeps its letters.
    folded = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode("ascii")
    stem, dot, extension = folded.rpartition(".")
    if not dot:
        stem, extension = folded, ""
    stem = re.sub(r"_{2,}", "_", _NOT_PLAIN.sub("_", stem))
    # "(final).xml" is "final.xml", not "final_.xml".
    stem = re.sub(r"_?\._?", ".", stem).strip("._-")
    extension = _NOT_PLAIN.sub("", extension)
    plain = (stem or fallback) + (f".{extension}" if extension else "")
    return plain[:_MAX_NAME]


def content_disposition(filename: str, *, fallback: str = "download") -> str:
    """The header value for a file the browser should save as ``filename``."""
    name = _CONTROL.sub("", filename or "").replace("/", "-").replace("\\", "-").strip()[:_MAX_NAME] or fallback
    plain = _ascii_stand_in(name, fallback)
    if plain == name:
        return f"attachment; filename={plain}"
    return f"attachment; filename={plain}; filename*=UTF-8''{quote(name, safe='')}"


def attachment(filename: str, *, fallback: str = "download") -> Dict[str, str]:
    """``headers=attachment("hosts.csv")`` — the one way a route names a download."""
    return {"Content-Disposition": content_disposition(filename, fallback=fallback)}
