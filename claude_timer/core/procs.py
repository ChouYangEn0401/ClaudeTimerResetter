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
