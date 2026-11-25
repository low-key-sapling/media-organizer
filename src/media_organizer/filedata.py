"""Supplemental user data is checked separately from the ordinary byte stream."""
from __future__ import annotations

import errno
import hashlib
import os
import stat
import time
from pathlib import Path

BLOCK = 1024 * 1024


class ProcessLock:
    """Only one process may schedule or recover jobs in a state directory."""

    def __init__(self, directory: Path):
        directory.mkdir(parents=True, exist_ok=True)
        self.handle = (directory / ".process.lock").open("a+b")
        self.handle.seek(0, 2)
        if self.handle.tell() == 0:
            self.handle.write(b"0")
            self.handle.flush()
        self.handle.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(self.handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.handle.close()
            raise ValueError("此任务目录已有程序在运行，请使用现有界面，或退出后再使用命令行") from None

    def close(self):
        if self.handle.closed:
            return
        self.handle.seek(0)
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(self.handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            fcntl.flock(self.handle.fileno(), fcntl.LOCK_UN)
        self.handle.close()


class Interrupted(Exception):
    pass


def sha256(path: Path, stop=None, progress=None) -> str:
    digest = hashlib.sha256()
    read, last = 0, time.monotonic()
    with path.open("rb") as source:
        while chunk := source.read(BLOCK):
            if stop and stop.is_set():
                raise Interrupted()
            digest.update(chunk)
            read += len(chunk)
            if progress and time.monotonic() - last >= 0.4:
                progress(read)
                last = time.monotonic()
    if progress:
        progress(read)
    return digest.hexdigest()


def extra_names(path: Path) -> list[str]:
    if os.name != "nt":
        if not hasattr(os, "listxattr"):
            return []
        try:
            return ["xattr:" + name for name in os.listxattr(path, follow_symlinks=False) if name.startswith("user.")]
        except OSError as error:
            if error.errno in (errno.ENOTSUP, errno.EOPNOTSUPP):
                return []
            raise
    import ctypes
    from ctypes import wintypes
    class StreamData(ctypes.Structure):
        _fields_ = [("size", ctypes.c_longlong), ("name", wintypes.WCHAR * 296)]
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    first = kernel.FindFirstStreamW
    first.argtypes = [wintypes.LPCWSTR, ctypes.c_int, ctypes.POINTER(StreamData), wintypes.DWORD]
    first.restype = wintypes.HANDLE
    following = kernel.FindNextStreamW
    following.argtypes = [wintypes.HANDLE, ctypes.POINTER(StreamData)]
    following.restype = wintypes.BOOL
    close = kernel.FindClose
    close.argtypes = [wintypes.HANDLE]
    data = StreamData()
    handle = first(str(path), 0, ctypes.byref(data), 0)
    if handle == ctypes.c_void_p(-1).value:
        code = ctypes.get_last_error()
        if code in (1, 38, 50):
            return []
        raise ctypes.WinError(code)
    names = []
    try:
        while True:
            name = data.name
            if name != "::$DATA" and name.endswith(":$DATA"):
                names.append("ads:" + name[:-6])
            if not following(handle, ctypes.byref(data)):
                code = ctypes.get_last_error()
                if code != 38:
                    raise ctypes.WinError(code)
                break
    finally:
        close(handle)
    return names


def extra_signature(path: Path, stop=None) -> tuple[dict, list[str]]:
    result, issues = {}, []
    try:
        names = extra_names(path)
    except OSError as error:
        return {}, [f"无法检查附加数据：{error}"]
    for name in names:
        try:
            if name.startswith("ads:"):
                result[name] = sha256(Path(str(path) + name[4:]), stop)
            else:
                result[name] = hashlib.sha256(os.getxattr(path, name[6:], follow_symlinks=False)).hexdigest()
        except OSError as error:
            issues.append(f"附加数据 {name} 无法读取：{error}")
    return result, issues


def copy_extras(source: Path, target: Path, stop=None) -> tuple[dict, list[str]]:
    expected, issues = extra_signature(source, stop)
    for name in expected:
        try:
            if name.startswith("ads:"):
                with open(str(source) + name[4:], "rb") as src, open(str(target) + name[4:], "wb") as dst:
                    while chunk := src.read(BLOCK):
                        if stop and stop.is_set():
                            raise Interrupted()
                        dst.write(chunk)
                    dst.flush()
                    os.fsync(dst.fileno())
            else:
                os.setxattr(target, name[6:], os.getxattr(source, name[6:], follow_symlinks=False), follow_symlinks=False)
        except OSError as error:
            issues.append(f"附加数据 {name} 无法保留：{error}")
    actual, target_issues = extra_signature(target, stop)
    issues.extend(target_issues)
    if actual != expected:
        issues.append("附加数据未完整保留")
    return expected, issues


def preserve_windows_properties(source: Path, target: Path) -> list[str]:
    if os.name != "nt":
        return []
    import ctypes
    from ctypes import wintypes
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    create = kernel.CreateFileW
    create.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    create.restype = wintypes.HANDLE
    set_time = kernel.SetFileTime
    set_time.argtypes = [wintypes.HANDLE, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p]
    set_time.restype = wintypes.BOOL
    close = kernel.CloseHandle
    close.argtypes = [wintypes.HANDLE]
    class FileTime(ctypes.Structure):
        _fields_ = [("low", wintypes.DWORD), ("high", wintypes.DWORD)]
    warnings = []
    source_stat = source.stat()
    birth = getattr(source_stat, "st_birthtime_ns", None)
    if birth is not None:
        handle = create(str(target), 0x100, 0x7, None, 3, 0, None)
        if handle == ctypes.c_void_p(-1).value:
            warnings.append("Windows 创建时间无法保留，原始时间已记录")
        else:
            try:
                ticks = birth // 100 + 116444736000000000
                creation = FileTime(ticks & 0xFFFFFFFF, ticks >> 32)
                if not set_time(handle, ctypes.byref(creation), None, None):
                    warnings.append("Windows 创建时间无法保留，原始时间已记录")
            finally:
                close(handle)
    set_attributes = kernel.SetFileAttributesW
    set_attributes.argtypes = [wintypes.LPCWSTR, wintypes.DWORD]
    set_attributes.restype = wintypes.BOOL
    attributes = getattr(source_stat, "st_file_attributes", 0) & (0x1 | 0x2 | 0x4 | 0x20 | 0x2000)
    if not set_attributes(str(target), attributes or 0x80):
        warnings.append("部分 Windows 文件属性无法保留")
    return warnings


def property_report(entry: dict, target: Path) -> dict:
    actual = target.stat()
    birth = getattr(actual, "st_birthtime_ns", None)
    differences = []
    if actual.st_mtime_ns != entry["mtime_ns"]:
        differences.append("修改时间不同，原始值已记录")
    if entry.get("birthtime_ns") is not None and birth != entry["birthtime_ns"]:
        differences.append("创建时间不同，原始值已记录")
    if stat.S_IMODE(actual.st_mode) != stat.S_IMODE(entry["mode"]):
        differences.append("权限模式不同")
    if os.name != "nt" and (actual.st_uid != entry.get("uid") or actual.st_gid != entry.get("gid")):
        differences.append("所有者或组不同")
    return {"target_mtime_ns": actual.st_mtime_ns, "target_birthtime_ns": birth, "target_mode": actual.st_mode, "attribute_differences": differences, "permissions_note": "所有者、ACL 和系统权限不构成跨平台一致性保证"}
