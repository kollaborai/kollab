"""The official MCP Registry (registry.modelcontextprotocol.io) in Apps.

``search`` finds servers for a query, ``to_app`` turns one into the MCP entry
Add writes. Kollab has not reviewed registry servers, so their text is data:
control characters are stripped, only https links survive, a secret never lands
on a command line, and Add shows the exact command before anything runs.
"""

import asyncio
import hashlib
import http.client
import json
import logging
import re
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable
from urllib.parse import urlsplit

logger = logging.getLogger(__name__)

API = "https://registry.modelcontextprotocol.io/v0"
PAGE = 100  # the API's largest page
SHOWN = 20
# Measured 2026-10-08: one call at a time answers in 0.2-15 s, some stall past 40 s. More tries at once
# slow every call down, so a try with no answer after HEDGE_SECONDS gets one more beside it; the first
# answer wins.
CALL_SECONDS = 30  # one call, all its tries
HEDGE_SECONDS = 10.0
TRIES = 2
# A broad plain search ("microsoft") is slow by itself, and "microsoft/" and "/microsoft" carry the best
# matches. It gets one try and this long after them, then finishes into the cache.
PLAIN_GRACE_SECONDS = 2.0
CACHE_SECONDS = 600
CACHE_ENTRIES = 64
REPLY_MAX_BYTES = 8 * 1024 * 1024
STATUS_KEY = "io.modelcontextprotocol.registry/official"
RUNNERS = {"npm": "npx", "pypi": "uvx", "oci": "docker"}
GENERIC_NAMES = {"mcp", "server", "mcp-server", "api", "app"}
NOT_REVIEWED = "From the MCP Registry. Kollab has not reviewed it."
_ACRONYMS = {"ai", "api", "aws", "cli", "crm", "csv", "db", "dns", "gcp", "http", "io", "json", "llm", "mcp", "ml"}
_ACRONYMS |= {"pdf", "rag", "s3", "sdk", "seo", "sql", "ssh", "ui", "url", "xml"}
_BRANDS = {
    "github": "GitHub",
    "gitlab": "GitLab",
    "graphql": "GraphQL",
    "hubspot": "HubSpot",
    "linkedin": "LinkedIn",
    "mongodb": "MongoDB",
    "mysql": "MySQL",
    "openai": "OpenAI",
    "postgresql": "PostgreSQL",
    "youtube": "YouTube",
}

_TOKEN = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")
_NAME = re.compile(r"[A-Za-z0-9.-]+/[A-Za-z0-9._-]+")
_ENV_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_HEADER_NAME = re.compile(r"[A-Za-z0-9-]+")
_REF = re.compile(r"[A-Za-z0-9@/._:+-]+")  # package names and versions: no spaces or shell characters
_FLAG = re.compile(r"-{1,2}[A-Za-z0-9][A-Za-z0-9._-]*")
_ENV_REF = re.compile(r"\$\{([^}]*)\}")  # what mcp-remote fills in from its environment
# Variables npx, uvx and docker read themselves. Set by a registry entry, they would change what gets
# installed or run, out of sight of the command Add shows.
_RUNNER_ENV = re.compile(
    r"PATH|HOME|SHELL|ENV|BASH_ENV|TMPDIR|NODE_\w*|NPM_\w*|COREPACK_\w*|YARN_\w*|PNPM_\w*|UV_\w*|PIP_\w*"
    r"|PYTHON\w*|VIRTUAL_ENV|CONDA_\w*|DOCKER_\w*|LD_\w*|DYLD_\w*|XDG_\w*|SSL_CERT_\w*|REQUESTS_CA_BUNDLE"
    r"|CURL_CA_BUNDLE|(HTTPS?|ALL|NO|FTP)_PROXY|GIT_\w*",
    re.IGNORECASE,
)
# Runner flags a registry entry may pass, and whether each takes a value. Any other could change what
# gets installed (npx --registry, uvx --from) or reach past the container (docker -v, --privileged).
_RUNTIME_FLAGS: dict[str, dict[str, bool]] = {
    "npm": {"-y": False, "--yes": False},
    "pypi": {},
    "oci": {
        **dict.fromkeys(("-e", "--env", "-w", "--workdir", "--platform", "--pull"), True),
        **dict.fromkeys(("-i", "--interactive", "--rm", "--init", "-t", "--tty"), False),
    },
}

Get = Callable[[str], dict[str, Any]]
_cache: dict[str, tuple[float, dict[str, Any]]] = {}
_finishing: set[asyncio.Future[Any]] = set()  # plain searches that outlive their search: the loop keeps weak refs
# Its own threads: a stalled try holds one until CALL_SECONDS, never a thread of the daemon's shared pool.
_executor = ThreadPoolExecutor(max_workers=32, thread_name_prefix="mcp-registry")


class _Unsupported(Exception):
    """A server field Apps cannot turn into a safe command."""


def http_get(path: str) -> dict[str, Any]:
    """GET one registry API path as JSON. Raises OSError or ValueError."""
    request = urllib.request.Request(API + path, headers={"Accept": "application/json", "User-Agent": "kollab"})
    try:
        with urllib.request.urlopen(request, timeout=CALL_SECONDS) as response:
            body = response.read(REPLY_MAX_BYTES + 1)
    except http.client.HTTPException as exc:  # IncompleteRead and the like are not OSErrors
        raise ConnectionError(f"the reply broke off ({type(exc).__name__})") from exc
    if len(body) > REPLY_MAX_BYTES:
        raise ValueError("the reply is larger than 8 MB")
    data = json.loads(body)
    if not isinstance(data, dict):
        raise ValueError("the reply is not a JSON object")
    return data


def clear_cache() -> None:
    _cache.clear()


def describe_error(exc: BaseException) -> str:
    """A short reason for a failed registry call."""
    if isinstance(exc, urllib.error.HTTPError):
        return f"HTTP {exc.code}"
    if isinstance(exc, urllib.error.URLError):
        return str(exc.reason)
    if isinstance(exc, TimeoutError):
        return "timed out"
    return str(exc) or type(exc).__name__


async def _get_cached(path: str, get: Get, tries: int = TRIES) -> dict[str, Any]:
    """One API call through the cache.

    A try with no answer after HEDGE_SECONDS gets another beside it, and a failed
    one is replaced at once, up to ``tries``; the first answer wins. A 4xx is
    final. Raises the last failure, or TimeoutError after CALL_SECONDS.
    """
    hit = _cache.get(path)
    if hit and time.monotonic() - hit[0] < CACHE_SECONDS:
        return hit[1]
    loop = asyncio.get_running_loop()
    deadline = loop.time() + CALL_SECONDS
    running: set[asyncio.Future[dict[str, Any]]] = set()
    started = 0
    failure: Exception = TimeoutError(f"no answer in {CALL_SECONDS} s")
    try:
        while (left := deadline - loop.time()) > 0:
            if started < tries:
                running.add(loop.run_in_executor(_executor, get, path))
                started += 1
            elif not running:
                break
            wait = min(left, HEDGE_SECONDS) if started < tries else left
            done, running = await asyncio.wait(running, timeout=wait, return_when=asyncio.FIRST_COMPLETED)
            for future in done:
                exc = future.exception()
                if exc is None:
                    data = future.result()
                    _cache[path] = (time.monotonic(), data)
                    while len(_cache) > CACHE_ENTRIES:
                        _cache.pop(next(iter(_cache)))
                    return data
                if not isinstance(exc, (OSError, ValueError)) or (
                    isinstance(exc, urllib.error.HTTPError) and exc.code < 500
                ):
                    raise exc
                logger.debug("MCP Registry call %s: a try failed: %s", path, exc)
                failure = exc
    finally:
        for future in running:
            future.cancel()  # one already running finishes in its thread; its answer is dropped
    raise failure


def _status(item: dict[str, Any]) -> str:
    meta = item.get("_meta")
    official = meta.get(STATUS_KEY) if isinstance(meta, dict) else None
    status = official.get("status") if isinstance(official, dict) else None
    return status if isinstance(status, str) else "active"


def _haystack(server: dict[str, Any]) -> str:
    return " ".join(str(server.get(key) or "") for key in ("name", "title", "description")).casefold()


async def search(query: str, get: Get = http_get) -> tuple[list[dict[str, Any]], bool, list[Exception]]:
    """Active servers matching every word of query, whether the plain search had more pages,
    and the calls that failed or were cut short (then the list may be short).

    The registry matches a substring of the whole name, so "github" alone also
    hits every io.github.* namespace (26k servers). "github/" finds namespaces
    that end in the word (a vendor's own servers) and "/github" names that start
    with it, so those come back even when the plain search overflows its page.
    """
    words = query.casefold().split()
    term = max(words, key=len)
    paths = [
        f"/servers?limit={PAGE}&version=latest&search={urllib.parse.quote(text, safe='')}"
        for text in (term, f"{term}/", f"/{term}")
    ]
    plain = asyncio.ensure_future(_get_cached(paths[0], get, tries=1))
    _finishing.add(plain)
    plain.add_done_callback(_finishing.discard)
    plain.add_done_callback(lambda task: task.cancelled() or task.exception())  # a late failure is not "unretrieved"
    narrow = await asyncio.gather(*(_get_cached(path, get) for path in paths[1:]), return_exceptions=True)
    try:
        first: Any = await asyncio.wait_for(asyncio.shield(plain), PLAIN_GRACE_SECONDS)
    except asyncio.TimeoutError:
        first = TimeoutError("the plain search is still running")  # it keeps going and fills the cache
    except Exception as exc:  # noqa: BLE001 - the call's own failure, same as the narrow ones
        first = exc
    pages = [first, *narrow]
    servers: list[dict[str, Any]] = []
    seen: set[str] = set()
    errors: list[Exception] = []
    for page in pages:
        if isinstance(page, BaseException):
            if not isinstance(page, Exception):
                raise page
            logger.debug("MCP Registry search failed: %s", page)
            errors.append(page)
            continue
        for item in page.get("servers") or []:
            server = item.get("server") if isinstance(item, dict) else None
            if not isinstance(server, dict) or not isinstance(server.get("name"), str):
                continue
            if _status(item) != "active" or server["name"] in seen:
                continue
            seen.add(server["name"])
            servers.append(server)
    if len(errors) == len(paths):
        raise errors[0]
    first = pages[0]
    metadata = first.get("metadata") if isinstance(first, dict) else None
    overflow = isinstance(metadata, dict) and bool(metadata.get("nextCursor"))
    found = [server for server in servers if all(word in _haystack(server) for word in words)]
    return found, overflow, errors


def rank(servers: list[dict[str, Any]], query: str) -> list[dict[str, Any]]:
    """Best first: the vendor's own namespace, then the name, then a mention anywhere."""
    term = max(query.casefold().split(), key=len)

    def key(server: dict[str, Any]) -> tuple[int, bool, int, str]:
        name = server["name"].casefold()
        namespace, _, segment = name.rpartition("/")
        title = str(server.get("title") or "").casefold()
        if namespace.split(".")[-1] == term:
            tier = 0
        elif term in (segment, title):
            tier = 1
        elif segment.startswith(term) or title.startswith(term):
            tier = 2
        elif term in segment or term in title:
            tier = 3
        else:
            tier = 4
        repository = server.get("repository")
        has_repo = bool(_https(repository.get("url") if isinstance(repository, dict) else None))
        return tier, not has_repo, len(name), name

    return sorted(servers, key=key)


async def fetch_server(name: str, get: Get = http_get) -> dict[str, Any]:
    """The latest version of one server. Raises ValueError when it is missing or not active."""
    if not _NAME.fullmatch(name):
        raise ValueError(f"{name!r} is not an MCP Registry server name")
    data = await _get_cached(f"/servers/{urllib.parse.quote(name, safe='')}/versions/latest", get)
    server = data.get("server")
    if not isinstance(server, dict) or server.get("name") != name:
        raise ValueError(f"the MCP Registry has no server named {name}")
    status = _status(data)
    if status != "active":
        raise ValueError(f"{name} is {status} in the MCP Registry")
    return server


# --- one server as an Apps MCP entry ------------------------------------------


def _clean(value: Any, limit: int) -> str:
    """Registry text as one plain line. Control and format characters go: a terminal would run escape codes."""
    if not isinstance(value, str):
        return ""
    text = " ".join("".join(" " if unicodedata.category(ch)[0] == "C" else ch for ch in value).split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _https(url: Any) -> str:
    if not isinstance(url, str):
        return ""
    url = url.strip()
    try:
        parts = urlsplit(url)
    except ValueError:  # "https://[x]/"
        return ""
    return url if parts.scheme == "https" and parts.netloc and not any(ch.isspace() for ch in url) else ""


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.casefold()).strip("-")[:48].strip("-")


def candidate_ids(name: str) -> list[str]:
    """MCP server names Add may save a registry server under, best first.

    The part after the slash, unless it says nothing ("com.notion/mcp" saves as
    "notion-mcp"); then that name with the vendor appended, for a clash.
    """
    namespace, _, segment = name.rpartition("/")
    vendor = namespace.split(".")[-1]
    base = _slug(segment)
    if not base or base in GENERIC_NAMES:
        base = _slug(f"{vendor}-{segment}") or "server"
    ids = [base, _slug(f"{base}-{vendor}"), _slug(f"{base}-{namespace}")]
    return [key for index, key in enumerate(ids) if key and key not in ids[:index]]


def _title(slug: str) -> str:
    """A label for a server with no title: "notion-mcp-server" -> "Notion MCP Server"."""
    words = slug.split("-")
    return " ".join(_BRANDS.get(word) or (word.upper() if word in _ACRONYMS else word.capitalize()) for word in words)


def _input_name(hint: str, inputs: list[dict[str, Any]]) -> str:
    base = re.sub(r"[^A-Z0-9]+", "_", hint.upper()).strip("_") or "VALUE"
    if base[0].isdigit():
        base = f"V_{base}"
    taken = {inp["name"] for inp in inputs}
    name, count = base, 1
    while name in taken:
        count += 1
        name = f"{base}_{count}"
    return name


def _default(spec: dict[str, Any]) -> dict[str, str]:
    """A registry default, unless it is secret or holds braces: Add would fill {workspace} or {home} into it."""
    value = spec.get("default")
    usable = isinstance(value, str) and not spec.get("isSecret") and not re.search(r"[{}]", value)
    return {"default": value} if usable else {}


def _variables(
    text: str, variables: Any, inputs: list[dict[str, Any]], to: str, env_prefix: str = "", required: bool = False
) -> str:
    """Swap each declared {variable} in text for an input.

    to="arg" puts {TOKEN} back (Add fills it in, and a secret refuses: it would
    show in previews). to="env" puts ${NAME} for mcp-remote to read from env.
    Undeclared braces stay as text. ``required`` makes every variable required.
    """
    declared = variables if isinstance(variables, dict) else {}
    swapped: dict[str, str] = {}

    def swap(match: re.Match[str]) -> str:
        spec = declared.get(match.group(1))
        if not isinstance(spec, dict):
            return match.group(0)
        if match.group(1) not in swapped:
            if to == "arg" and spec.get("isSecret"):
                raise _Unsupported("Apps can't put a secret on a command line")
            name = _input_name(f"{env_prefix}{match.group(1)}", inputs)
            inputs.append(
                {
                    "name": name,
                    "label": _clean(match.group(1), 60),
                    "help": _clean(spec.get("description"), 200),
                    "to": to,
                    "secret": to == "env" and bool(spec.get("isSecret")),
                    "required": required or bool(spec.get("isRequired")),
                    **_default(spec),
                }
            )
            swapped[match.group(1)] = name
        return "${" + swapped[match.group(1)] + "}" if to == "env" else "{" + swapped[match.group(1)] + "}"

    return _TOKEN.sub(swap, text)


def _all_filled(text: str, inputs: list[dict[str, Any]], reason: str, builtins: bool = True) -> None:
    """Every {token} left in text is an input, or with ``builtins`` a token Add fills itself ({workspace}, {home})."""
    known = {inp["name"] for inp in inputs} | ({"workspace", "home"} if builtins else set())
    if any(token not in known for token in _TOKEN.findall(text)):
        raise _Unsupported(reason)


def _arguments(arguments: Any, inputs: list[dict[str, Any]]) -> list[str]:
    """argv for package arguments. An optional argument with no value is left out.

    A positional argument keeps its place, so its variables are required. An
    optional named one records itself as the input's ``drop``: left empty, Add
    leaves the whole argument out instead of passing "".
    """
    if arguments is None:
        return []
    if not isinstance(arguments, list):
        raise _Unsupported("Apps can't fill in this server's arguments")
    argv: list[str] = []
    for arg in arguments:
        kind = arg.get("type") if isinstance(arg, dict) else None
        name = arg.get("name") if isinstance(arg, dict) else None
        if kind not in ("named", "positional") or (
            kind == "named" and not (isinstance(name, str) and _FLAG.fullmatch(name))
        ):
            raise _Unsupported("Apps can't fill in this server's arguments")
        value = arg.get("value")
        created: list[dict[str, Any]] = []
        if isinstance(value, str):
            before = len(inputs)
            must = kind == "positional" or bool(arg.get("isRequired"))
            value = _variables(value, arg.get("variables"), inputs, "arg", required=must)
            _all_filled(value, inputs, "Apps can't fill in this server's arguments")
            created = inputs[before:]
        elif not arg.get("isRequired"):
            continue  # the server's own default applies
        elif arg.get("isSecret"):
            raise _Unsupported("Apps can't put a secret on a command line")
        else:
            hint = str(arg.get("valueHint") or name or "argument")
            token = _input_name(hint, inputs)
            inputs.append(
                {
                    "name": token,
                    "label": _clean(hint.lstrip("-"), 60),
                    "help": _clean(arg.get("description"), 200),
                    "to": "arg",
                    "required": True,
                    **_default(arg),
                }
            )
            value = "{" + token + "}"
        part = [value] if kind == "positional" else ([name, value] if value else [name])
        for inp in created:
            if not inp["required"]:
                inp["drop"] = part
        argv += part
    return argv


def _env_inputs(variables: Any) -> list[dict[str, Any]]:
    if variables is None:
        return []
    if not isinstance(variables, list):
        raise _Unsupported("Apps can't set this server's environment variables")
    inputs: list[dict[str, Any]] = []
    for var in variables:
        name = var.get("name") if isinstance(var, dict) else None
        if not (isinstance(name, str) and _ENV_NAME.fullmatch(name)):
            raise _Unsupported("Apps can't set this server's environment variables")
        if _RUNNER_ENV.fullmatch(name):
            raise _Unsupported(f"Apps won't set {name}: npx, uvx and docker read it themselves")
        if any(inp["name"] == name for inp in inputs):
            continue
        fixed = var.get("value") if isinstance(var.get("value"), str) and "{" not in var["value"] else None
        spec = {**var, "default": fixed} if fixed is not None else var
        inputs.append(
            {
                "name": name,
                "label": name,
                "help": _clean(var.get("description"), 200),
                "secret": bool(var.get("isSecret")),
                "required": bool(var.get("isRequired")),
                **_default(spec),
            }
        )
    return inputs


def _runtime(kind: str, arguments: Any, inputs: list[dict[str, Any]]) -> list[str]:
    """Flags for the runner itself: only _RUNTIME_FLAGS, and docker -e passes only this server's own variables."""
    if arguments is None:
        return []
    if not isinstance(arguments, list):
        raise _Unsupported("Apps can't fill in this server's arguments")
    argv: list[str] = []
    for arg in arguments:
        flag = arg.get("name") if isinstance(arg, dict) else None
        named = isinstance(arg, dict) and arg.get("type") == "named"
        takes_value = _RUNTIME_FLAGS[kind].get(flag) if named and isinstance(flag, str) else None
        if takes_value is None:
            raise _Unsupported(f"Apps won't pass {_clean(flag, 40) or 'this argument'} to {RUNNERS[kind]}")
        argv += _arguments([arg], inputs) if takes_value else [flag]
    own = {inp["name"] for inp in inputs if "to" not in inp}
    for flag, value in zip(argv, argv[1:]):
        if flag in ("-e", "--env") and "=" not in value and value not in own:
            # docker would copy it from this computer's environment into the container
            raise _Unsupported("Apps won't pass this computer's environment into a container")
    return argv


def _package(package: Any) -> dict[str, Any]:
    if not isinstance(package, dict):
        raise _Unsupported("Apps can't read this server's package")
    kind = package.get("registryType")
    runner = RUNNERS.get(kind) if isinstance(kind, str) else None
    if runner is None:
        raise _Unsupported(f"Apps can't run {kind if isinstance(kind, str) else 'these'} packages yet")
    transport = package.get("transport")
    if (transport.get("type") if isinstance(transport, dict) else "stdio") not in ("stdio", None):
        raise _Unsupported("Apps can't run a package that serves over HTTP yet")
    ident, version = package.get("identifier"), package.get("version")
    if not (isinstance(ident, str) and _REF.fullmatch(ident)) or not (
        version is None or (isinstance(version, str) and _REF.fullmatch(version))
    ):
        raise _Unsupported("Apps can't use this package name")
    inputs = _env_inputs(package.get("environmentVariables"))
    runtime = _runtime(kind, package.get("runtimeArguments"), inputs)
    package_args = _arguments(package.get("packageArguments"), inputs)
    pinned = version and version != "latest"
    if kind == "npm":
        args = [
            "-y",
            *(arg for arg in runtime if arg not in ("-y", "--yes")),
            f"{ident}@{version}" if pinned else ident,
        ]
    elif kind == "pypi":
        args = [*runtime, f"{ident}@{version}" if pinned else ident]  # uvx name@version
    else:
        tagged = ":" in ident.rsplit("/", 1)[-1] or "@" in ident
        passed = {runtime[i + 1] for i, arg in enumerate(runtime[:-1]) if arg in ("-e", "--env")}
        env_flags = [
            part for inp in inputs if "to" not in inp and inp["name"] not in passed for part in ("-e", inp["name"])
        ]
        image = ident if tagged or not pinned else f"{ident}:{version}"
        args = ["run", "-i", "--rm", *(arg for arg in runtime if arg not in ("run", "-i", "--rm")), *env_flags, image]
    return {"command": runner, "args": [*args, *package_args], "inputs": inputs}


def _remote(remote: Any) -> dict[str, Any]:
    """A hosted server, through mcp-remote (Kollab's MCP client speaks stdio)."""
    if not isinstance(remote, dict) or remote.get("type") not in ("streamable-http", "sse"):
        raise _Unsupported("Apps can't connect to this kind of remote server")
    inputs: list[dict[str, Any]] = []
    url = _variables(str(remote.get("url") or ""), remote.get("variables"), inputs, "arg", required=True)
    # Only this server's own inputs: {workspace} or {home} would send this computer's paths to the host.
    _all_filled(url, inputs, "Apps can't fill in part of this server's address", builtins=False)
    if "$" in url:
        raise _Unsupported("Apps can't fill in part of this server's address")
    if not _https(_TOKEN.sub("x", url)):
        raise _Unsupported("This server's address is not https")
    args = ["-y", "mcp-remote", url]
    headers = remote.get("headers") or []
    if not isinstance(headers, list):
        raise _Unsupported("Apps can't send this server's headers")
    for header in headers:
        name = header.get("name") if isinstance(header, dict) else None
        if not (isinstance(name, str) and _HEADER_NAME.fullmatch(name)):
            raise _Unsupported("Apps can't send this server's headers")
        value = header.get("value") if isinstance(header.get("value"), str) else None
        if value is not None and header.get("variables"):
            # "Bearer {token}": each variable becomes an env input, read by mcp-remote as ${NAME}.
            text = _variables(
                value,
                header["variables"],
                inputs,
                "env",
                env_prefix=f"MCP_HEADER_{name}_",
                required=bool(header.get("isRequired")),
            )
        elif value is not None and not header.get("isSecret"):
            text = _clean(value, 200)
        elif header.get("isRequired") or isinstance(header.get("default"), str):
            env = _input_name(f"MCP_HEADER_{name}", inputs)
            inputs.append(
                {
                    "name": env,
                    "label": name,
                    "help": _clean(header.get("description"), 200),
                    "secret": bool(header.get("isSecret")),
                    "required": bool(header.get("isRequired")),
                    **_default(header),
                }
            )
            text = "${" + env + "}"
        else:
            continue  # optional and empty: send nothing
        # mcp-remote fills ${NAME} from its environment, which holds this computer's secrets:
        # only this server's own env inputs (an address variable named HOME must not vouch
        # for ${HOME}), and no {workspace} or {home}.
        own_env = {inp["name"] for inp in inputs if inp.get("to", "env") == "env"}
        if any(ref not in own_env for ref in _ENV_REF.findall(text)):
            raise _Unsupported("Apps can't send this server's headers")
        _all_filled(text, inputs, "Apps can't send this server's headers", builtins=False)
        args += ["--header", f"{name}:{text}"]  # no space after the colon, as mcp-remote asks
    return {"command": "npx", "args": args, "inputs": inputs}


def to_app(server: dict[str, Any], on_path: Callable[[str], bool]) -> dict[str, Any]:
    """An Apps MCP entry for a registry server. ``unsupported`` says why Apps can't add it.

    A hosted server comes first: only mcp-remote runs locally then. A package
    runs the publisher's code on this machine. The first way whose runner is on
    PATH wins; if none is, the row says what to install.
    """
    name = server["name"]
    ways: list[dict[str, Any]] = []
    reasons: list[str] = []
    builders = [(_remote, remote) for remote in server.get("remotes") or []]
    builders += [(_package, package) for package in server.get("packages") or []]
    for build, spec in builders:
        try:
            ways.append(build(spec))
        except _Unsupported as exc:
            reasons.append(str(exc))
    way = next((way for way in ways if on_path(way["command"])), ways[0] if ways else None)
    repository = server.get("repository")
    homepage = _https(repository.get("url") if isinstance(repository, dict) else None) or _https(
        server.get("websiteUrl")
    )
    return {
        "id": candidate_ids(name)[0],
        "kind": "mcp",
        "registry": name,
        "name": _clean(server.get("title"), 60) or _title(candidate_ids(name)[0]),
        "description": _clean(server.get("description"), 160),
        "publisher": _clean(name.rpartition("/")[0], 80),
        "homepage": homepage,
        "version": _clean(server.get("version"), 40),
        "official": False,
        "requires": [way["command"]] if way else [],
        "mcp": way or {"command": "", "args": [], "inputs": []},
        "unsupported": "" if way else (reasons[0] if reasons else "This server lists nothing to install"),
    }


def fingerprint(app: dict[str, Any]) -> str:
    """A code for exactly what Add would save. The confirm step shows the command and carries the code;
    Add refuses when the server it loads again has a different one."""
    mcp = app["mcp"]
    blob = json.dumps([app["registry"], mcp["command"], mcp["args"], mcp["inputs"]], sort_keys=True)
    return hashlib.sha256(blob.encode()).hexdigest()[:16]


# --- servers the curated catalog already has -----------------------------------


def _ref_key(text: Any) -> str:
    """A package or URL without its version, so a registry entry can match a catalog arg."""
    if not isinstance(text, str) or not text.strip() or text.startswith("-"):
        return ""
    text = text.strip().casefold()
    if text.startswith(("https://", "http://")):
        try:
            parts = urlsplit(text)
        except ValueError:  # "https://[x]/"
            return ""
        return f"{parts.netloc}{parts.path.rstrip('/')}"
    if text.startswith("@"):
        text = "@" + text[1:].split("@", 1)[0]
    else:
        text = text.split("@", 1)[0]
    text = text.split("==", 1)[0]
    head, _, last = text.rpartition("/")
    return f"{head}/{last.split(':', 1)[0]}" if head else last.split(":", 1)[0]


def catalog_refs(catalog: dict[str, Any]) -> set[str]:
    refs = {
        _ref_key(arg)
        for app in catalog.get("apps", [])
        if app.get("kind") == "mcp"
        for arg in app.get("mcp", {}).get("args", [])
    }
    refs.discard("")
    return refs


def server_refs(server: dict[str, Any]) -> set[str]:
    refs = {_ref_key(remote.get("url")) for remote in server.get("remotes") or [] if isinstance(remote, dict)}
    refs |= {
        _ref_key(package.get("identifier")) for package in server.get("packages") or [] if isinstance(package, dict)
    }
    refs.discard("")
    return refs
