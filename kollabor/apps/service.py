"""Apps: the directory of MCP servers, CLI tools and skills, and what Add does.

Row state comes from the catalog, the MCP settings file, PATH and the skill
library. MCP writes go through MCPManager, CLI installs run a catalog argv
(never a shell), and skill archives come from codeload.github.com only.
"""

import asyncio
import io
import json
import logging
import os
import shlex
import shutil
import tarfile
import tempfile
import urllib.request
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Awaitable, BinaryIO, Callable

from kollabor_agent import MCPManager
from kollabor_agent.agent_manager import Skill
from kollabor_config.config_utils import get_bundled_skills_dir

from . import registry
from .catalog import TOKEN_RE, load_catalog

logger = logging.getLogger(__name__)

CLI_TIMEOUT_SECONDS = 600  # ponytail: blocking install; background job + polling if installs get slow
# ponytail: a skill costs its whole repo's tarball (OfficeCLI 74 MB, HyperFrames 124 MB);
# fetch just the folder through the GitHub trees API if big skill repos become common.
DOWNLOAD_MAX_BYTES = 300 * 1024 * 1024  # the whole repo tarball, streamed, not the skill
SKILL_MAX_BYTES = 5 * 1024 * 1024
OUTPUT_TAIL_CHARS = 2000
APP_MARKER = ".kollab-app.json"
MCP_MARKER = "added_by"  # servers.<id>.added_by == "apps": Apps wrote it, so Apps may remove it
KIND_BADGE = {"mcp": "MCP", "cli": "CLI", "skill": "Skill"}
KIND_TITLES = (("mcp", "MCP Servers"), ("cli", "CLI Tools"), ("skill", "Skills"))


def _result(ok: bool, message: str, mcp_changed: bool = False, skills_changed: bool = False) -> dict[str, Any]:
    return {"ok": ok, "message": message, "mcp_changed": mcp_changed, "skills_changed": skills_changed}


def _input_value(inp: dict[str, Any], values: dict[str, str]) -> str:
    """The value given for an input: "" when it was cleared, its default when none was given."""
    return (values[inp["name"]] or "") if inp["name"] in values else (inp.get("default") or "")


def required_unset(inputs: list[dict[str, Any]], values: dict[str, str]) -> list[dict[str, Any]]:
    """Required inputs left empty: no value given (or it was cleared) and no default."""
    return [inp for inp in inputs if inp.get("required") and not _input_value(inp, values)]


def _without(args: list[str], inputs: list[dict[str, Any]], skipped: set[str]) -> list[str]:
    """args minus what passes an input left empty: its docker -e pair, its mcp-remote --header, its optional argument.

    Docker would copy the variable from this computer's environment, and mcp-remote would send ${NAME} as text.
    """
    drops = [list(inp["drop"]) for inp in inputs if inp["name"] in skipped and inp.get("drop")]
    kept: list[str] = []
    i = 0
    while i < len(args):
        pair = args[i : i + 2]
        if len(pair) == 2 and (
            (pair[0] in ("-e", "--env") and pair[1] in skipped)
            or (pair[0] == "--header" and any("${" + name + "}" in pair[1] for name in skipped))
        ):
            i += 2
            continue
        drop = next((seq for seq in drops if args[i : i + len(seq)] == seq), None)
        if drop is not None:
            drops.remove(drop)
            i += len(drop)
            continue
        kept.append(args[i])
        i += 1
    return kept


def _library_badge(row: dict[str, Any]) -> str | None:
    """The Built-in or Local badge of a library skill with no catalog entry, else None."""
    return next((badge for badge in row["badges"] if badge in ("Built-in", "Local")), None)


def is_installed(row: dict[str, Any]) -> bool:
    """What "N installed" counts: anything added, except the skills that ship with Kollab."""
    return row["state"] == "added" and _library_badge(row) != "Built-in"


def _runtime_note(info: dict[str, Any] | None) -> str:
    if not info:
        return ""
    if info.get("error"):
        return f"error: {info['error']}"
    if info.get("connected"):
        tools = info.get("tool_count", 0)
        return f"connected · {tools} tool{'' if tools == 1 else 's'}"
    return ""


def _install_argv(method: dict[str, Any]) -> list[str]:
    manager, package = method["manager"], method["package"]
    if manager == "brew":
        return ["brew", "install", *(["--cask"] if method.get("cask") else []), package]
    if manager == "npm":
        return ["npm", "install", "-g", package]
    if manager == "uv":
        return ["uv", "tool", "install", package]
    return ["pipx", "install", package]


async def _run_argv(argv: list[str]) -> tuple[int, str]:
    """Run a catalog argv with no shell and stdin closed. Returns (exit code, output tail)."""
    proc = await asyncio.create_subprocess_exec(
        *argv,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    try:
        output, _ = await asyncio.wait_for(proc.communicate(), CLI_TIMEOUT_SECONDS)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        return 124, f"timed out after {CLI_TIMEOUT_SECONDS} s"
    return proc.returncode or 0, output.decode(errors="replace")[-OUTPUT_TAIL_CHARS:]


def _download(url: str) -> BinaryIO:
    """Stream a codeload tarball into a spool file (memory first, disk past 8 MB)."""
    if not url.startswith("https://codeload.github.com/"):
        raise ValueError("skill archives come from codeload.github.com only")
    spool = tempfile.SpooledTemporaryFile(max_size=8 * 1024 * 1024)
    try:
        with urllib.request.urlopen(url, timeout=60) as response:
            while chunk := response.read(1024 * 1024):
                if spool.tell() + len(chunk) > DOWNLOAD_MAX_BYTES:
                    raise ValueError("the skill's repo archive is larger than 300 MB")
                spool.write(chunk)
    except BaseException:
        spool.close()
        raise
    spool.seek(0)
    return spool  # type: ignore[return-value]


async def _fetch(url: str) -> BinaryIO:
    return await asyncio.to_thread(_download, url)


def _extract_skill(archive: bytes | BinaryIO, skill_path: str, folder: Path) -> None:
    """Write the regular files under skill_path from a codeload tarball into folder.

    One streaming pass, so a big repo never sits in memory. Only members under
    skill_path are checked and written: a link elsewhere in the repo is never
    extracted, so it does not block the skill. On an error the caller drops
    folder with whatever was written.
    """
    prefix = skill_path.strip("/") + "/"
    stream = io.BytesIO(archive) if isinstance(archive, bytes) else archive
    total = written = 0
    with stream, tarfile.open(fileobj=stream, mode="r|gz") as tar:
        for member in tar:
            top_and_rest = member.name.split("/", 1)
            if len(top_and_rest) != 2 or not top_and_rest[1].startswith(prefix):
                continue
            inner = top_and_rest[1][len(prefix) :]
            if member.isdir():
                continue
            # The write is folder / inner, so inner itself must stay relative ("a//b" makes it absolute).
            if not inner or PurePosixPath(inner).is_absolute() or ".." in PurePosixPath(inner).parts:
                raise ValueError(f"archive member {member.name!r} escapes its folder")
            if not member.isreg():
                raise ValueError(f"archive member {member.name!r} is a link or special file")
            total += member.size
            if total > SKILL_MAX_BYTES:
                raise ValueError("skill is larger than 5 MB")
            out = folder / inner
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_bytes(tar.extractfile(member).read())  # type: ignore[union-attr]
            written += 1
    if not written:
        raise ValueError(f"{skill_path} is not in the archive")


class AppsService:
    """Apps directory: row state, the Add / Remove / Turn On actions, and the installers.

    The constructor arguments are test seams; production callers pass nothing.
    """

    def __init__(
        self,
        catalog: dict[str, Any] | None = None,
        mcp_manager: MCPManager | None = None,
        home: Path | None = None,
        workspace: Path | None = None,
        which: Callable[[str], str | None] | None = None,
        run: Callable[[list[str]], Awaitable[tuple[int, str]]] | None = None,
        fetch: Callable[[str], Awaitable[bytes | BinaryIO]] | None = None,
        registry_get: Callable[[str], dict[str, Any]] | None = None,
    ) -> None:
        self.catalog = catalog if catalog is not None else load_catalog()
        self.mcp_manager = mcp_manager or MCPManager()
        self.home = Path(home) if home else Path.home()
        self.workspace = Path(workspace) if workspace else Path.cwd()
        self.which = which or shutil.which
        self.run = run or _run_argv
        self.fetch = fetch or _fetch
        self.registry_get = registry_get or registry.http_get
        self.skills_dir = self.home / ".kollab" / "skills"

    # --- rows and sections -------------------------------------------------

    def rows(self, mcp_runtime: dict[str, dict[str, Any]] | None = None) -> list[dict[str, Any]]:
        """Every catalog entry, MCP servers Add saved from the MCP Registry, then library skills with no entry.

        mcp_runtime maps server name to {"connected": bool, "tool_count": int, "error": str}
        when the caller knows the session's MCP state.
        """
        runtime = mcp_runtime or {}
        configured = (self.mcp_manager.load_config() or {}).get("servers", {})
        library = self._library()
        rows = [self._catalog_row(app, configured, runtime, library) for app in self.catalog["apps"]]
        catalog_ids = {f"{app['kind']}:{app['id']}" for app in self.catalog["apps"]}
        for key, server in configured.items():
            if isinstance(server, dict) and server.get("registry"):
                rows.append(self._registry_saved_row(key, server, runtime))
        for name, (skill, source) in library.items():
            if f"skill:{name}" not in catalog_ids:
                rows.append(self._library_row(skill, source))
        return rows

    def sections(self, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        by_id = {row["id"]: row for row in rows}
        for_you = [
            row_id
            for row_id in self.catalog["recommended"]
            if row_id in by_id and by_id[row_id]["state"] not in ("added", "unavailable")
        ][:4]
        featured = [row_id for row_id in self.catalog["featured"] if row_id in by_id]
        sections: list[dict[str, Any]] = []
        if for_you:
            sections.append({"id": "for-you", "title": "For You", "ids": for_you})
        if featured:
            sections.append({"id": "featured", "title": "Featured", "ids": featured})
        groups = [(kind, title, kind, None) for kind, title in KIND_TITLES]
        # Library skills get their own sections after the catalog's, so the
        # skills you can add are not buried under the ones that ship with Kollab.
        groups += [
            ("your-skills", "Your Skills", "skill", "Local"),
            ("built-in", "Built-in Skills", "skill", "Built-in"),
        ]
        for section_id, title, kind, badge in groups:
            group = sorted(
                (row for row in rows if row["kind"] == kind and _library_badge(row) == badge),
                key=lambda row: (row["state"] != "added", row["label"].casefold()),
            )
            if group:
                sections.append({"id": section_id, "title": title, "ids": [row["id"] for row in group]})
        return sections

    def missing_inputs(self, row_id: str, values: dict[str, str] | None = None) -> list[dict[str, Any]]:
        """Required MCP inputs with no value and no default. Empty for every other kind."""
        app = self._catalog_app(self._resolve(row_id))
        if app is None or app["kind"] != "mcp":
            return []
        return [dict(inp) for inp in required_unset(app["mcp"].get("inputs", []), values or {})]

    # --- the MCP Registry --------------------------------------------------

    async def registry_rows(self, query: str, mcp_runtime: dict[str, dict[str, Any]] | None = None) -> dict[str, Any]:
        """MCP Registry servers for a search, best first, minus the ones the catalog already lists.

        Never raises: a registry that does not answer is said in ``note``.
        """
        q = " ".join(query.split())
        if len(q) < 2:
            return {"query": q, "rows": [], "note": "Type two or more letters to search the MCP Registry."}
        try:
            servers, overflow, errors = await registry.search(q, self.registry_get)
        except (OSError, ValueError) as exc:
            return {
                "query": q,
                "rows": [],
                "note": f"Could not search the MCP Registry ({registry.describe_error(exc)}).",
            }
        known = registry.catalog_refs(self.catalog)
        configured = (self.mcp_manager.load_config() or {}).get("servers", {})
        # Saved ones are rows already (``rows``), so a search lists each server once.
        saved = {server.get("registry") for server in configured.values() if isinstance(server, dict)}
        hits = [
            server
            for server in registry.rank(servers, q)
            if server["name"] not in saved and not registry.server_refs(server) & known
        ]
        rows = [
            self._registry_row(registry.to_app(server, self._on_path), configured, mcp_runtime or {})
            for server in hits[: registry.SHOWN]
        ]
        rows.sort(key=lambda row: row["state"] == "unavailable")  # stable: rank order otherwise
        term = max(q.casefold().split(), key=len)
        if servers and not hits:
            note = "Every MCP Registry match is in the directory already."
        elif not hits and overflow:
            # Only the servers one search reads were checked against every word.
            note = f'None of the MCP Registry servers read match {q}. "{term}" matches too many: try a rarer word.'
        elif not hits:
            note = f"No MCP Registry servers match {q}."
        elif len(hits) > len(rows) or overflow:
            note = f"The top {len(rows)} of {len(hits)}{'+' if overflow else ''} matches. Add a word to narrow them."
        else:
            note = ""
        if errors:
            reason = registry.describe_error(errors[0])
            note = " ".join(filter(None, [f"Part of the search failed ({reason}), so this list may be short.", note]))
        return {"query": q, "rows": rows, "note": note}

    async def registry_app(self, name: str) -> tuple[dict[str, Any], dict[str, Any]]:
        """One registry server's entry and row, from its latest version. Raises OSError or ValueError."""
        server = await registry.fetch_server(name, self.registry_get)
        app = registry.to_app(server, self._on_path)
        row = self._registry_row(app, (self.mcp_manager.load_config() or {}).get("servers", {}), {})
        return {**app, "id": row["server"]}, row

    async def form_target(self, row_id: str) -> tuple[dict[str, Any], dict[str, Any] | None]:
        """A row and the entry behind it: catalog, MCP Registry, or None (a library skill)."""
        if row_id.startswith("registry:"):
            saved = next((row for row in self.rows() if row["id"] == row_id), None)
            if saved is not None:
                return saved, None  # saved already: Remove and Turn On need no registry call
            name = row_id.split(":", 1)[1]
            try:
                app, row = await self.registry_app(name)
            except (OSError, ValueError) as exc:
                raise ValueError(
                    f"Could not load {name} from the MCP Registry: {registry.describe_error(exc)}"
                ) from exc
            return row, app
        row = self._resolve(row_id)
        return row, self._catalog_app(row)

    # --- actions -----------------------------------------------------------

    async def add(
        self, row_id: str, values: dict[str, str] | None = None, confirm: str | None = None
    ) -> dict[str, Any]:
        """Add one app. A registry server also needs ``confirm``, the code its confirm step showed."""
        if row_id.startswith("registry:"):
            return await self._add_registry(row_id.split(":", 1)[1], values or {}, confirm)
        row = self._resolve(row_id)
        app = self._catalog_app(row)
        if row["state"] != "available" or app is None:
            reason = row["note"] if row["state"] == "unavailable" else f"{row['label']} is already {row['state']}"
            return _result(False, reason)
        if app["kind"] == "mcp":
            return self._add_mcp(app, values or {})
        if app["kind"] == "cli":
            return await self._add_cli(app)
        installed = []
        for tool in self._tools_first(app)[0]:
            result = await self._add_cli(tool)
            if not result["ok"]:
                return result
            installed.append(result["message"])
        result = await self._add_skill(app)
        result["message"] = " ".join([*installed, result["message"]])
        return result

    async def remove(self, row_id: str) -> dict[str, Any]:
        row = self._resolve(row_id)
        app_id = row.get("server") or row["id"].split(":", 1)[1]
        if row["kind"] == "mcp" and "remove" in row["actions"]:
            self.mcp_manager.remove_server(app_id)
            return _result(True, f"Removed {row['label']}.", mcp_changed=True)
        if row["kind"] == "skill" and "remove" in row["actions"]:
            shutil.rmtree(self.skills_dir / app_id)
            return _result(True, f"Removed {row['label']}.", skills_changed=True)
        if row["kind"] == "mcp" and row["state"] != "available":
            raise ValueError(f"{row['label']} was set up outside Apps, so Apps leaves it alone")
        raise ValueError(f"{row['label']} can't be removed from Apps")

    async def enable(self, row_id: str) -> dict[str, Any]:
        row = self._resolve(row_id)
        if "enable" not in row["actions"]:
            raise ValueError(f"{row['label']} is not off")
        app_id = row.get("server") or row["id"].split(":", 1)[1]
        app = self._catalog_app(row) or {}
        secrets = {inp["name"] for inp in app.get("mcp", {}).get("inputs", []) if inp.get("secret")}
        server = (self.mcp_manager.load_config() or {}).get("servers", {}).get(app_id, {})
        # Add saves a registry server's variables only when set or required, so an empty one is a required one.
        wanted = server.get("env", {}).keys() if server.get("registry") else secrets
        empty = sorted(name for name, value in server.get("env", {}).items() if name in wanted and not value)
        if empty:
            raise ValueError(f"Set {', '.join(empty)} with /mcp setup before turning on {row['label']}.")
        result = self.mcp_manager.enable_server(app_id)
        if not result.get("success"):
            raise ValueError(result.get("error", f"could not turn on {row['label']}"))
        return _result(True, f"Turned on {row['label']}.", mcp_changed=True)

    # --- row state ---------------------------------------------------------

    def _library(self) -> dict[str, tuple[Skill, str]]:
        """Skills by name across the three tiers. A higher tier wins a name clash."""
        tiers = [
            (get_bundled_skills_dir(), "bundled"),
            (self.skills_dir, "global"),
            (self.workspace / ".kollab" / "skills", "local"),
        ]
        found: dict[str, tuple[Skill, str]] = {}
        for tier_dir, source in tiers:
            if tier_dir is None or not tier_dir.is_dir():
                continue
            for entry in sorted(tier_dir.iterdir()):
                if entry.name.startswith(".") or not entry.is_dir():
                    continue
                skill = Skill.from_directory(entry, source=source)
                if skill is not None:
                    found[skill.name] = (skill, source)
        return found

    def _catalog_row(
        self,
        app: dict[str, Any],
        configured: dict[str, Any],
        runtime: dict[str, dict[str, Any]],
        library: dict[str, tuple[Skill, str]],
    ) -> dict[str, Any]:
        kind = app["kind"]
        if kind == "mcp":
            state, note, preview, actions = self._mcp_state(app, configured, runtime)
        elif kind == "cli":
            state, note, preview, actions = self._cli_state(app)
        else:
            state, note, preview, actions = self._skill_state(app, library)
        return {
            "id": f"{kind}:{app['id']}",
            "label": app["name"],
            "detail": app["description"],
            "group": kind,
            "kind": kind,
            "publisher": app["publisher"],
            "homepage": app["homepage"],
            "icon": app.get("icon"),
            "tags": app.get("tags", []),
            "state": state,
            "note": note,
            "badges": [KIND_BADGE[kind]],
            "preview": preview,
            "actions": actions,
        }

    @staticmethod
    def _library_row(skill: Skill, source: str) -> dict[str, Any]:
        return {
            "id": f"skill:{skill.name}",
            "label": skill.name,
            "detail": skill.description,
            "group": "skill",
            "kind": "skill",
            "publisher": "",
            "homepage": "",
            "icon": None,
            "tags": [],
            "state": "added",
            "note": "",
            "badges": ["Skill", "Built-in" if source == "bundled" else "Local"],
            "preview": "",
            "actions": [],
        }

    def _mcp_state(
        self, app: dict[str, Any], configured: dict[str, Any], runtime: dict[str, dict[str, Any]]
    ) -> tuple[str, str, str, list[str]]:
        mcp = app["mcp"]
        preview = shlex.join([mcp["command"], *mcp.get("args", [])])
        server = configured.get(app["id"])
        if isinstance(server, dict) and server.get("registry"):
            # Its own row lists it. This one would claim another server's command as the catalog's.
            return "unavailable", f"A server from the MCP Registry is saved as {app['id']}", preview, []
        if server is not None:
            note = _runtime_note(runtime.get(app["id"]))
            # A server set up by hand under a catalog id keeps its config: no Remove.
            remove = ["remove"] if server.get(MCP_MARKER) == "apps" else []
            if server.get("enabled", False):
                return "added", note, preview, remove
            return "off", note, preview, ["enable", *remove]
        missing = [exe for exe in app.get("requires", []) if not self.which(exe)]
        if missing:
            return "unavailable", f"Needs {', '.join(missing)} on PATH", preview, []
        return "available", "", preview, ["add"]

    def _cli_state(self, app: dict[str, Any]) -> tuple[str, str, str, list[str]]:
        cli = app["cli"]
        if self.which(cli["bin"]):
            return "added", "", "", []
        method = self._install_method(cli)
        if method is not None:
            return "available", "", shlex.join(_install_argv(method)), ["add"]
        managers = " or ".join(method["manager"] for method in cli["install"])
        return "unavailable", f"Needs {managers} on PATH", "", []

    def _skill_state(
        self, app: dict[str, Any], library: dict[str, tuple[Skill, str]]
    ) -> tuple[str, str, str, list[str]]:
        spec = app["skill"]
        if app["id"] not in library:
            preview = f"{spec['repo']}@{spec['ref']}:{spec['path']}"
            tools, unmet = self._tools_first(app)
            if unmet:  # a skill that drives a tool is no use until the tool is there
                return "unavailable", f"Needs {', '.join(unmet)} on PATH", preview, []
            if tools:  # Add installs the tool first, with the catalog's own install command
                first = [shlex.join(_install_argv(self._install_method(tool["cli"]))) for tool in tools]
                note = f"Installs {', '.join(tool['name'] for tool in tools)} first"
                return "available", note, "; ".join([*first, preview]), ["add"]
            return "available", "", preview, ["add"]
        marker = self.skills_dir / app["id"] / APP_MARKER
        return "added", "", "", ["remove"] if marker.exists() else []

    def _on_path(self, exe: str) -> bool:
        return bool(self.which(exe))

    def _registry_key(self, app: dict[str, Any], configured: dict[str, Any]) -> str | None:
        """The server name a registry entry has, or would get, in the MCP settings. None when every name is taken.

        Catalog ids stay reserved even when not added, so a later catalog Add never finds a registry server there.
        """
        candidates = registry.candidate_ids(app["registry"])
        for key in candidates:
            server = configured.get(key)
            if isinstance(server, dict) and server.get("registry") == app["registry"]:
                return key
        reserved = {entry["id"] for entry in self.catalog["apps"] if entry["kind"] == "mcp"}
        return next((key for key in candidates if key not in reserved and key not in configured), None)

    def _registry_saved_row(
        self, key: str, server: dict[str, Any], runtime: dict[str, dict[str, Any]]
    ) -> dict[str, Any]:
        """A registry server Add saved, from its settings entry: no registry call needed to list or remove it."""
        name = str(server["registry"])
        remove = ["remove"] if server.get(MCP_MARKER) == "apps" else []
        state, actions = ("added", remove) if server.get("enabled", False) else ("off", ["enable", *remove])
        return {
            "id": f"registry:{name}",
            "server": key,
            "label": str(server.get("title") or key),
            "detail": str(server.get("description") or ""),
            "group": "mcp",
            "kind": "mcp",
            "publisher": name.rpartition("/")[0],
            "homepage": str(server.get("homepage") or ""),
            "icon": None,
            "tags": [],
            "state": state,
            "note": _runtime_note(runtime.get(key)),
            "badges": ["Registry"],  # its section already says MCP; a phone-width row keeps room for the name
            "preview": shlex.join([str(server.get("command", "")), *map(str, server.get("args", []))]),
            "actions": actions,
        }

    def _registry_row(
        self, app: dict[str, Any], configured: dict[str, Any], runtime: dict[str, dict[str, Any]]
    ) -> dict[str, Any]:
        key = self._registry_key(app, configured)
        saved = configured.get(key) if key else None
        if isinstance(saved, dict) and saved.get("registry") == app["registry"]:
            return self._registry_saved_row(key, saved, runtime)  # type: ignore[arg-type]
        mcp = app["mcp"]
        missing = [exe for exe in app["requires"] if not self.which(exe)]
        if app["unsupported"]:
            state, note = "unavailable", app["unsupported"]
        elif key is None:
            state, note = "unavailable", f"Another MCP server already uses the name {app['id']}"
        elif missing:
            state, note = "unavailable", f"Needs {', '.join(missing)} on PATH"
        else:
            state, note = "available", registry.NOT_REVIEWED
        return {
            "id": f"registry:{app['registry']}",
            "server": key or app["id"],
            "label": app["name"],
            "detail": app["description"],
            "group": "mcp",
            "kind": "mcp",
            "publisher": app["publisher"],
            "homepage": app["homepage"],
            "icon": None,
            "tags": [],
            "state": state,
            "note": note,
            "badges": ["Registry"],  # its section already says MCP; a phone-width row keeps room for the name
            "preview": shlex.join([mcp["command"], *mcp["args"]]) if mcp["command"] else "",
            "actions": ["add"] if state == "available" else [],
        }

    # --- add paths ---------------------------------------------------------

    def _resolve(self, row_id: str) -> dict[str, Any]:
        rows = self.rows()
        if ":" in row_id:
            matches = [row for row in rows if row["id"] == row_id]
        else:
            matches = [row for row in rows if row["id"].split(":", 1)[1] == row_id]
        if not matches:
            raise ValueError(f"No app named {row_id!r}")
        if len(matches) > 1:
            raise ValueError(f"{row_id!r} matches more than one app; use kind:id")
        return matches[0]

    def _catalog_app(self, row: dict[str, Any]) -> dict[str, Any] | None:
        kind, app_id = row["kind"], row["id"].split(":", 1)[1]
        return next((app for app in self.catalog["apps"] if app["kind"] == kind and app["id"] == app_id), None)

    def _tools_first(self, app: dict[str, Any]) -> tuple[list[dict[str, Any]], list[str]]:
        """A skill's missing `requires`: catalog CLI tools Add can install first, and those none can."""
        tools: list[dict[str, Any]] = []
        unmet: list[str] = []
        for exe in app.get("requires", []):
            if self.which(exe):
                continue
            tool = next(
                (
                    entry
                    for entry in self.catalog["apps"]
                    if entry["kind"] == "cli" and entry["cli"]["bin"] == exe and self._install_method(entry["cli"])
                ),
                None,
            )
            if tool is None:
                unmet.append(exe)
            else:
                tools.append(tool)
        return tools, unmet

    def _install_method(self, cli: dict[str, Any]) -> dict[str, Any] | None:
        return next((method for method in cli["install"] if self.which(method["manager"])), None)

    def _add_mcp(
        self, app: dict[str, Any], values: dict[str, str], extra: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        mcp = app["mcp"]
        inputs = mcp.get("inputs", [])
        unset = required_unset(inputs, values)
        needed = [inp["label"] for inp in unset if not inp.get("secret")]
        if needed:
            return _result(False, f"Needs {', '.join(needed)}")

        builtins = {"workspace": str(self.workspace), "home": str(self.home)}
        env: dict[str, str] = {}
        arg_values: dict[str, str] = {}
        for inp in inputs:
            value = TOKEN_RE.sub(lambda m: builtins.get(m.group(1), m.group(0)), _input_value(inp, values))
            if inp.get("to", "env") == "env":
                if value or inp.get("required"):  # "" is not "unset" to every server
                    env[inp["name"]] = value
            else:
                arg_values[inp["name"]] = value
        skipped = {inp["name"] for inp in inputs if inp["name"] not in env and not arg_values.get(inp["name"])}
        args = [
            TOKEN_RE.sub(lambda m: arg_values.get(m.group(1), builtins.get(m.group(1), m.group(0))), arg)
            for arg in _without(mcp.get("args", []), inputs, skipped)
        ]

        entry = {
            "type": "stdio",
            "command": mcp["command"],
            "args": args,
            "env": env,
            "enabled": not unset,
            "description": app["description"],
            MCP_MARKER: "apps",
            **(extra or {}),
        }
        try:
            self.mcp_manager.upsert_server(app["id"], entry)
        except (ValueError, OSError) as exc:
            return _result(False, str(exc))
        if unset:
            names = ", ".join(inp["name"] for inp in unset)
            return _result(True, f"Added {app['name']} off. Set {names} with /mcp setup, then turn it on.", True)
        return _result(True, f"Added {app['name']}.", mcp_changed=True)

    async def _add_registry(self, name: str, values: dict[str, str], confirm: str | None) -> dict[str, Any]:
        try:
            app, row = await self.registry_app(name)
        except (OSError, ValueError) as exc:
            return _result(False, f"Could not load {name} from the MCP Registry: {registry.describe_error(exc)}")
        if row["state"] != "available":
            return _result(
                False, row["note"] if row["state"] == "unavailable" else f"{row['label']} is already {row['state']}"
            )
        # Save only what the confirm step showed: the registry can change in between.
        if not confirm:
            return _result(False, f"{row['label']} needs its confirm step: /apps add {row['id']} shows the command.")
        if confirm != registry.fingerprint(app):
            return _result(False, "This server changed in the MCP Registry. Check the command again, then Add.")
        extra = {"registry": name, "title": app["name"], "homepage": app["homepage"], "version": app["version"]}
        return self._add_mcp(app, values, extra)

    async def _add_cli(self, app: dict[str, Any]) -> dict[str, Any]:
        cli = app["cli"]
        method = self._install_method(cli)
        if method is None:
            return _result(False, f"No install method for {app['name']} is on PATH")
        argv = _install_argv(method)
        try:
            code, tail = await self.run(argv)
        except OSError as exc:
            return _result(False, f"Could not start {argv[0]}: {exc}")
        if code != 0:
            return _result(False, f"{app['name']} install failed (exit {code}):\n{tail}")
        if not self.which(cli["bin"]):
            return _result(False, f"Installed, but {cli['bin']} is not on PATH yet. Open a new shell.")
        message = f"Installed {app['name']}."
        if cli.get("post_install"):
            message += f" Next, run `{cli['post_install']}` in your terminal."
        return _result(True, message)

    async def _add_skill(self, app: dict[str, Any]) -> dict[str, Any]:
        spec = app["skill"]
        url = f"https://codeload.github.com/{spec['repo']}/tar.gz/{spec['ref']}"
        target = self.skills_dir / app["id"]
        try:
            archive = await self.fetch(url)
            self.skills_dir.mkdir(parents=True, exist_ok=True)
            staging = Path(tempfile.mkdtemp(dir=self.skills_dir, prefix=f".{app['id']}-"))
            try:
                folder = staging / app["id"]
                await asyncio.to_thread(_extract_skill, archive, spec["path"], folder)
                if Skill.from_directory(folder, source="global") is None:
                    raise ValueError(f"SKILL.md is missing or its name is not {app['id']}")
                marker = {
                    "id": app["id"],
                    "repo": spec["repo"],
                    "path": spec["path"],
                    "ref": spec["ref"],
                    "installed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                }
                (folder / APP_MARKER).write_text(json.dumps(marker, indent=2))
                os.replace(folder, target)
            finally:
                shutil.rmtree(staging, ignore_errors=True)
        except (OSError, ValueError, tarfile.TarError) as exc:
            return _result(False, f"Could not add {app['name']}: {exc}")
        return _result(True, f"Added {app['name']} to your skills.", skills_changed=True)
