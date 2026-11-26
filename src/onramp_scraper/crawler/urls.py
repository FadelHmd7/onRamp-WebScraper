"""URL normalization.

Every link goes through :func:`normalize_url` before the shared seen-set
check, so one page written several ways is crawled once.

Only changes that can never turn two different pages into one URL are made
(RFC 3986 section 6 "safe" normalizations, plus dropping tracking
parameters). Trailing slashes, ``index.html`` and path case are left alone:
servers may treat them as different pages. If they serve the same content,
the content hash catches it later.
"""

import re
from string import ascii_letters, digits
from urllib.parse import quote, unquote, urljoin, urlsplit, urlunsplit

ALLOWED_SCHEMES = frozenset({"http", "https"})
DEFAULT_PORTS = {"http": 80, "https": 443}

# Query parameters that only track the visitor and never change the page.
TRACKING_PARAMS = frozenset(
    {"gclid", "dclid", "fbclid", "msclkid", "mc_cid", "mc_eid", "_ga", "_gl", "yclid"}
)
TRACKING_PREFIXES = ("utm_",)

_UNRESERVED = frozenset(ascii_letters + digits + "-._~")
_PERCENT_ESCAPE = re.compile(r"%([0-9A-Fa-f]{2})")
# Characters allowed unescaped in a path / a query (RFC 3986 pchar, plus "/" and "?").
_PATH_SAFE = "/:@!$&'()*+,;=-._~%"
_QUERY_SAFE = "/?:@!$'()*+,;=-._~%"


def _normalize_escapes(text: str, safe: str) -> str:
    """Decode escaped unreserved characters, uppercase the rest, escape raw unsafe ones."""

    def fix(match: re.Match[str]) -> str:
        char = chr(int(match.group(1), 16))
        return char if char in _UNRESERVED else f"%{match.group(1).upper()}"

    return quote(_PERCENT_ESCAPE.sub(fix, text), safe=safe)


def _remove_dot_segments(path: str) -> str:
    """RFC 3986 section 5.2.4: resolve "." and ".." segments."""
    output: list[str] = []
    for segment in path.split("/"):
        if segment == "..":
            if len(output) > 1:
                output.pop()
        elif segment != ".":
            output.append(segment)
    result = "/".join(output)
    # A trailing "." or ".." refers to a directory, so it keeps its slash.
    if path.endswith(("/.", "/..")):
        result += "/"
    return result if result.startswith("/") else "/" + result


def _is_tracking(key: str) -> bool:
    name = unquote(key).lower()
    return name in TRACKING_PARAMS or name.startswith(TRACKING_PREFIXES)


def _normalize_query(query: str) -> str:
    pairs = []
    for pair in query.split("&"):
        if not pair:
            continue
        key = pair.split("=", 1)[0]
        if _is_tracking(key):
            continue
        # "+" is left alone: servers disagree on whether it means a space.
        pairs.append(_normalize_escapes(pair, _QUERY_SAFE))
    return "&".join(sorted(pairs))


def _normalize_host(host: str) -> str | None:
    host = host.rstrip(".").lower()
    if not host:
        return None
    if ":" in host:  # IPv6 literal
        return f"[{host}]"
    try:
        return host.encode("idna").decode("ascii")
    except UnicodeError:
        return None


def normalize_url(url: str, base: str | None = None) -> str | None:
    """Return the canonical form of ``url``, or ``None`` if it cannot be crawled.

    ``base`` is the page the link was found on; relative links resolve against it.
    """
    url = url.strip()
    if base is not None:
        url = urljoin(base, url)
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError:  # malformed host or port
        return None

    scheme = parts.scheme.lower()
    if scheme not in ALLOWED_SCHEMES or parts.hostname is None:
        return None
    host = _normalize_host(parts.hostname)
    if host is None:
        return None
    netloc = host if port in (None, DEFAULT_PORTS[scheme]) else f"{host}:{port}"

    path = _normalize_escapes(_remove_dot_segments(parts.path or "/"), _PATH_SAFE)
    query = _normalize_query(parts.query)
    # Credentials and the fragment are dropped on purpose.
    return urlunsplit((scheme, netloc, path, query, ""))


def host_of(url: str) -> str:
    """The rate-limit key for a URL: its lowercase host name, as the spec defines a domain."""
    return (urlsplit(url).hostname or "").lower()
