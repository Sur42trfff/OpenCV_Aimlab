"""屏幕 / 窗口区域截取 — 基于 dxcam (Desktop Duplication API) 从显存直读。

支持显示器截取与指定进程窗口截取两种模式。
相比 mss (GDI) 延迟更低、帧率更高，不经过 CPU 拷贝。
"""

from __future__ import annotations

import ctypes
import struct
from ctypes import wintypes
from dataclasses import dataclass

import dxcam
import numpy as np

# 避免 Windows 缩放导致截屏区域错位
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32

TH32CS_SNAPPROCESS = 0x00000002


class PROCESSENTRY32(ctypes.Structure):
    _fields_ = [
        ("dwSize", wintypes.DWORD),
        ("cntUsage", wintypes.DWORD),
        ("th32ProcessID", wintypes.DWORD),
        ("th32DefaultHeapID", ctypes.c_size_t),
        ("th32ModuleID", wintypes.DWORD),
        ("cntThreads", wintypes.DWORD),
        ("th32ParentProcessID", wintypes.DWORD),
        ("pcPriClassBase", wintypes.LONG),
        ("dwFlags", wintypes.DWORD),
        ("szExeFile", ctypes.c_wchar * 260),
    ]


class RECT(ctypes.Structure):
    _fields_ = [
        ("left", wintypes.LONG),
        ("top", wintypes.LONG),
        ("right", wintypes.LONG),
        ("bottom", wintypes.LONG),
    ]


class MONITORINFOEXW(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("rcMonitor", RECT),
        ("rcWork", RECT),
        ("dwFlags", wintypes.DWORD),
        ("szDevice", ctypes.c_wchar * 32),
    ]


# ---------------------------------------------------------------------------
# 窗口查找（通过进程名）
# ---------------------------------------------------------------------------


def _find_process_id(process_name: str) -> int | None:
    snap = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if snap == -1:
        return None
    entry = PROCESSENTRY32()
    entry.dwSize = ctypes.sizeof(entry)
    if not kernel32.Process32FirstW(snap, ctypes.byref(entry)):
        kernel32.CloseHandle(snap)
        return None
    target = process_name.lower()
    while True:
        if entry.szExeFile.lower() == target:
            kernel32.CloseHandle(snap)
            return int(entry.th32ProcessID)
        if not kernel32.Process32NextW(snap, ctypes.byref(entry)):
            break
    kernel32.CloseHandle(snap)
    return None


def _find_windows_for_pid(pid: int) -> list[int]:
    windows: list[int] = []

    WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def _enum_cb(hwnd: int, lparam: int) -> bool:
        if not user32.IsWindowVisible(hwnd):
            return True
        if user32.IsIconic(hwnd):
            return True
        proc_id = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(proc_id))
        if proc_id.value == pid:
            windows.append(hwnd)
        return True

    user32.EnumWindows(WNDENUMPROC(_enum_cb), 0)
    return windows


def _get_client_rect_screen(hwnd: int) -> tuple[int, int, int, int] | None:
    rect = RECT()
    if not user32.GetClientRect(hwnd, ctypes.byref(rect)):
        return None
    pt = wintypes.POINT()
    if not user32.ClientToScreen(hwnd, ctypes.byref(pt)):
        return None
    w = rect.right - rect.left
    h = rect.bottom - rect.top
    if w <= 0 or h <= 0:
        return None
    return pt.x, pt.y, w, h


def find_window_by_process(process_name: str) -> tuple[int, int, int, int] | None:
    pid = _find_process_id(process_name)
    if pid is None:
        return None
    hwnds = _find_windows_for_pid(pid)
    if not hwnds:
        return None
    best: tuple[int, int, int, int] | None = None
    best_area = 0
    for hwnd in hwnds:
        r = _get_client_rect_screen(hwnd)
        if r is None:
            continue
        area = r[2] * r[3]
        if area > best_area:
            best_area = area
            best = r
    return best


# ---------------------------------------------------------------------------
# 显示器枚举（Win32，不依赖 mss）
# ---------------------------------------------------------------------------

_monitors_cache: list[dict] | None = None


def _enum_monitors() -> list[dict]:
    """枚举所有显示器，返回 [{left, top, width, height, device, index}]，index 从 0 开始。"""
    global _monitors_cache
    if _monitors_cache is not None:
        return _monitors_cache

    result: list[dict] = []
    idx_counter = [0]

    MONITORENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HMONITOR, wintypes.HDC, ctypes.POINTER(RECT), wintypes.LPARAM)

    def _cb(hmon: int, hdc: int, rect_p, _lparam: int) -> bool:
        info = MONITORINFOEXW()
        info.cbSize = ctypes.sizeof(info)
        if not user32.GetMonitorInfoW(hmon, ctypes.byref(info)):
            return True
        r = info.rcMonitor
        result.append({
            "left": r.left,
            "top": r.top,
            "width": r.right - r.left,
            "height": r.bottom - r.top,
            "device": info.szDevice,
            "index": idx_counter[0],
        })
        idx_counter[0] += 1
        return True

    user32.EnumDisplayMonitors(None, None, MONITORENUMPROC(_cb), 0)
    _monitors_cache = result
    return result


def _get_monitor_index_at_point(screen_x: int, screen_y: int) -> int:
    """返回屏幕坐标所在显示器的 0-based index。"""
    monitors = _enum_monitors()
    hmon = user32.MonitorFromPoint(wintypes.POINT(screen_x, screen_y), 1)  # MONITOR_DEFAULTTONEAREST
    info = MONITORINFOEXW()
    info.cbSize = ctypes.sizeof(info)
    if user32.GetMonitorInfoW(hmon, ctypes.byref(info)):
        device = info.szDevice
        for m in monitors:
            if m["device"] == device:
                return m["index"]
    return 0


# ---------------------------------------------------------------------------
# 刷新率
# ---------------------------------------------------------------------------

def get_refresh_rate(monitor_index: int = 1) -> int:
    """获取指定显示器的当前刷新率（Hz）。失败时返回 60。"""
    output_idx = max(0, monitor_index - 1)
    monitors = _enum_monitors()
    if output_idx < len(monitors):
        device = monitors[output_idx]["device"]
    else:
        device = None

    # 方法 1：桌面 DC
    try:
        hdc = user32.GetDC(None)
        if hdc:
            refresh = ctypes.windll.gdi32.GetDeviceCaps(hdc, 116)
            user32.ReleaseDC(None, hdc)
            if 30 <= refresh <= 500:
                return int(refresh)
    except Exception:
        pass

    # 方法 2：指定设备 DC
    if device:
        try:
            hdc = ctypes.windll.gdi32.CreateDCW("DISPLAY", device, None, None)
            if hdc:
                refresh = ctypes.windll.gdi32.GetDeviceCaps(hdc, 116)
                ctypes.windll.gdi32.DeleteDC(hdc)
                if 30 <= refresh <= 500:
                    return int(refresh)
        except Exception:
            pass

    # 方法 3：EnumDisplaySettings
    try:
        buf = ctypes.create_string_buffer(256)
        struct.pack_into("H", buf, 68, 220)
        if user32.EnumDisplaySettingsW(device or None, -1, buf):
            freq = struct.unpack_from("I", buf, 184)[0]
            if 30 <= freq <= 500:
                return int(freq)
    except Exception:
        pass
    return 60


# ---------------------------------------------------------------------------
# 捕获区域
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class CaptureRegion:
    left: int
    top: int
    width: int
    height: int

    @property
    def center_screen(self) -> tuple[int, int]:
        return self.left + self.width // 2, self.top + self.height // 2


def build_region(monitor: dict, scale: float) -> CaptureRegion:
    w, h = monitor["width"], monitor["height"]
    rw, rh = int(w * scale), int(h * scale)
    left = monitor["left"] + (w - rw) // 2
    top = monitor["top"] + (h - rh) // 2
    return CaptureRegion(left, top, rw, rh)


def build_window_region(
    left: int, top: int, width: int, height: int, scale: float
) -> CaptureRegion:
    rw, rh = int(width * scale), int(height * scale)
    r_left = left + (width - rw) // 2
    r_top = top + (height - rh) // 2
    return CaptureRegion(r_left, r_top, rw, rh)


# ---------------------------------------------------------------------------
# 屏幕捕获（dxcam）
# ---------------------------------------------------------------------------

def _get_dxcam_outputs() -> list[tuple[int, int]]:
    """返回所有 dxcam 输出端口列表，每项为 (device_idx, output_idx)。"""
    outs = dxcam.__factory.outputs
    flat: list[tuple[int, int]] = []
    for didx, outputs in enumerate(outs):
        for oidx in range(len(outputs)):
            flat.append((didx, oidx))
    return flat


class ScreenCapture:
    def __init__(
        self,
        monitor_index: int = 1,
        region_scale: float = 0.55,
        capture_mode: str = "monitor",
        window_process: str = "AimLab_tb.exe",
    ):
        self.capture_mode = capture_mode
        self._camera: dxcam.DXCamera | None = None

        monitors = _enum_monitors()

        if capture_mode == "window":
            rect = find_window_by_process(window_process)
            if rect is None:
                raise RuntimeError(
                    f"找不到进程窗口: {window_process}，请确保游戏已启动"
                )
            win_left, win_top, win_w, win_h = rect
            center_x = win_left + win_w // 2
            center_y = win_top + win_h // 2
            output_idx = _get_monitor_index_at_point(center_x, center_y)

            self._monitor_left = monitors[output_idx]["left"]
            self._monitor_top = monitors[output_idx]["top"]
            self.window_width = win_w
            self.window_height = win_h
            self.region = build_window_region(win_left, win_top, win_w, win_h, region_scale)
            self.monitor_index = -1

            print(f"[捕获] 窗口模式 — {window_process}")
            print(f"  客户区: {win_w}x{win_h}  捕获: {self.region.width}x{self.region.height}")
        else:
            output_idx = max(0, monitor_index - 1)
            output_idx = min(output_idx, len(monitors) - 1)
            mon = monitors[output_idx]

            self._monitor_left = mon["left"]
            self._monitor_top = mon["top"]
            self.window_width = mon["width"]
            self.window_height = mon["height"]
            self.region = build_region(mon, region_scale)
            self.monitor_index = output_idx + 1

            print(f"[捕获] 显示器模式 — 屏幕 {output_idx + 1}  缩放 {region_scale}")
            print(f"  捕获: {self.region.width}x{self.region.height}")

        flat_outputs = _get_dxcam_outputs()
        output_idx = min(output_idx, len(flat_outputs) - 1)
        dev_idx, out_idx = flat_outputs[output_idx]
        self._camera = dxcam.create(device_idx=dev_idx, output_idx=out_idx, output_color="BGR")

    def grab_bgr(self) -> np.ndarray:
        rel_left = self.region.left - self._monitor_left
        rel_top = self.region.top - self._monitor_top
        region = (
            rel_left,
            rel_top,
            rel_left + self.region.width,
            rel_top + self.region.height,
        )
        frame = self._camera.grab(region=region)
        if frame is None:
            return np.zeros(
                (self.region.height, self.region.width, 3), dtype=np.uint8
            )
        return frame

    def region_to_screen(self, x: int, y: int) -> tuple[int, int]:
        return self.region.left + x, self.region.top + y
