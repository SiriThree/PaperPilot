"""Strict Windows-only credential file reader using an already-open file handle.

Allow only a protected DACL containing simple, explicit allow ACEs for the
current effective user. This intentionally rejects common shared ACLs. Elevated
administrators and the operating system remain trusted; this is not a vault.
"""

import ctypes
import os
from ctypes import wintypes


class ACE_HEADER(ctypes.Structure):
    _fields_ = [("type", wintypes.BYTE), ("flags", wintypes.BYTE), ("size", wintypes.WORD)]


class ACL_SIZE_INFORMATION(ctypes.Structure):
    _fields_ = [("count", wintypes.DWORD), ("used", wintypes.DWORD), ("free", wintypes.DWORD)]


class FILE_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("attributes", wintypes.DWORD),
        ("created", wintypes.FILETIME),
        ("accessed", wintypes.FILETIME),
        ("written", wintypes.FILETIME),
        ("serial", wintypes.DWORD),
        ("size_high", wintypes.DWORD),
        ("size_low", wintypes.DWORD),
        ("links", wintypes.DWORD),
        ("index_high", wintypes.DWORD),
        ("index_low", wintypes.DWORD),
    ]


def read_windows_secret(path, limit):
    if not os.path.isabs(path) or len(path) < 3 or path[1:3] not in (":\\", ":/"):
        raise RuntimeError("Protected secret file must use a local absolute path")
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    advapi = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel.GetDriveTypeW.argtypes = [wintypes.LPCWSTR]
    kernel.GetDriveTypeW.restype = wintypes.UINT
    if kernel.GetDriveTypeW(path[:3]) != 3:  # DRIVE_FIXED
        raise RuntimeError("Protected secret file requires a local fixed drive")
    kernel.CreateFileW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.c_void_p,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    ]
    kernel.CreateFileW.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    kernel.GetFileType.argtypes = [wintypes.HANDLE]
    kernel.GetFileType.restype = wintypes.DWORD
    kernel.GetFileInformationByHandle.argtypes = [wintypes.HANDLE, ctypes.POINTER(FILE_INFORMATION)]
    kernel.GetFileInformationByHandle.restype = wintypes.BOOL
    kernel.ReadFile.argtypes = [
        wintypes.HANDLE,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
        ctypes.c_void_p,
    ]
    kernel.ReadFile.restype = wintypes.BOOL
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    kernel.GetCurrentThread.restype = wintypes.HANDLE
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    advapi.GetSecurityInfo.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_void_p),
    ]
    advapi.GetSecurityInfo.restype = wintypes.DWORD
    advapi.GetSecurityDescriptorControl.argtypes = [
        ctypes.c_void_p,
        ctypes.POINTER(wintypes.WORD),
        ctypes.POINTER(wintypes.DWORD),
    ]
    advapi.GetSecurityDescriptorControl.restype = wintypes.BOOL
    advapi.OpenThreadToken.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        wintypes.BOOL,
        ctypes.POINTER(wintypes.HANDLE),
    ]
    advapi.OpenThreadToken.restype = wintypes.BOOL
    advapi.OpenProcessToken.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE)]
    advapi.OpenProcessToken.restype = wintypes.BOOL
    advapi.GetTokenInformation.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
    ]
    advapi.GetTokenInformation.restype = wintypes.BOOL
    advapi.GetAclInformation.argtypes = [ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD]
    advapi.GetAclInformation.restype = wintypes.BOOL
    advapi.GetAce.argtypes = [ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(ctypes.c_void_p)]
    advapi.GetAce.restype = wintypes.BOOL
    advapi.IsValidSid.argtypes = [ctypes.c_void_p]
    advapi.IsValidSid.restype = wintypes.BOOL
    advapi.GetLengthSid.argtypes = [ctypes.c_void_p]
    advapi.GetLengthSid.restype = wintypes.DWORD
    advapi.EqualSid.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    advapi.EqualSid.restype = wintypes.BOOL

    # No share-write/delete: the checked handle and read refer to the same file.
    handle = kernel.CreateFileW(path, 0x80000000 | 0x00020000, 1, None, 3, 0x00200000, None)
    if handle == wintypes.HANDLE(-1).value:
        raise RuntimeError("Protected secret file could not be opened")
    descriptor = ctypes.c_void_p()
    token = wintypes.HANDLE()
    try:
        info = FILE_INFORMATION()
        if (
            kernel.GetFileType(handle) != 1
            or not kernel.GetFileInformationByHandle(handle, ctypes.byref(info))
            or info.attributes & (0x10 | 0x400)
        ):
            raise ValueError("not a regular non-reparse file")
        size = (info.size_high << 32) | info.size_low
        if not 0 < size <= limit:
            raise ValueError("invalid size")
        owner, dacl = ctypes.c_void_p(), ctypes.c_void_p()
        if (
            advapi.GetSecurityInfo(
                handle,
                1,
                0x1 | 0x4,
                ctypes.byref(owner),
                None,
                ctypes.byref(dacl),
                None,
                ctypes.byref(descriptor),
            )
            != 0
        ):
            raise ValueError("security descriptor unavailable")
        control, revision = wintypes.WORD(), wintypes.DWORD()
        if (
            not descriptor.value
            or not owner.value
            or not dacl.value
            or not advapi.GetSecurityDescriptorControl(
                descriptor, ctypes.byref(control), ctypes.byref(revision)
            )
            or not control.value & 0x1000
        ):  # SE_DACL_PROTECTED
            raise ValueError("unprotected DACL")
        if not advapi.OpenThreadToken(kernel.GetCurrentThread(), 0x8, True, ctypes.byref(token)):
            if ctypes.get_last_error() != 1008 or not advapi.OpenProcessToken(
                kernel.GetCurrentProcess(), 0x8, ctypes.byref(token)
            ):
                raise ValueError("effective identity unavailable")
        needed = wintypes.DWORD()
        advapi.GetTokenInformation(token, 1, None, 0, ctypes.byref(needed))
        if not needed.value or needed.value > 65536:
            raise ValueError("identity unavailable")
        token_info = ctypes.create_string_buffer(needed.value)
        if not advapi.GetTokenInformation(token, 1, token_info, needed, ctypes.byref(needed)):
            raise ValueError("identity unavailable")
        user_sid = ctypes.cast(token_info, ctypes.POINTER(ctypes.c_void_p)).contents.value
        if not user_sid or not advapi.IsValidSid(user_sid) or not advapi.EqualSid(owner, user_sid):
            raise ValueError("unexpected owner")
        acl = ACL_SIZE_INFORMATION()
        if not advapi.GetAclInformation(dacl, ctypes.byref(acl), ctypes.sizeof(acl), 2) or not acl.count:
            raise ValueError("invalid DACL")
        for index in range(acl.count):
            ace = ctypes.c_void_p()
            if not advapi.GetAce(dacl, index, ctypes.byref(ace)) or not ace.value:
                raise ValueError("invalid ACE")
            header = ACE_HEADER.from_address(ace.value)
            sid = ace.value + 8
            if (
                header.type != 0
                or header.flags != 0
                or header.size < 16
                or not advapi.IsValidSid(sid)
                or advapi.GetLengthSid(sid) > header.size - 8
                or not advapi.EqualSid(sid, user_sid)
            ):
                raise ValueError("unsupported or untrusted ACE")
        result = ctypes.create_string_buffer(size)
        received = wintypes.DWORD()
        if not kernel.ReadFile(handle, result, size, ctypes.byref(received), None) or received.value != size:
            raise ValueError("secret file read failed")
        raw = result.raw[:size]
        if b"\x00" in raw:
            raise ValueError("invalid secret")
        value = raw.decode("utf-8")
        return value[:-2] if value.endswith("\r\n") else value[:-1] if value.endswith("\n") else value
    except (OSError, ValueError, UnicodeError):
        raise RuntimeError("Protected secret file could not be read safely") from None
    finally:
        if descriptor.value:
            kernel.LocalFree(descriptor)
        if token.value:
            kernel.CloseHandle(token)
        kernel.CloseHandle(handle)
