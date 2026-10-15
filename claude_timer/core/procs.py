# -*- coding: utf-8 -*-
"""procs.py — 「這個 pid 還是不是當初那個行程」。

pid 會被作業系統重用：記下來的 pid 還活著，不代表就是當初那個 claude。所以凡是要靠 pid 判斷
「它還在跑嗎」（resumer 的 job、~/.claude/sessions/<pid>.json 的持有者）都要連**行程建立時間**一起比：
Windows 用 GetProcessTimes 的建立時間（FILETIME，100 奈秒為單位的整數），Claude Code 自己寫在
sessions/<pid>.json 裡的 procStart 也是同一種單位，可以直接比。

純查作業系統，不碰 Claude、不花 token。
"""
from __future__ import annotations

import ctypes
import os
import platform
import sys
from typing import Optional

PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
STILL_ACTIVE = 259


def process_created(pid: int) -> Optional[int]:
    """pid 還活著就回它的建立時間（Windows：FILETIME 整數；其他平台：0），不在了回 None。"""
    if not pid:
        return None
    if platform.system() != "Windows":
        try:
            os.kill(pid, 0)
            return 0
        except OSError:
            return None
    k = ctypes.windll.kernel32
    handle = k.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
    if not handle:
        return None
    try:
        code = ctypes.c_ulong()
        k.GetExitCodeProcess(handle, ctypes.byref(code))
        if code.value != STILL_ACTIVE:
            return None
        times = [ctypes.c_ulonglong() for _ in range(4)]
        if not k.GetProcessTimes(handle, *(ctypes.byref(t) for t in times)):
            return None
        return int(times[0].value)
    finally:
        k.CloseHandle(handle)


def process_image(pid: int) -> Optional[str]:
    """pid 的執行檔完整路徑（小寫）；開不了行程回 None，查不到名稱回空字串。"""
    if platform.system() != "Windows":
        return ""
    k = ctypes.windll.kernel32
    handle = k.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
    if not handle:
        return None
    try:
        buf = ctypes.create_unicode_buffer(1024)
        size = ctypes.c_uint(1024)
        ok = k.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size))
        return buf.value.lower() if ok else ""
    finally:
        k.CloseHandle(handle)


def same_process(pid: int, created: Optional[int]) -> bool:
    """pid 還活著，而且（有記建立時間的話）建立時間對得上。"""
    now = process_created(pid)
    if now is None:
        return False
    return not created or now == created


def descendants(pid: int) -> list[tuple[int, str]]:
    """pid 底下所有的子孫行程 [(pid, 執行檔名)]（Toolhelp32 快照；非 Windows 或失敗就空清單）。

    強制接管時 taskkill /T 會連它們一起關：在那之前先記下名字，log 才寫得出「連帶關掉了什麼」——
    VSCode 裡那個對話開的 bash、背景跑的測試、它自己起的伺服器都在這裡面。"""
    if sys.platform != "win32":
        return []
    import ctypes
    from ctypes import wintypes

    class PROCESSENTRY32W(ctypes.Structure):
        _fields_ = [("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD), ("th32ProcessID", wintypes.DWORD),
                    ("th32DefaultHeapID", ctypes.c_void_p), ("th32ModuleID", wintypes.DWORD),
                    ("cntThreads", wintypes.DWORD), ("th32ParentProcessID", wintypes.DWORD),
                    ("pcPriClassBase", ctypes.c_long), ("dwFlags", wintypes.DWORD),
                    ("szExeFile", ctypes.c_wchar * 260)]

    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    snap = k32.CreateToolhelp32Snapshot(0x00000002, 0)          # TH32CS_SNAPPROCESS
    if not snap or snap == wintypes.HANDLE(-1).value:
        return []
    children: dict[int, list[tuple[int, str]]] = {}
    try:
        entry = PROCESSENTRY32W()
        entry.dwSize = ctypes.sizeof(PROCESSENTRY32W)
        ok = k32.Process32FirstW(snap, ctypes.byref(entry))
        while ok:
            children.setdefault(int(entry.th32ParentProcessID), []).append((int(entry.th32ProcessID),
                                                                            entry.szExeFile))
            ok = k32.Process32NextW(snap, ctypes.byref(entry))
    finally:
        k32.CloseHandle(snap)
    out: list[tuple[int, str]] = []
    todo, seen = [int(pid)], {int(pid)}
    while todo:
        for child in children.get(todo.pop(), []):
            if child[0] not in seen:            # pid 重用時快照裡可能繞回來
                seen.add(child[0])
                out.append(child)
                todo.append(child[0])
    return out

