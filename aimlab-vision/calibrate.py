"""
颜色校准 — 多点取色版

使用前:
  1. Aim Lab 窗口化，进入有小球的训练
  2. 把游戏放在主屏中央

操作:
  F3       冻结当前画面（截图）
  鼠标左键  点击小球不同部位取色（亮面、暗面、边缘）
  鼠标右键  撤销上一次取色
  U        回到实时预览
  R        重置全部取色
  滑块      调节 V 容差（阴影覆盖范围）
  空格      保存到 config.yaml
  Q        退出

原理:
  每次点击采集 9×9 像素块中所有 HSV 值，多点击后取全部样本的
  min/max 再加上容差 margin，得到覆盖带阴影小球的 HSV 区间。
"""

from __future__ import annotations

import ctypes
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np
import yaml

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from src.capture import ScreenCapture
from src.config_loader import load_config

WIN = "Calibrate"
PATCH_SIZE = 9           # 取色窗口大小（奇数）
MARGIN_H_DEFAULT = 5     # H 容差
MARGIN_S_DEFAULT = 30    # S 容差
MARGIN_V_DEFAULT = 30    # V 容差（可滚轮调节）


@dataclass
class SampleData:
    x: int
    y: int
    h_vals: list[int] = field(default_factory=list)
    s_vals: list[int] = field(default_factory=list)
    v_vals: list[int] = field(default_factory=list)


class CalibrationState:
    def __init__(self):
        self.is_frozen = False
        self.frozen_frame: np.ndarray | None = None
        self.live_frame: np.ndarray | None = None
        self.samples: list[SampleData] = []
        self.margin_v = MARGIN_V_DEFAULT

    def reset(self):
        self.samples.clear()

    def all_h(self) -> list[int]:
        return [v for s in self.samples for v in s.h_vals]

    def all_s(self) -> list[int]:
        return [v for s in self.samples for v in s.s_vals]

    def all_v(self) -> list[int]:
        return [v for s in self.samples for v in s.v_vals]

    @property
    def sample_count(self) -> int:
        return len(self.samples)

    def compute_range(self) -> tuple[np.ndarray, np.ndarray]:
        """从全部采样像素计算 HSV 区间。"""
        if not self.samples:
            return (
                np.array([0, 0, 0], dtype=np.uint8),
                np.array([179, 255, 255], dtype=np.uint8),
            )
        h_all = self.all_h()
        s_all = self.all_s()
        v_all = self.all_v()
        lo = np.array(
            [
                max(0, min(h_all) - MARGIN_H_DEFAULT),
                max(0, min(s_all) - MARGIN_S_DEFAULT),
                max(0, min(v_all) - self.margin_v),
            ],
            dtype=np.uint8,
        )
        hi = np.array(
            [
                min(179, max(h_all) + MARGIN_H_DEFAULT),
                min(255, max(s_all) + MARGIN_S_DEFAULT),
                min(255, max(v_all) + self.margin_v),
            ],
            dtype=np.uint8,
        )
        return lo, hi


def _extract_patch(frame: np.ndarray, cx: int, cy: int) -> SampleData:
    """从帧中提取 PATCH_SIZE×PATCH_SIZE 的 HSV 采样数据。"""
    h, w = frame.shape[:2]
    half = PATCH_SIZE // 2
    x1 = max(0, cx - half)
    y1 = max(0, cy - half)
    x2 = min(w, cx + half + 1)
    y2 = min(h, cy + half + 1)
    patch_bgr = frame[y1:y2, x1:x2]
    patch_hsv = cv2.cvtColor(patch_bgr, cv2.COLOR_BGR2HSV)
    flat = patch_hsv.reshape(-1, 3)
    return SampleData(
        x=cx,
        y=cy,
        h_vals=flat[:, 0].tolist(),
        s_vals=flat[:, 1].tolist(),
        v_vals=flat[:, 2].tolist(),
    )


def _draw_markers(frame: np.ndarray, samples: list[SampleData]) -> np.ndarray:
    """在帧上绘制采样点标记（带编号）。"""
    vis = frame.copy()
    for i, s in enumerate(samples):
        # 用采样中心像素的 HSV 还原为 BGR 作为标记颜色
        idx = len(s.h_vals) // 2  # 中间像素
        px_hsv = np.array([[[s.h_vals[idx], s.s_vals[idx], s.v_vals[idx]]]], dtype=np.uint8)
        px_bgr = cv2.cvtColor(px_hsv, cv2.COLOR_HSV2BGR)[0, 0]
        color = (int(px_bgr[0]), int(px_bgr[1]), int(px_bgr[2]))

        cv2.circle(vis, (s.x, s.y), 8, color, -1)
        cv2.circle(vis, (s.x, s.y), 9, (255, 255, 255), 1)
        # 编号
        label = str(i + 1)
        (tw, th), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.45, 1)
        cv2.putText(
            vis, label,
            (s.x - tw // 2, s.y + th // 2),
            cv2.FONT_HERSHEY_SIMPLEX, 0.45, (255, 255, 255), 2,
        )
    return vis


def _apply_mask(frame: np.ndarray, lo: np.ndarray, hi: np.ndarray) -> np.ndarray:
    hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
    return cv2.inRange(hsv, lo, hi)


def _hsv_range_str(lo: np.ndarray, hi: np.ndarray) -> str:
    return f"H:[{lo[0]}-{hi[0]}] S:[{lo[1]}-{hi[1]}] V:[{lo[2]}-{hi[2]}]"


def _normalize_range(lo: np.ndarray, hi: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """确保 lower <= upper。"""
    lo = lo.copy()
    hi = hi.copy()
    for i in range(3):
        if lo[i] > hi[i]:
            lo[i], hi[i] = hi[i], lo[i]
    return lo, hi


def on_mouse(event, x, y, _flags, param):
    state: CalibrationState = param
    if state.frozen_frame is None:
        return
    fw = state.frozen_frame.shape[1]

    if event == cv2.EVENT_LBUTTONDOWN and x < fw:
        # 左键点击游戏画面取色
        sd = _extract_patch(state.frozen_frame, x, y)
        state.samples.append(sd)
        lo, hi = state.compute_range()
        n = state.sample_count
        print(f"[取色 #{n}] ({x},{y}) "
              f"H: {min(sd.h_vals)}-{max(sd.h_vals)} "
              f"S: {min(sd.s_vals)}-{max(sd.s_vals)} "
              f"V: {min(sd.v_vals)}-{max(sd.v_vals)} => {_hsv_range_str(lo, hi)}")

    elif event == cv2.EVENT_RBUTTONDOWN:
        if state.samples:
            removed = state.samples.pop()
            lo, hi = state.compute_range()
            print(f"[撤销 #{len(state.samples)+1}] ({removed.x},{removed.y}) "
                  f"=> {_hsv_range_str(lo, hi)}")
        else:
            print("[无采样点可撤销]")


def on_trackbar_margin(val):
    """trackbar 回调，由主循环读取 state.margin_v。"""
    pass


def main():
    cfg = load_config(ROOT / "config.yaml")
    cap_cfg = cfg["capture"]
    det_cfg = cfg["detection"]

    capture = ScreenCapture(
        monitor_index=cap_cfg.get("monitor", 1),
        region_scale=cap_cfg.get("region_scale", 0.55),
    )

    state = CalibrationState()
    # 从当前配置加载初始 HSV 区间（供参考，不影响采样逻辑）
    init_lower = det_cfg.get("hsv_lower", [0, 0, 0])
    init_upper = det_cfg.get("hsv_upper", [179, 255, 255])

    cv2.namedWindow(WIN, cv2.WINDOW_NORMAL)
    cv2.resizeWindow(WIN, 1280, 480)
    cv2.createTrackbar("V margin", WIN, MARGIN_V_DEFAULT, 80, on_trackbar_margin)
    cv2.setMouseCallback(WIN, on_mouse, state)

    # 把窗口移到屏幕右侧
    try:
        hwnd = ctypes.windll.user32.FindWindowW(None, WIN)
        if hwnd:
            sw = ctypes.windll.user32.GetSystemMetrics(0)
            ctypes.windll.user32.MoveWindow(hwnd, max(0, sw - 1350), 40, 1320, 500, True)
    except Exception:
        pass

    print("=" * 55)
    print("颜色校准 — 多点取色")
    print("  F3 = 冻结画面（截图）  左键点击小球取色")
    print("  右键 = 撤销  滑块 = V容差  R = 重置")
    print("  空格 = 保存  Q = 退出")
    print(f"  初始配置: {_hsv_range_str(np.array(init_lower), np.array(init_upper))}")
    print("=" * 55)

    last_grab = 0.0
    last_f3_ts = 0.0  # F3 防抖

    while True:
        # --- 帧获取 ---
        if state.is_frozen:
            frame = state.frozen_frame
        else:
            now = time.perf_counter()
            if state.live_frame is None or now - last_grab > 0.033:
                state.live_frame = capture.grab_bgr()
                last_grab = now
            frame = state.live_frame
        if frame is None:
            cv2.waitKey(30)
            continue

        # --- 读取 V margin ---
        state.margin_v = cv2.getTrackbarPos("V margin", WIN)

        # --- 计算 HSV 区间与 mask ---
        lo, hi = state.compute_range()
        mask = _apply_mask(frame, lo, hi)
        white_pct = 100.0 * np.count_nonzero(mask) / mask.size if mask.size > 0 else 0.0

        # --- 绘制左侧面板：游戏帧 + 采样标记 ---
        if state.is_frozen and state.frozen_frame is not None:
            left = _draw_markers(state.frozen_frame, state.samples)
        else:
            left = frame.copy()

        # --- 绘制右侧面板：mask ---
        mask_bgr = cv2.cvtColor(mask, cv2.COLOR_GRAY2BGR)
        panel = np.hstack([left, mask_bgr])
        h_panel = panel.shape[0]

        # --- 状态栏 ---
        status = "FROZEN" if state.is_frozen else "LIVE"
        n = state.sample_count
        status_line = (f"{status} | Samples: {n} | "
                       f"{_hsv_range_str(lo, hi)} | "
                       f"mask: {white_pct:.1f}%")
        cv2.putText(panel, status_line, (8, 22),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 255, 255), 1)

        guide = "F3=freeze | LClick=sample | RClick=undo | R=reset | Space=save | Q=quit"
        cv2.putText(panel, guide, (8, h_panel - 8),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.4, (180, 180, 180), 1)

        # 分隔线 & 标签
        fw = left.shape[1]
        cv2.line(panel, (fw, 0), (fw, h_panel), (0, 255, 255), 2)
        cv2.putText(panel, "game", (10, 46),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.45, (180, 255, 180), 1)
        cv2.putText(panel, "mask", (fw + 10, 46),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.45, (180, 255, 180), 1)

        # 提示
        if not state.is_frozen:
            cv2.putText(panel, "Press F3 to freeze", (fw // 2 - 80, h_panel // 2),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 0), 2)
        elif n == 0:
            cv2.putText(panel, "Click ball on LEFT", (fw // 2 - 90, h_panel // 2),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 255), 2)

        cv2.imshow(WIN, panel)

        # --- 按键处理 ---
        # waitKey 处理 ASCII 键；F3 通过 GetAsyncKeyState 检测（避免 VK_F3=0x72='r' 冲突）
        key = cv2.waitKey(30) & 0xFF

        # F3: Windows API 直接读键状态，带 0.5s 防抖避免连触发
        f3_now = time.perf_counter()
        if (ctypes.windll.user32.GetAsyncKeyState(0x72) & 0x8000
                and f3_now - last_f3_ts > 0.5):
            last_f3_ts = f3_now
            state.is_frozen = True
            state.frozen_frame = capture.grab_bgr()
            state.live_frame = state.frozen_frame
            state.reset()
            print("[冻结] 画面已冻结，在小球上左键点击取色")

        if key == ord("q") or key == 27 or \
           cv2.getWindowProperty(WIN, cv2.WND_PROP_VISIBLE) < 1:
            break

        elif key == ord("r"):
            state.reset()
            print("[重置] 已清除全部采样点")

        elif key == ord("u"):
            state.is_frozen = False
            state.frozen_frame = None
            state.reset()
            print("[实时] 回到实时预览，采样已清除")

        elif key == ord(" "):
            lo, hi = state.compute_range()
            lo, hi = _normalize_range(lo, hi)
            cfg["detection"]["hsv_lower"] = lo.tolist()
            cfg["detection"]["hsv_upper"] = hi.tolist()
            path = ROOT / "config.yaml"
            with path.open("w", encoding="utf-8") as f:
                yaml.dump(cfg, f, allow_unicode=True, default_flow_style=False, sort_keys=False)
            print(f"[保存] lower={lo.tolist()} upper={hi.tolist()}")

    cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
