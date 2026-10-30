"""Owner-only folders and files that work on POSIX and Windows (kollab #268).

POSIX checks the owner uid and the mode bits. Windows checks the owner SID and the DACL, and refuses
reparse points (junctions and symlinks) on the way down from the user's profile folder.
"""

import errno
import functools
import hashlib
import os
import stat
from typing import Any

if os.name == "nt":
    import ctypes
    from ctypes import wintypes

    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)

    SE_FILE_OBJECT = 1
    OWNER_SECURITY_INFORMATION = 0x1
    DACL_SECURITY_INFORMATION = 0x4
    ACCESS_ALLOWED_ACE_TYPE = 0
    ACCESS_DENIED_ACE_TYPE = 1
    INHERIT_ONLY_ACE = 0x08
    TOKEN_QUERY = 0x0008
    TOKEN_USER = 1
    FILE_ATTRIBUTE_REPARSE_POINT = 0x400
    # FILE_WRITE_DATA, FILE_APPEND_DATA, FILE_WRITE_EA, FILE_DELETE_CHILD, FILE_WRITE_ATTRIBUTES, DELETE,
    # WRITE_DAC, WRITE_OWNER, GENERIC_ALL and GENERIC_WRITE.
    WRITE_BITS = (
        0x2
        | 0x4
        | 0x10
        | 0x40
        | 0x100
        | 0x10000
        | 0x40000
        | 0x80000
        | 0x10000000
        | 0x40000000
    )
    # SYSTEM and Administrators, as in every profile folder. The user's own SID is added at run time.
    # SYSTEM, Administrators, OWNER RIGHTS and CREATOR OWNER. OWNER RIGHTS grants only to the owner, which must
    # itself be trusted; os.mkdir(path, 0o700) writes such an ACE. CREATOR OWNER matches no token.
    TRUSTED_FIXED = ("S-1-5-18", "S-1-5-32-544", "S-1-3-4", "S-1-3-0")

    class _ACL(ctypes.Structure):
        _fields_ = [
            ("AclRevision", ctypes.c_ubyte),
            ("Sbz1", ctypes.c_ubyte),
            ("AclSize", wintypes.WORD),
            ("AceCount", wintypes.WORD),
            ("Sbz2", wintypes.WORD),
        ]

    class _ACE_HEADER(ctypes.Structure):
        _fields_ = [
            ("AceType", ctypes.c_ubyte),
            ("AceFlags", ctypes.c_ubyte),
            ("AceSize", wintypes.WORD),
        ]

    class _ACCESS_ALLOWED_ACE(
        ctypes.Structure
    ):  # the SID starts at offset 8 (SidStart)
        _fields_ = [
            ("Header", _ACE_HEADER),
            ("Mask", wintypes.DWORD),
            ("SidStart", wintypes.DWORD),
        ]

    class _SID_AND_ATTRIBUTES(
        ctypes.Structure
    ):  # TOKEN_USER is one of these at offset 0
        _fields_ = [("Sid", ctypes.c_void_p), ("Attributes", wintypes.DWORD)]

    P = ctypes.POINTER
    _kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    _kernel32.CloseHandle.restype = wintypes.BOOL
    _kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    _kernel32.LocalFree.restype = ctypes.c_void_p
    _kernel32.GetLongPathNameW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.LPWSTR,
        wintypes.DWORD,
    ]
    _kernel32.GetLongPathNameW.restype = wintypes.DWORD
    _kernel32.GetCurrentProcess.argtypes = []
    _kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    _advapi32.GetNamedSecurityInfoW.argtypes = [
        wintypes.LPCWSTR,
        ctypes.c_int,
        wintypes.DWORD,
        P(ctypes.c_void_p),
        P(ctypes.c_void_p),
        P(ctypes.c_void_p),
        P(ctypes.c_void_p),
        P(ctypes.c_void_p),
    ]
    _advapi32.GetNamedSecurityInfoW.restype = wintypes.DWORD
    _advapi32.GetAce.argtypes = [ctypes.c_void_p, wintypes.DWORD, P(ctypes.c_void_p)]
    _advapi32.GetAce.restype = wintypes.BOOL
    _advapi32.ConvertSidToStringSidW.argtypes = [ctypes.c_void_p, P(wintypes.LPWSTR)]
    _advapi32.ConvertSidToStringSidW.restype = wintypes.BOOL
    _advapi32.OpenProcessToken.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        P(wintypes.HANDLE),
    ]
    _advapi32.OpenProcessToken.restype = wintypes.BOOL
    _advapi32.GetTokenInformation.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        ctypes.c_void_p,
        wintypes.DWORD,
        P(wintypes.DWORD),
    ]
    _advapi32.GetTokenInformation.restype = wintypes.BOOL


@functools.cache
def current_user_id() -> str:
    """The caller's uid on POSIX, or the user SID on Windows. Cached after the first call."""
    if os.name != "nt":
        return str(os.getuid())
    return _token_user_sid()


def user_tag() -> str:
    """A name-safe tag for the current user: the uid on POSIX, 12 hex characters of a hash on Windows."""
    if os.name != "nt":
        return str(os.getuid())
    return hashlib.sha256(current_user_id().encode()).hexdigest()[:12]


def owned_private(
    path: str | os.PathLike[str], st: os.stat_result | None = None, *, mask: int = 0o077
) -> bool:
    """True when ``path`` is ours and no other principal can reach it through ``mask``.

    On Windows ``mask`` may only be 0o077 (no access for anyone else) or 0o022 (no write access for anyone else).
    """
    if os.name != "nt":
        st = st or os.lstat(path)
        return st.st_uid == os.getuid() and not (stat.S_IMODE(st.st_mode) & mask)
    if mask not in (0o077, 0o022):
        raise ValueError(
            f"owned_private mask must be 0o077 or 0o022 on Windows, not {mask:#o}"
        )
    return _windows_owned_private(str(path), mask)


def check_private_dir(path: str | os.PathLike[str]) -> None:
    """Raise PermissionError unless ``path`` is a folder that only we can write to."""
    if os.name != "nt":
        st = os.lstat(path)
        if not stat.S_ISDIR(st.st_mode) or not owned_private(path, st):
            raise PermissionError(f"{path} is not a private folder of ours")
        return
    root = _profile_root()
    full = _long_path(os.path.abspath(path))
    norm_full, norm_root = os.path.normcase(full), os.path.normcase(root)
    if norm_full != norm_root and not norm_full.startswith(norm_root + os.sep):
        raise PermissionError(f"{path} is outside the user profile folder")
    # Every folder from the profile folder down to ``path`` must be a real folder that no one else can write to.
    steps = [root]
    rel = os.path.relpath(full, root)
    if rel != os.curdir:
        for part in rel.split(os.sep):
            steps.append(os.path.join(steps[-1], part))
    for step in steps:
        if os.lstat(
            step
        ).st_file_attributes & FILE_ATTRIBUTE_REPARSE_POINT or not owned_private(
            step, mask=0o022
        ):
            raise PermissionError(
                f"{step} is a reparse point or can be written by others"
            )
    if not owned_private(full, mask=0o077):
        raise PermissionError(f"{path} is not a private folder of ours")


def ensure_private_dir(path: str | os.PathLike[str]) -> None:
    """Create ``path`` and any missing parents as private folders, then check it with check_private_dir."""
    if os.name != "nt":
        os.makedirs(path, 0o700, exist_ok=True)
        st = os.lstat(path)
        if stat.S_ISDIR(st.st_mode) and st.st_uid == os.getuid():
            os.chmod(path, 0o700)
    else:
        # os.mkdir with mode 0o700 gives the new folder an owner, SYSTEM and Administrators ACL (Python 3.13+).
        missing = []
        current = os.path.abspath(path)
        while not os.path.lexists(current):
            missing.append(current)
            parent = os.path.dirname(current)
            if parent == current:
                break
            current = parent
        for folder in reversed(missing):
            try:
                os.mkdir(folder, 0o700)
            except (
                FileExistsError
            ):  # made by someone else meanwhile; check_private_dir decides
                pass
    check_private_dir(path)


def open_nofollow(
    path: str | os.PathLike[str],
    flags: int,
    mode: int = 0o600,
    *,
    dir_fd: int | None = None,
) -> int:
    """os.open that refuses a symlink or junction at ``path``. Returns the file descriptor."""
    if os.name != "nt":
        return os.open(path, flags | os.O_NOFOLLOW, mode, dir_fd=dir_fd)
    if dir_fd is not None:
        raise ValueError(
            "dir_fd is not supported on Windows; check the folder with check_private_dir first"
        )
    before = os.lstat(path) if os.path.lexists(path) else None
    if before is not None and before.st_file_attributes & FILE_ATTRIBUTE_REPARSE_POINT:
        raise OSError(errno.ELOOP, "refusing to follow a reparse point", str(path))
    fd = os.open(path, flags | os.O_BINARY | os.O_NOINHERIT, mode)
    try:
        after = os.fstat(fd)
        if (
            after.st_ino == 0
            or after.st_file_attributes & FILE_ATTRIBUTE_REPARSE_POINT
            or (
                before is not None
                and (
                    before.st_ino == 0
                    or (before.st_dev, before.st_ino) != (after.st_dev, after.st_ino)
                )
            )
        ):
            raise OSError(errno.ELOOP, "refusing to follow a reparse point", str(path))
    except BaseException:
        os.close(fd)
        raise
    return fd


def _profile_root() -> str:
    return _long_path(os.path.abspath(os.environ["USERPROFILE"]))


def _long_path(path: str) -> str:
    """``path`` with 8.3 short names expanded (RUNNER~1 -> runneradmin). Reparse points are not followed."""
    size = _kernel32.GetLongPathNameW(path, None, 0)
    if not size:  # missing: keep the spelling, the walk checks what exists
        return path
    buf = ctypes.create_unicode_buffer(size)
    if not _kernel32.GetLongPathNameW(path, buf, size):
        return path
    return buf.value


def _windows_owned_private(path: str, mask: int) -> bool:
    owner, dacl, sd = ctypes.c_void_p(), ctypes.c_void_p(), ctypes.c_void_p()
    rc = _advapi32.GetNamedSecurityInfoW(
        path,
        SE_FILE_OBJECT,
        OWNER_SECURITY_INFORMATION | DACL_SECURITY_INFORMATION,
        ctypes.byref(owner),
        None,
        ctypes.byref(dacl),
        None,
        ctypes.byref(sd),
    )
    if rc:
        raise OSError(rc)
    try:
        if (
            owner.value is None or dacl.value is None
        ):  # a NULL DACL gives everyone full access
            return False
        trusted = {*TRUSTED_FIXED, current_user_id()}
        if _sid_to_string(owner) not in trusted:
            return False
        acl = ctypes.cast(dacl, P(_ACL)).contents
        for i in range(acl.AceCount):
            ace_ptr = ctypes.c_void_p()
            if not _advapi32.GetAce(dacl, i, ctypes.byref(ace_ptr)):
                raise ctypes.WinError(ctypes.get_last_error())
            header = ctypes.cast(ace_ptr, P(_ACE_HEADER)).contents
            if (
                header.AceFlags & INHERIT_ONLY_ACE
                or header.AceType == ACCESS_DENIED_ACE_TYPE
            ):
                continue
            if (
                header.AceType != ACCESS_ALLOWED_ACE_TYPE
            ):  # fail closed on any other ACE type
                return False
            ace = ctypes.cast(ace_ptr, P(_ACCESS_ALLOWED_ACE)).contents
            if (
                _sid_to_string(ace_ptr.value + _ACCESS_ALLOWED_ACE.SidStart.offset)
                in trusted
            ):
                continue
            if ace.Mask & (WRITE_BITS if mask == 0o022 else 0xFFFFFFFF):
                return False
        return True
    finally:
        _kernel32.LocalFree(sd)


def _token_user_sid() -> str:
    token = wintypes.HANDLE()
    if not _advapi32.OpenProcessToken(
        _kernel32.GetCurrentProcess(), TOKEN_QUERY, ctypes.byref(token)
    ):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        size = wintypes.DWORD(0)
        # The first call fails with ERROR_INSUFFICIENT_BUFFER and sets the size we need.
        _advapi32.GetTokenInformation(token, TOKEN_USER, None, 0, ctypes.byref(size))
        buf = ctypes.create_string_buffer(size.value)
        if not _advapi32.GetTokenInformation(
            token, TOKEN_USER, buf, size.value, ctypes.byref(size)
        ):
            raise ctypes.WinError(ctypes.get_last_error())
        return _sid_to_string(ctypes.cast(buf, P(_SID_AND_ATTRIBUTES)).contents.Sid)
    finally:
        _kernel32.CloseHandle(token)


def _sid_to_string(sid: Any) -> str:
    text = wintypes.LPWSTR()
    if not _advapi32.ConvertSidToStringSidW(sid, ctypes.byref(text)):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        return text.value
    finally:
        _kernel32.LocalFree(ctypes.cast(text, ctypes.c_void_p))
