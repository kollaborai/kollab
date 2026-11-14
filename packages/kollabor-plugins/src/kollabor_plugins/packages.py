"""Git sources for installable plugin packages."""

import re
from urllib.parse import urlsplit

_REF = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/-]*\Z", re.ASCII)
_SCP_SOURCE = re.compile(r"git@[A-Za-z0-9.-]+:[^\s?#]+\Z", re.ASCII)


def parse_git_source(source: str) -> tuple[str, str | None]:
    """Validate an install source without echoing a potentially secret URL.

    A ref, when supplied, is resolved to a branch or tag by the installer; this
    function only checks its syntax. Local paths are reserved for dev mode.
    """
    if not isinstance(source, str) or not source or any(c.isspace() for c in source):
        raise ValueError("Invalid plugin Git source")
    url, separator, ref = source.partition("#")
    if separator and not _REF.fullmatch(ref):
        raise ValueError("Invalid plugin Git ref")
    if _SCP_SOURCE.fullmatch(url):
        return url, ref if separator else None
    try:
        parts = urlsplit(url)
        valid = (
            parts.scheme in {"https", "ssh"}
            and bool(parts.hostname)
            and parts.username is None
            and parts.password is None
            and not parts.query
            and not parts.fragment
            and bool(parts.path and parts.path != "/")
            and parts.port != 0
        )
    except ValueError:
        valid = False
    if not valid:
        raise ValueError("Invalid plugin Git source")
    return url, ref if separator else None
