"""Windows 鼠标控制 — 虚拟 1600 DPI 一步到位式瞄准。

将像素偏移直接换算为鼠标输入计数，单次 SendInput 完成移动。
"""

from __future__ import annotations

import ctypes
import time
from ctypes import wintypes

user32 = ctypes.windll.user32

MOUSEEVENTF_MOVE = 0x0001
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
INPUT_MOUSE = 0


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", wintypes.LONG),
        ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_size_t),
    ]


class INPUT(ctypes.Structure):
    class _INPUT(ctypes.Union):
        _fields_ = [("mi", MOUSEINPUT)]

    _anonymous_ = ("u",)
    _fields_ = [("type", wintypes.DWORD), ("u", _INPUT)]


class MouseController:
    """虚拟 1600 DPI 鼠标控制器 — 像素偏移 → 计数 → 单次移动。"""

    def move_relative(self, dx: int, dy: int) -> None:
        """发送单次相对移动（dx/dy 为鼠标输入计数）。"""
        if dx == 0 and dy == 0:
            return
        inp = INPUT(type=INPUT_MOUSE)
        inp.mi = MOUSEINPUT(dx=dx, dy=dy, dwFlags=MOUSEEVENTF_MOVE, dwExtraInfo=0)
        user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(INPUT))

    def click(self) -> None:
        """发送一次鼠标左键点击（mouse_event + SendInput 双重保障）。"""
        user32.mouse_event(MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
        time.sleep(0.02)
        user32.mouse_event(MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)
        time.sleep(0.008)
        inp_down = INPUT(type=INPUT_MOUSE)
        inp_down.mi = MOUSEINPUT(dwFlags=MOUSEEVENTF_LEFTDOWN)
        inp_up = INPUT(type=INPUT_MOUSE)
        inp_up.mi = MOUSEINPUT(dwFlags=MOUSEEVENTF_LEFTUP)
        user32.SendInput(1, ctypes.byref(inp_down), ctypes.sizeof(INPUT))
        time.sleep(0.02)
        user32.SendInput(1, ctypes.byref(inp_up), ctypes.sizeof(INPUT))
