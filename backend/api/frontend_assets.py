"""Snapshot static assets and version the URLs emitted by the frontend."""

from dataclasses import dataclass
from functools import lru_cache
from hashlib import sha256
from html import escape, unescape
from pathlib import Path
import re
from urllib.parse import parse_qsl, unquote, urlencode, urlsplit, urlunsplit


MEDIA_SUFFIXES = {".ico", ".svg", ".png", ".jpg", ".jpeg", ".webp", ".woff", ".woff2"}
ASSET_SUFFIXES = MEDIA_SUFFIXES | {".js", ".mjs", ".css"}
HTML_REFERENCE = re.compile(
    rb"<(?:link|script|img)\b[^>]*?\s(?:href|src)\s*=\s*['\"](?P<url>/[^'\"\s>]+)",
    re.IGNORECASE,
)
CSS_REFERENCE = re.compile(rb"url\(\s*['\"]?(?P<url>/[^)'\"\s]+)", re.IGNORECASE)
REFERENCE_PATTERNS = {".html": HTML_REFERENCE, ".css": CSS_REFERENCE}


@dataclass(frozen=True)
class Asset:
    body: bytes
    version: str
    references: tuple[re.Match[bytes], ...] = ()


def local_file(root: Path, path: str) -> Path | None:
    file_path = root / path
    if file_path.is_file() and file_path.resolve().is_relative_to(root):
        return file_path
    return None


@lru_cache(maxsize=64)
def _read_file(path: Path, _identity: tuple[int, ...]) -> Asset:
    body = path.read_bytes()
    pattern = REFERENCE_PATTERNS.get(path.suffix.lower())
    references = tuple(pattern.finditer(body)) if pattern else ()
    return Asset(body, sha256(body).hexdigest(), references)


@lru_cache(maxsize=16)
def _render(source: Asset, urls: tuple[bytes, ...]) -> Asset:
    parts = []
    offset = 0
    for match, url in zip(source.references, urls):
        start, end = match.span("url")
        parts.extend((source.body[offset:start], url))
        offset = end
    parts.append(source.body[offset:])
    body = b"".join(parts)
    return Asset(body, sha256(body).hexdigest())


def read_asset(root: Path, path: Path) -> Asset:
    # Each worker reuses the same bytes for hashing and serving. A dev edit
    # changes the stat key without needing to restart or re-hash on each GET.
    stat = path.stat()
    source = _read_file(path, (stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns))
    if not source.references:
        return source
    html = path.suffix.lower() == ".html"
    suffixes = ASSET_SUFFIXES if html else MEDIA_SUFFIXES
    urls = tuple(_version_url(root, match.group("url"), suffixes, html) for match in source.references)
    return _render(source, urls)


def _version_url(root: Path, raw_url: bytes, suffixes: set[str], html: bool) -> bytes:
    if raw_url.startswith(b"//"):
        return raw_url
    url = raw_url.decode("utf-8")
    parts = urlsplit(unescape(url) if html else url)
    path = unquote(parts.path).removeprefix("/")
    if Path(path).suffix.lower() not in suffixes or (file_path := local_file(root, path)) is None:
        return raw_url

    query = [(key, value) for key, value in parse_qsl(parts.query, keep_blank_values=True) if key != "v"]
    query.append(("v", read_asset(root, file_path).version))
    versioned = urlunsplit(parts._replace(query=urlencode(query)))
    return (escape(versioned, quote=True) if html else versioned).encode("utf-8")
