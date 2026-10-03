"""Which config paths hold credentials. One rule for the terminal and the web."""

from typing import Any, Mapping, Optional

# Suffix match: "bridge_token" is a secret, "token_threshold_k" is not.
SECRET_SUFFIXES = ("key", "token", "secret", "password")


def is_secret_path(path: str, widget: Optional[Mapping[str, Any]] = None) -> bool:
    """True when the widget definition says ``"secret": true`` or the last path
    segment ends in key, token, secret or password.

    A secret's value is never drawn or sent to a browser.
    """
    if widget is not None and widget.get("secret") is True:
        return True
    return path.rsplit(".", 1)[-1].lower().endswith(SECRET_SUFFIXES)
