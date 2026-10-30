# Windows platform helpers (issue #268, port items 2, 4 and 5)

Status: v2, after a security, SRE and platform review. Wave 2 builds these helpers and their tests only. No call site
changes until wave 3.

The agent runtime calls POSIX-only APIs in about 300 places: owner and mode checks, `O_NOFOLLOW` and `dir_fd`
opens, `os.getuid`, signals, process groups and AF_UNIX sockets. Three helpers keep today's macOS and Linux behavior
and add a Windows branch, so call sites can move to them file by file.

## Scope
- In:
  - the CLI, the detached daemon, the hub and kill switch, the engine and web UI, and voice;
  - the client side of the agent network: `config_sync.py`, `relay_owner.py`, `relay_state.py` and
    `relay_conversations.py`.
- Out: the relay server (`relay_service.py`, `relay_runtime.py`, `relay_selfhost.py`, used only by
  `kollab relay serve`). It stays Linux and macOS. On Windows, `kollab relay serve` will exit with one line saying so.

## Threat model
- Unchanged: another local user, or a process running as one, must not:
  - read kollab state;
  - plant, swap or delete files in it;
  - connect to an agent endpoint;
  - impersonate an agent server.
- Trusted principals:
  - POSIX: our own uid.
  - Windows: our own user SID, SYSTEM (S-1-5-18) and Administrators (S-1-5-32-544). Those are the principals that
    every Windows profile folder already trusts.
  - Windows ACEs for OWNER RIGHTS (S-1-3-4) and CREATOR OWNER (S-1-3-0) also count as trusted. OWNER RIGHTS grants
    only to the owner, which must itself be trusted, and `os.mkdir(path, 0o700)` writes that ACE. CREATOR
    OWNER matches no token.
- One stricter rule stays as it is today: the kill switch's halt guard tells an agent's tool shell from the person's
  own CLI by the caller's process ancestry. Both run as the same user, so the caller's pid must come from the kernel.
  A pid the caller states about itself is never trusted.

## Placement
- Canonical modules: `packages/kollabor-config/src/kollabor_config/private_fs.py`, `proc_ctl.py` and
  `local_ipc.py`.
- They import only the standard library. ctypes is used on Windows only, behind `os.name == "nt"`.
- `local_ipc` imports `private_fs` and `proc_ctl` lazily, inside its Windows branches.
- Wave 3 adds byte-identical copies under `plugins/hub` for the kill switch and the relay-shipped hub files, the same
  way `file_lock.py` was done (#423), with the same parity test.
- Wave 3 also gives `kollabor-rpc` and `kollabor-voice` a dependency on `kollabor-config`, once it is checked that
  this creates no cycle.
- ctypes rules:
  - every function gets `argtypes` and `restype` from `ctypes.wintypes` (HANDLE, BOOL, DWORD and so on);
  - libraries load with `ctypes.WinDLL(name, use_last_error=True)`;
  - errors are read with `ctypes.get_last_error()` right after the call;
  - every handle and every `LocalFree`'d buffer is released in a `finally`.

## Helper 1: `private_fs`
- `current_user_id() -> str`
  - POSIX: `str(os.getuid())`.
  - Windows: the process token's user SID as a string (`OpenProcessToken`, `GetTokenInformation(TokenUser)`,
    `ConvertSidToStringSidW`).
  - Cached after the first call.
- `user_tag() -> str`, for path names.
  - POSIX: `str(os.getuid())`.
  - Windows: the first 12 hex characters of `sha256(current_user_id())`.
- `owned_private(path, st=None, *, mask=0o077) -> bool`
  - POSIX: with `st = st or os.lstat(path)`, the result is
    `st.st_uid == os.getuid() and not (stat.S_IMODE(st.st_mode) & mask)`.
  - Windows: read the owner and DACL (`GetNamedSecurityInfoW`). The result is True only if all of these hold:
    - The owner SID is trusted.
    - The DACL is not NULL. A NULL DACL means everyone has full access.
    - Every ACE passes:
      - an ACE with INHERIT_ONLY_ACE is skipped;
      - an ACCESS_DENIED ACE is skipped;
      - an ACCESS_ALLOWED ACE whose SID is untrusted fails when its mask hits a forbidden bit:
        - for `mask=0o077`: any bit at all;
        - for `mask=0o022`: FILE_WRITE_DATA (also FILE_ADD_FILE), FILE_APPEND_DATA (also FILE_ADD_SUBDIRECTORY),
          FILE_WRITE_EA, FILE_DELETE_CHILD, FILE_WRITE_ATTRIBUTES, DELETE, WRITE_DAC, WRITE_OWNER, GENERIC_ALL or
          GENERIC_WRITE;
      - any other ACE type fails (fail closed).
- `check_private_dir(path)` raises `PermissionError` unless the folder is safe.
  - POSIX: lstat; it must be a directory and `owned_private(mask=0o077)`.
  - Windows:
    1. The path must be inside the user's profile folder (`%USERPROFILE%`). Outside it, the check refuses.
       - Both paths get 8.3 short names expanded with `GetLongPathNameW` (CI runners use `RUNNER~1`).
       - `realpath` is never used, because it would resolve the junctions the walk must see.
    2. Every component from the profile folder down to `path` must not be a reparse point
       (`lstat().st_file_attributes & 0x400`) and must pass `owned_private(mask=0o022)`.
    3. `path` itself must pass `owned_private(mask=0o077)`.
- `ensure_private_dir(path)`
  - POSIX: `os.makedirs(path, 0o700, exist_ok=True)`. If the folder is ours, it is tightened to 0o700. Then
    `check_private_dir(path)`.
  - Windows: each missing component is created with `os.mkdir(component, 0o700)`, which since Python 3.13 sets an
    owner, SYSTEM and Administrators ACL. Then `check_private_dir(path)`. A test on windows-latest proves the ACL
    claim.
- `open_nofollow(path, flags, mode=0o600, *, dir_fd=None) -> int`
  - POSIX: `os.open(path, flags | os.O_NOFOLLOW, mode, dir_fd=dir_fd)`.
  - Windows:
    1. `dir_fd` must be None, else ValueError. Like `O_NOFOLLOW`, only the last component is checked; parent
       folders are covered by `check_private_dir`.
    2. If the path exists, lstat it and refuse a reparse point.
    3. `fd = os.open(path, flags | os.O_BINARY | os.O_NOINHERIT, mode)`.
    4. fstat the fd. Close it and raise `OSError(errno.ELOOP)` when any of these hold:
       - `st_ino` is 0 on either side;
       - `(st_dev, st_ino)` differ from the lstat result;
       - the fstat shows the reparse bit.
- Folder-pinned (`dir_fd`) sites get a Windows path branch in wave 3: `check_private_dir` on the folder, then
  path-based opens inside it. That is safe because no other principal can write in a checked folder or above it, up
  to the profile.
- Permission-setting sites (chmod, fchmod, umask) stay as they are. On Windows they only flip the read-only flag;
  privacy comes from the folder ACL.

## Helper 2: `proc_ctl`
- `pid_alive(pid) -> bool`
  - POSIX: `os.kill(pid, 0)`. ProcessLookupError means False; PermissionError means True.
  - Windows: `OpenProcess(SYNCHRONIZE | PROCESS_QUERY_LIMITED_INFORMATION)`, then `WaitForSingleObject(h, 0)`.
    WAIT_TIMEOUT means True and WAIT_OBJECT_0 means False.
    - If OpenProcess fails: ERROR_ACCESS_DENIED means True; anything else means False.
  - It never calls `os.kill` on Windows, where signal 0 is CTRL_C_EVENT.
  - Call sites include the halt guard's probe at `halt_runtime.py:189`. Its False maps to the guard's refusal, as
    ProcessLookupError does today.
- `process_created(pid) -> int | None`: the creation time (`GetProcessTimes`, as a FILETIME integer) on Windows;
  None when unknown and on POSIX.
- `start_child(argv, *, detached=False, **popen_kwargs) -> subprocess.Popen`
  - POSIX: `subprocess.Popen(argv, start_new_session=True, **popen_kwargs)`.
  - Windows: the creation flags are CREATE_NEW_PROCESS_GROUP, plus DETACHED_PROCESS and CREATE_BREAKAWAY_FROM_JOB
    when `detached`.
    - If CreateProcess fails with winerror 5 while breakaway is set, it retries once without breakaway.
    - A non-detached child is assigned to this process's job (`ChildJob`). A detached daemon is not: it must outlive
      us, just as a setsid'd daemon does today.
- `ChildJob`: one Job Object per process, created lazily with JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE, so Windows ends our
  children when this process ends.
  - `adopt(popen) -> bool` returns False and logs a warning when AssignProcessToJobObject fails, for example when a
    runner's job forbids nesting.
  - It is a no-op on POSIX.
- `terminate(pid)` and `kill(pid)`
  - POSIX: SIGTERM and SIGKILL.
  - Windows: `TerminateProcess` with exit code 1, raising ProcessLookupError when the pid does not exist, so that
    callers' except branches keep working.
- `kill_tree(pid, sig, *, protect=frozenset(), created=None)`
  - POSIX: the halt closure from `plugins/hub/halt_runtime.py:926-950`, moved verbatim. It runs `getpgid`, and runs
    `killpg` only when the group is the pid's own, larger than 1, not ours and holds no protected pid. Otherwise it
    sends `kill(pid, sig)`.
  - Windows:
    1. When `created` is given and differs from `process_created(pid)`, it returns without killing (pid reuse).
    2. SIGTERM: Windows has no signal, so the caller should already have asked over IPC. It waits up to 5 s for
       `pid_alive` to turn False, then escalates to the hard path.
    3. SIGKILL (hard):
       - Take a Toolhelp snapshot and build the tree from `pid` by parent pid.
       - Walk a child only if its creation time is not earlier than its parent's. An earlier child means the parent
         pid was reused.
       - Skip `protect` and our own pid.
       - TerminateProcess the leaves first.
- `on_stop_signal(loop, callback) -> undo`
  - POSIX: `loop.add_signal_handler` for SIGTERM and SIGINT.
  - Windows: `signal.signal` for SIGINT and SIGBREAK. Each handler calls `loop.call_soon_threadsafe(callback)`.
  - The Proactor loop wakes on signals through its self-pipe (Python 3.8+), so it needs no heartbeat. A
    windows-latest test proves this, and is skipped when the runner has no console.
  - `undo()` restores the previous handlers.
- `process_table() -> dict[int, tuple[int, int, str]]`: Windows only, mapping pid to (ppid, created, exe name) from
  a Toolhelp snapshot. It raises NotImplementedError on POSIX.
- The kill switch's caller chain (`network_halt.py:1335-1352`, `:1398`, `:1439`) reads ppid, pgid and sid through
  `ps`. Its Windows design (ancestry from `process_table`, with the creation-time rule) is wave 3 group G1. It is not
  part of this helper.

## Helper 3: `local_ipc`
On Windows asyncio has no unix sockets, and a named pipe with a NULL security descriptor lets Everyone read. So on
Windows the transport is loopback TCP with a mutual, direction-labelled HMAC challenge. The key sits in an endpoint
file inside a checked private folder.

- Endpoint: the same path each site uses for its unix socket today.
  - POSIX: the socket.
  - Windows: a JSON file `{"port": N, "key": "<64 hex>", "pid": P}`.
- `async serve(handler, endpoint, **kw) -> Endpoint`
  - POSIX: `asyncio.start_unix_server(handler, path=str(endpoint), **kw)`. Sites keep their own umask and chmod
    calls.
  - Windows:
    1. `ensure_private_dir(endpoint.parent)`.
    2. Generate a fresh 32-byte key (`secrets.token_bytes`).
    3. `asyncio.start_server(wrapped, "127.0.0.1", 0, reuse_address=False, **kw)`.
    4. Write the file to a temporary name in the same folder (O_CREAT, O_EXCL, O_NOINHERIT), then `os.replace` it
       onto the endpoint.
  - `wrapped` runs the server side of the handshake inside `asyncio.wait_for(..., 5)` before it calls the handler.
  - `Endpoint.close()` closes the server and removes the file only if it still holds this server's port and pid.
- Handshake, every message a fixed 32 bytes, read with `readexactly`:
  1. The server sends a random N_s.
  2. The client sends a random N_c, then `HMAC-SHA256(key, b"kollab-ipc/client" + N_s + N_c)`.
  3. The server checks it with `hmac.compare_digest`, closing on failure, then sends
     `HMAC-SHA256(key, b"kollab-ipc/server" + N_c + N_s)`.
  4. The client checks it with `hmac.compare_digest` and raises ConnectionRefusedError on failure.
  - The two directions use different labels, so a peer cannot reflect one side's proof back as the other's.
- `async connect(endpoint, **kw) -> (reader, writer)`
  - POSIX: `asyncio.open_unix_connection(str(endpoint), **kw)`.
  - Windows:
    1. `check_private_dir(endpoint.parent)`.
    2. `owned_private(endpoint, mask=0o077)`. If it fails, raise ConnectionRefusedError.
    3. Read the file. A missing file raises FileNotFoundError, as a missing socket does today. A file whose pid is
       not `pid_alive` raises ConnectionRefusedError, as a stale socket does today.
    4. `asyncio.open_connection("127.0.0.1", port, **kw)`, then the client side of the handshake within 5 s.
- `connect_blocking(endpoint, timeout) -> socket.socket`: the same, for blocking clients.
- `peer_pid(writer_or_sock) -> int | None`
  - POSIX: today's SO_PEERCRED (Linux) and LOCAL_PEERPID (macOS) reads, from `halt_runtime.py:219-228`.
  - Windows: the kernel's owner of the client's TCP connection. `GetExtendedTcpTable(AF_INET,
    TCP_TABLE_OWNER_PID_CONNECTIONS)` gives the row whose local port is the client's port and whose remote port is
    ours, and its `dwOwningPid` is the answer.
  - It never uses a pid the client states.
- `peer_is_me(writer_or_sock) -> bool`
  - POSIX: today's peer uid check.
  - Windows: True for every connection that reached a handler, because passing the handshake proves the peer read
    the key, and only trusted principals can read the key.

## Proof
- On windows-latest, under CPython 3.14 and without admin rights, `windows-locks.yml` runs:
  - **private_fs**:
    - `ensure_private_dir` under `%TEMP%` gives a folder whose `icacls` output has no Users, Authenticated Users or
      Everyone ACE;
    - after `icacls <dir> /grant *S-1-5-32-545:(W)`, `owned_private` is False;
    - after `mklink /J`, `check_private_dir` and `open_nofollow` refuse the junction;
    - no test uses `os.symlink`.
  - **proc_ctl**:
    - `pid_alive` is True for a live child, False after it is killed, and True for pid 4 (the access-denied path);
    - `pid_alive` never calls `os.kill` (patched to raise);
    - the hard `kill_tree` ends a grandchild;
    - the Job test is skipped when the runner forbids nested jobs.
  - **local_ipc**:
    - a round trip works;
    - a wrong key is refused;
    - a reflection attempt (sending N_s back as N_c) is refused;
    - a stale pid gives ConnectionRefusedError;
    - `peer_pid` equals a child process's pid.
- On macOS and Linux CI, the POSIX branches are tested with the same behaviors.

## Rollout
1. Wave 2: the three helpers, their tests, this spec, and the `windows-locks.yml` steps.
2. Wave 3: the call sites move by file group, G1 to G6.
   - G1 (the kill switch) also designs the Windows caller chain, and reruns the Docker harness on POSIX.
