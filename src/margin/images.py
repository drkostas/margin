"""Pictures in a published file: a local image a markdown file shows is uploaded to Outline.

A file in the repository refers to its pictures by a relative path (`![The Time tab](shots/time.png)`),
which works on GitHub and in an editor but means nothing to Outline. Before a body is sent, each
such reference is uploaded once as an attachment of that document and the reference is pointed at
it. The repository file keeps its relative path.

⭐ A PICTURE IS PART OF THE FILE. Its bytes count in what margin compares, so a changed picture is
published even when the markdown did not change, and an unchanged picture is never uploaded twice
(each upload is remembered by document and content).

⭐ ATTACHED TO ITS DOCUMENT. An attachment belongs to the document it was uploaded for, so everyone
who can read that document can see it. One uploaded for another document (the private draft margin
uses to learn Outline's markdown) would be visible to margin's own account only.

Only images that are files next to the markdown are touched: a web address, an address Outline
already serves, a data: URI or a missing file is left as it is.
"""
from __future__ import annotations

import hashlib
import re
from pathlib import Path

TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".gif": "image/gif",
         ".webp": "image/webp", ".svg": "image/svg+xml"}

# ![alt](path) and ![alt](path "title"), the path optionally in <angle brackets>
IMAGE = re.compile(r'!\[(?P<alt>[^\]]*)\]\(\s*(?P<path><[^>]+>|[^)\s]+)(?P<title>\s+"[^"]*")?\s*\)')


def _target(ref: str) -> str:
    return ref[1:-1] if ref.startswith("<") and ref.endswith(">") else ref


def local(body: str, base: Path) -> list[tuple[str, Path]]:
    """(reference as written, file) for each local image the body shows, in order, once each."""
    out, seen = [], set()
    for m in IMAGE.finditer(body):
        ref = m.group("path")
        target = _target(ref)
        if re.match(r"^[a-z][a-z0-9+.-]*:", target, re.I) or target.startswith(("/", "#")):
            continue                          # a web address, data:, or one Outline already serves
        path = (base / target.split("#", 1)[0].split("?", 1)[0]).resolve()
        if path.suffix.lower() in TYPES and path.is_file() and ref not in seen:
            seen.add(ref)
            out.append((ref, path))
    return out


def digest(body: str, base: Path) -> str:
    """A line per local image with its content's hash, or "" when there is none. Added to the
    file's text where margin compares it, so a changed picture counts as a changed file."""
    lines = [f"<!-- margin image {ref} {hashlib.sha256(p.read_bytes()).hexdigest()} -->"
             for ref, p in local(body, base)]
    return "\n" + "\n".join(lines) if lines else ""


def point(body: str, urls: dict[str, str]) -> str:
    """The body with each reference in `urls` replaced by its Outline address."""
    def swap(m: re.Match) -> str:
        url = urls.get(m.group("path"))
        if url is None:
            return m.group(0)
        return f"![{m.group('alt')}]({url}{m.group('title') or ''})"
    return IMAGE.sub(swap, body)
