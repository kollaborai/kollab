"""Apps catalog: ``bundles/data/apps.json`` merged with ``~/.kollab/apps.json``.

``validate_catalog`` holds every schema rule. The bundled file, user overrides,
``scripts/validate_apps.py`` and the tests all use it.
"""

import json
import logging
import re
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import urlsplit

from kollabor_config.config_utils import resolve_global_path

logger = logging.getLogger(__name__)

KINDS = ("mcp", "cli", "skill")
CLI_MANAGERS = ("brew", "npm", "uv", "pipx")
BUILTIN_TOKENS = ("workspace", "home")
DESCRIPTION_MAX = 90

_ID_RE = re.compile(r"[a-z0-9][a-z0-9-]{0,47}")
_ENV_RE = re.compile(r"[A-Z][A-Z0-9_]*")
TOKEN_RE = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")
_PACKAGE_RE = re.compile(r"[A-Za-z0-9@/._+-]+")
_BIN_RE = re.compile(r"[A-Za-z0-9._+-]+")
_HEX_RE = re.compile(r"[0-9A-Fa-f]{6}")
_SVG_PATH_RE = re.compile(r"[MmLlHhVvCcSsQqTtAaZz0-9 ,.+\-eE]+")
_REPO_RE = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+")
_REF_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/-]*")
_ROW_RE = re.compile(r"(mcp|cli|skill):[a-z0-9][a-z0-9-]{0,47}")


def _bundled_file() -> Path | None:
    candidates = [
        # repo tree or wheel: this file is kollabor/apps/catalog.py
        Path(__file__).resolve().parents[2] / "bundles" / "data" / "apps.json",
        resolve_global_path("bundles", "data", "apps.json"),
    ]
    return next((path for path in candidates if path.exists()), None)


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("Ignoring apps catalog %s: %s", path, exc)
        return None


def _merge(bundled: dict[str, Any], user: Any) -> dict[str, Any]:
    if not isinstance(user, dict) or not isinstance(user.get("apps", []), list):
        raise ValueError("expected an object with an apps list")
    apps = {(app.get("kind"), app.get("id")): app for app in bundled.get("apps", [])}
    for app in user.get("apps", []):
        if not isinstance(app, dict):
            raise ValueError("apps entries must be objects")
        apps[(app.get("kind"), app.get("id"))] = app
    return {**bundled, "apps": list(apps.values())}


def load_catalog(user_path: Path | None = None) -> dict[str, Any]:
    """Return the bundled catalog with the user's apps.json merged on top by (kind, id).

    A user file that cannot be read, merged or validated is logged and ignored.
    """
    bundled_path = _bundled_file()
    bundled = _read_json(bundled_path) if bundled_path else None
    if not isinstance(bundled, dict):
        logger.error("Bundled apps catalog missing or unreadable; Apps will be empty")
        return {"schema_version": 1, "updated": "", "recommended": [], "featured": [], "apps": []}

    path = user_path or resolve_global_path("apps.json")
    if not path.exists():
        return bundled
    user = _read_json(path)
    if user is None:
        return bundled
    try:
        merged = _merge(bundled, user)
    except (AttributeError, TypeError, ValueError) as exc:
        logger.warning("Ignoring %s: %s", path, exc)
        return bundled
    errors = validate_catalog(merged)
    if errors:
        logger.warning("Ignoring %s: %s", path, "; ".join(errors))
        return bundled
    return merged


def _nonempty_str(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _mcp_problems(mcp: dict[str, Any]) -> list[str]:
    problems = []
    if not _nonempty_str(mcp.get("command")):
        problems.append("mcp.command must be a non-empty string")
    args = mcp.get("args", [])
    if not isinstance(args, list) or not all(isinstance(arg, str) for arg in args):
        return problems + ["mcp.args must be a list of strings"]

    inputs = mcp.get("inputs", [])
    if not isinstance(inputs, list) or not all(isinstance(inp, dict) for inp in inputs):
        return problems + ["mcp.inputs must be a list of objects"]
    names: set[str] = set()
    arg_names: set[str] = set()
    for inp in inputs:
        name = inp.get("name")
        if not (isinstance(name, str) and _ENV_RE.fullmatch(name)):
            problems.append(f"input name {name!r} must be UPPER_SNAKE_CASE")
            continue
        if name in names:
            problems.append(f"input {name} is declared twice")
        names.add(name)
        to = inp.get("to", "env")
        if to not in ("env", "arg"):
            problems.append(f"{name}: to must be env or arg")
        if to == "arg":
            arg_names.add(name)
        if inp.get("secret") and to == "arg":
            problems.append(f"{name}: a secret cannot be an arg (it would show in previews)")
        if not _nonempty_str(inp.get("label")):
            problems.append(f"{name}: label is required")
        for flag in ("required", "secret"):
            if flag in inp and not isinstance(inp[flag], bool):
                problems.append(f"{name}: {flag} must be true or false")
        for text in ("default", "help"):
            if text in inp and not isinstance(inp[text], str):
                problems.append(f"{name}: {text} must be a string")

    for arg in args:
        for token in TOKEN_RE.findall(arg):
            if token not in arg_names and token not in BUILTIN_TOKENS:
                problems.append(f"args use undeclared token {{{token}}}")
    return problems


def _cli_problems(cli: dict[str, Any]) -> list[str]:
    problems = []
    if not (isinstance(cli.get("bin"), str) and _BIN_RE.fullmatch(cli["bin"])):
        problems.append("cli.bin must be an executable name")
    install = cli.get("install")
    if not isinstance(install, list) or not install:
        return problems + ["cli.install needs at least one method"]
    for method in install:
        if not isinstance(method, dict):
            problems.append("cli.install entries must be objects")
            continue
        if method.get("manager") not in CLI_MANAGERS:
            problems.append(f"install manager must be one of {', '.join(CLI_MANAGERS)}")
        package = method.get("package")
        if not (isinstance(package, str) and _PACKAGE_RE.fullmatch(package)):
            problems.append(f"install package {package!r} is not a valid package name")
        if "cask" in method and (method.get("manager") != "brew" or not isinstance(method["cask"], bool)):
            problems.append("cask is only valid on brew installs and must be true or false")
    if "post_install" in cli and not isinstance(cli["post_install"], str):
        problems.append("post_install must be a string")
    return problems


def _skill_problems(skill: dict[str, Any]) -> list[str]:
    problems = []
    if not (isinstance(skill.get("repo"), str) and _REPO_RE.fullmatch(skill["repo"])):
        problems.append("skill.repo must be owner/name")
    path = skill.get("path")
    if not _nonempty_str(path) or PurePosixPath(path).is_absolute() or ".." in PurePosixPath(path).parts:
        problems.append("skill.path must be a relative folder without ..")
    ref = skill.get("ref")
    if not (isinstance(ref, str) and _REF_RE.fullmatch(ref)) or ".." in ref:
        problems.append("skill.ref must be a branch or tag name")
    return problems


_KIND_CHECKS = {"mcp": _mcp_problems, "cli": _cli_problems, "skill": _skill_problems}


def _app_problems(app: dict[str, Any]) -> list[str]:
    problems = []
    kind, app_id = app.get("kind"), app.get("id")
    if not (isinstance(app_id, str) and _ID_RE.fullmatch(app_id)):
        problems.append("id must match [a-z0-9][a-z0-9-]{0,47}")
    if kind not in KINDS:
        problems.append("kind must be mcp, cli or skill")
    for key in ("name", "publisher"):
        if not _nonempty_str(app.get(key)):
            problems.append(f"{key} must be a non-empty string")
    description = app.get("description")
    if not _nonempty_str(description) or len(description) > DESCRIPTION_MAX:
        problems.append(f"description must be 1-{DESCRIPTION_MAX} characters")
    homepage = app.get("homepage")
    if not isinstance(homepage, str) or urlsplit(homepage).scheme != "https" or not urlsplit(homepage).netloc:
        problems.append("homepage must be an https URL")
    if not isinstance(app.get("official"), bool):
        problems.append("official must be true or false")
    tags = app.get("tags", [])
    if not isinstance(tags, list) or any(not _nonempty_str(tag) or tag != tag.lower() for tag in tags):
        problems.append("tags must be lowercase strings")
    requires = app.get("requires", [])
    if not isinstance(requires, list) or any(not (isinstance(r, str) and _BIN_RE.fullmatch(r)) for r in requires):
        problems.append("requires must be executable names")
    icon = app.get("icon")
    if icon is not None and not (
        isinstance(icon, dict)
        and isinstance(icon.get("path"), str)
        and _SVG_PATH_RE.fullmatch(icon["path"])
        and isinstance(icon.get("hex"), str)
        and _HEX_RE.fullmatch(icon["hex"])
    ):
        problems.append("icon needs path (SVG path data) and hex (6 digits)")
    if kind in KINDS:
        for other in KINDS:
            if other != kind and other in app:
                problems.append(f"{other} object is not allowed on a {kind} app")
        if not isinstance(app.get(kind), dict):
            problems.append(f"{kind} object is required")
        else:
            problems += _KIND_CHECKS[kind](app[kind])
    return problems


def validate_catalog(data: Any) -> list[str]:
    """Return every rule the catalog breaks. An empty list means valid."""
    if not isinstance(data, dict):
        return ["catalog must be a JSON object"]
    errors: list[str] = []
    if data.get("schema_version") != 1:
        errors.append("schema_version must be 1")
    if not _nonempty_str(data.get("updated")):
        errors.append("updated must be a date string")
    apps = data.get("apps")
    if not isinstance(apps, list):
        return errors + ["apps must be a list"]

    row_ids: set[str] = set()
    for index, app in enumerate(apps):
        if not isinstance(app, dict):
            errors.append(f"apps[{index}]: must be an object")
            continue
        row_id = f"{app.get('kind')}:{app.get('id')}"
        errors += [f"{row_id}: {problem}" for problem in _app_problems(app)]
        if row_id in row_ids:
            errors.append(f"{row_id}: kind and id must be unique")
        row_ids.add(row_id)

    for key in ("recommended", "featured"):
        refs = data.get(key)
        if not isinstance(refs, list):
            errors.append(f"{key} must be a list of row ids")
            continue
        for ref in refs:
            if not (isinstance(ref, str) and _ROW_RE.fullmatch(ref)):
                errors.append(f"{key}: {ref!r} is not a kind:id row id")
            elif ref not in row_ids:
                errors.append(f"{key}: {ref} is not in apps")
    if isinstance(data.get("recommended"), list) and isinstance(data.get("featured"), list):
        for ref in sorted(set(data["recommended"]) & set(data["featured"]), key=str):
            errors.append(f"recommended: {ref} is also featured; a row shows in one of the two")
    return errors
