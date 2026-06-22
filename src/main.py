"""
Aim Lab 视觉自动瞄准 — 主程序

虚拟 1600 DPI 鼠标，一步到位式瞄准，60Hz 固定采样率。
严格顺序执行：捕获 → 检测最近目标 → 计算像素距离 → 单次移动 →
捕获 → 验证准星位于目标内 → 开枪。
"""

from __future__ import annotations

import ctypes
import math
import sys
import time
from pathlib import Path

import cv2
import yaml
from pynput import keyboard

_winmm = ctypes.windll.winmm
_winmm.timeBeginPeriod(1)

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.capture import ScreenCapture
from src.config_loader import load_config
from src.detector import TargetDetector
from src.mouse_control import MouseController
from src.profiler import FrameProfiler

DEBUG_WIN = "AimLab Vision Debug"

HFOV = 103.0       # Aim Lab 水平视场角
CAPTURE_HZ = 60     # 固定采样率


def _compute_vfov(hfov_deg: float, width: int, height: int) -> float:
    hfov_rad = math.radians(hfov_deg)
    vfov_rad = 2.0 * math.atan(math.tan(hfov_rad / 2.0) * height / width)
    return math.degrees(vfov_rad)


def _compute_aim_params(
    window_w: int, window_h: int, dpi: int, cm_per_360: float, hfov: float, shoot_interval_ms: int
) -> dict:
    vfov = _compute_vfov(hfov, window_w, window_h)

    inches_per_360 = cm_per_360 / 2.54
    counts_per_360 = dpi * inches_per_360

    degrees_per_pixel_x = hfov / window_w
    degrees_per_pixel_y = vfov / window_h

    counts_per_pixel_x = counts_per_360 * hfov / (360.0 * window_w)
    counts_per_pixel_y = counts_per_360 * vfov / (360.0 * window_h)

    return {
        "dpi": dpi,
        "cm_per_360": cm_per_360,
        "fov": hfov,
        "vfov": round(vfov, 2),
        "shoot_interval_ms": shoot_interval_ms,
        "degrees_per_pixel_x": round(degrees_per_pixel_x, 6),
        "degrees_per_pixel_y": round(degrees_per_pixel_y, 6),
        "counts_per_pixel_x": round(counts_per_pixel_x, 6),
        "counts_per_pixel_y": round(counts_per_pixel_y, 6),
    }


def _save_aim_config(config_path: Path, aim_cfg: dict) -> None:
    cfg = load_config(config_path)
    cfg["aim"] = aim_cfg
    with config_path.open("w", encoding="utf-8") as f:
        yaml.dump(cfg, f, allow_unicode=True, default_flow_style=False, sort_keys=False)


class BotState:
    def __init__(self) -> None:
        self.aim_enabled = False
        self.running = True


def _move_debug_window_to_right() -> None:
    try:
        hwnd = ctypes.windll.user32.FindWindowW(None, DEBUG_WIN)
        if hwnd:
            sw = ctypes.windll.user32.GetSystemMetrics(0)
            ctypes.windll.user32.MoveWindow(hwnd, max(0, sw - 980), 40, 960, 520, True)
    except Exception:
        pass


def run() -> None:
    cfg = load_config(ROOT / "config.yaml")
    cap_cfg = cfg["capture"]
    det_cfg = cfg["detection"]
    aim_cfg = cfg["aim"]
    dbg_cfg = cfg["debug"]
    hotkeys = cfg["hotkeys"]
    prof_cfg = cfg.get("profiler", {})

    profiler = FrameProfiler(
        enabled=prof_cfg.get("enabled", False),
        log_path=ROOT / prof_cfg.get("log_file", "profiler_log.csv"),
        flush_interval_frames=prof_cfg.get("flush_interval_frames", 60),
    )

    state = BotState()
    toggle_key = hotkeys.get("toggle", "f8").lower()

    monitor_idx = cap_cfg.get("monitor", 1)
    capture = ScreenCapture(
        monitor_index=monitor_idx,
        region_scale=cap_cfg.get("region_scale", 0.55),
        capture_mode=cap_cfg.get("mode", "monitor"),
        window_process=cap_cfg.get("window_process", "AimLab_tb.exe"),
    )

    # ——— 读取窗口分辨率，计算瞄准参数，写入配置文件 ———
    dpi = aim_cfg.get("dpi", 1600)
    cm_per_360 = aim_cfg.get("cm_per_360", 16.329)
    hfov = aim_cfg.get("fov", HFOV)
    shoot_interval_ms = aim_cfg.get("shoot_interval_ms", 125)

    aim_params = _compute_aim_params(
        capture.window_width, capture.window_height,
        dpi, cm_per_360, hfov, shoot_interval_ms,
    )
    _save_aim_config(ROOT / "config.yaml", aim_params)

    cpp_x = aim_params["counts_per_pixel_x"]
    cpp_y = aim_params["counts_per_pixel_y"]

    detector = TargetDetector(
        hsv_lower=det_cfg["hsv_lower"],
        hsv_upper=det_cfg["hsv_upper"],
        min_area=det_cfg.get("min_area", 80),
        max_area=det_cfg.get("max_area", 12000),
        morph_kernel=det_cfg.get("morph_kernel", 5),
        edge_margin=det_cfg.get("edge_margin", 40),
        target_circle_radius=det_cfg.get("target_circle_radius", 12),
        use_dynamic_radius=det_cfg.get("use_dynamic_radius", True),
        center_shoot_radius=det_cfg.get("center_shoot_radius", 7.0),
    )

    shoot_interval = shoot_interval_ms / 1000.0
    mouse = MouseController()
    frame_interval = 1.0 / CAPTURE_HZ

    def on_press(key):
        try:
            name = key.name if hasattr(key, "name") else None
        except AttributeError:
            name = None
        if name == toggle_key:
            state.aim_enabled = not state.aim_enabled
            print(f"[{'AIM ON' if state.aim_enabled else 'AIM OFF'}]")
        elif name == hotkeys.get("quit", "f9").lower():
            state.running = False

    listener = keyboard.Listener(on_press=on_press)
    listener.start()

    show_debug = dbg_cfg.get("show_window", True)
    scale = dbg_cfg.get("window_scale", 1.0)
    debug_moved = False

    if show_debug:
        cv2.namedWindow(DEBUG_WIN, cv2.WINDOW_NORMAL)
        cv2.resizeWindow(DEBUG_WIN, capture.region.width * 2, capture.region.height)

    print("=" * 52)
    print("Aim Lab 视觉助手 (1600 DPI 一步到位)")
    print(f"  虚拟鼠标: {dpi} DPI  |  {cm_per_360} cm / 360°")
    print(f"  窗口分辨率: {capture.window_width}x{capture.window_height}")
    print(f"  FOV: {hfov}° H  |  {aim_params['vfov']}° V")
    print(f"  deg/px: {aim_params['degrees_per_pixel_x']:.6f} H")
    print(f"          {aim_params['degrees_per_pixel_y']:.6f} V")
    print(f"  cpp_x={cpp_x:.4f}  cpp_y={cpp_y:.4f}")
    print(f"  射击间隔: {shoot_interval_ms} ms  |  采样率: {CAPTURE_HZ}Hz")
    print(f"  {toggle_key.upper()} = 瞄准  |  F9 = 退出")
    print(f"  当前瞄准: {'ON' if state.aim_enabled else 'OFF'}")
    print("=" * 52)

    fps_t = time.perf_counter()
    frames = 0
    fps = 0.0

    last_shot_time = 0.0
    fired_positions: list[tuple[int, int]] = []
    locked_pos: tuple[int, int] | None = None
    LOCK_MAX_DIST = 150

    while state.running:
        frame_start = time.perf_counter()

        # ——— 1. 屏幕捕获 ———
        t0 = time.perf_counter()
        frame = capture.grab_bgr()
        t1 = time.perf_counter()

        # ——— 2. 发现目标（距捕获窗口中心最近的一个）———
        result = detector.detect(frame)
        target = result.target
        candidates = result.candidates
        t2 = time.perf_counter()

        frames += 1
        now = time.perf_counter()
        if now - fps_t >= 1.0:
            fps = frames / (now - fps_t)
            frames = 0
            fps_t = now

        # 无目标时清空射击记录与锁定
        if not candidates:
            fired_positions.clear()
            locked_pos = None

        shoot_ready = False
        aim_ms = 0.0
        in_cooldown = (time.perf_counter() - last_shot_time) < shoot_interval

        # ——— 3. 瞄准 + 射击 ———
        if state.aim_enabled and candidates:
            fw, fh = capture.region.width, capture.region.height
            cx, cy = fw // 2, fh // 2

            # 3a. 锁定目标：尝试在当前候选中匹配已锁定的目标
            target: Target | None = None
            if locked_pos is not None:
                matched = TargetDetector.nearest_to_point(candidates, *locked_pos)
                if matched is not None:
                    dist = math.hypot(matched.x - locked_pos[0], matched.y - locked_pos[1])
                    if dist <= LOCK_MAX_DIST:
                        target = matched
                        locked_pos = (target.x, target.y)
                    else:
                        locked_pos = None
                else:
                    locked_pos = None

            # 3b. 无锁定时，从未射击的候选中选择距中心最近的目标
            if locked_pos is None:
                fresh = [
                    c for c in candidates
                    if not any(abs(c.x - fx) < 15 and abs(c.y - fy) < 15 for fx, fy in fired_positions)
                ]
                if fresh:
                    target = min(fresh, key=lambda t: t.distance_to_center)
                    locked_pos = (target.x, target.y)

            # 3c. 瞄准 / 射击判定
            if target is not None:
                at_center = detector.crosshair_at_center(target, fw, fh)

                if at_center and not in_cooldown:
                    shoot_ready = True
                    mouse.click()
                    last_shot_time = time.perf_counter()
                    fired_positions.append((target.x, target.y))
                    if len(fired_positions) > 50:
                        fired_positions = fired_positions[-30:]
                    locked_pos = None  # 已开枪，释放锁定，下一帧寻找新目标
                elif not at_center:
                    # 计算距离 → 转化为鼠标输入 → 一次性移动
                    t_aim0 = time.perf_counter()
                    dx = target.x - cx
                    dy = target.y - cy
                    counts_x = int(round(dx * cpp_x))
                    counts_y = int(round(dy * cpp_y))
                    mouse.move_relative(counts_x, counts_y)
                    t_aim1 = time.perf_counter()
                    aim_ms = (t_aim1 - t_aim0) * 1000.0

        # ——— 调试显示 ———
        t3 = time.perf_counter()
        if show_debug:
            debug = detector.draw_debug(frame, result, shoot_ready=shoot_ready, in_cooldown=in_cooldown)
            aim_tag = "AIM ON" if state.aim_enabled else "AIM OFF"
            cv2.putText(
                debug,
                f"[{aim_tag}] FPS:{fps:.0f}  balls:{len(candidates)}  cpp_x={cpp_x:.3f}",
                (8, debug.shape[0] - 10),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.55,
                (0, 255, 255) if state.aim_enabled else (180, 180, 180),
                2,
            )
            if scale != 1.0:
                dw = int(debug.shape[1] * scale)
                dh = int(debug.shape[0] * scale)
                debug = cv2.resize(debug, (dw, dh), interpolation=cv2.INTER_AREA)
            cv2.imshow(DEBUG_WIN, debug)
            if not debug_moved:
                _move_debug_window_to_right()
                debug_moved = True
            if cv2.waitKey(1) & 0xFF == 27:
                break
        t4 = time.perf_counter()

        capture_ms = (t1 - t0) * 1000.0
        detect_ms = (t2 - t1) * 1000.0
        debug_draw_ms = (t4 - t3) * 1000.0

        # ——— 固定 60Hz 帧对齐 ———
        elapsed = time.perf_counter() - frame_start
        sleep_ms = 0.0
        if elapsed < frame_interval:
            time.sleep(frame_interval - elapsed)
            sleep_ms = (time.perf_counter() - t4) * 1000.0

        total_ms = (time.perf_counter() - frame_start) * 1000.0

        profiler.record(
            capture_ms=capture_ms,
            detect_ms=detect_ms,
            aim_ms=aim_ms,
            debug_draw_ms=debug_draw_ms,
            frame_align_sleep_ms=sleep_ms,
            total_ms=total_ms,
        )

    listener.stop()
    profiler.flush()
    if profiler.frame_count > 0:
        print(f"\n[性能] {profiler.frame_count} 帧数据已写入 {profiler.log_path}")
        print(profiler.summary())
    cv2.destroyAllWindows()
    _winmm.timeEndPeriod(1)
    print("已退出")


if __name__ == "__main__":
    run()
