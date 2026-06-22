# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project overview

Aim Lab Vision is a Windows-only computer vision aimbot for Aim Lab. It captures the screen (monitor or specific window), detects target balls via HSV color masking, and moves the mouse using a virtual 1600 DPI one-shot aiming model with auto-click.

## Commands

```bash
# First-time setup (Python 3.12 required, 3.14 venv is known broken on Windows)
setup.bat

# Run the aimbot
run.bat                        # or: python -m src.main

# Calibrate HSV color range for target balls
calibrate.bat                  # or: python calibrate.py
```

## Architecture

The main loop (`src/main.py`) runs a strict sequential pipeline at a fixed 60Hz:

1. **Capture** — grab a BGR frame from the capture region
2. **Detect** — find the single target nearest to the capture window center
3. **Aim** — if crosshair is off target: compute pixel offset, convert to mouse counts, send one-shot `SendInput` move
4. **Verify & Shoot** — next frame: if crosshair is inside the target contour, fire one click. One shot per target area. Minimum 125ms between shots (configurable).

Detection always runs; aiming/shooting is toggled via hotkeys (default F8).

**Modules:**

- `src/capture.py` — Screen/window capture via dxcam (Desktop Duplication API). Two modes: `monitor` (capture a centered region of a display) and `window` (find a process by name, capture its client area). Exposes `window_width`/`window_height` for the game's render resolution.
- `src/detector.py` — `TargetDetector` converts BGR frames to HSV, applies morphological open/close, finds contours matching area bounds, and returns the single `Target` nearest to the frame center. Verifies crosshair-on-target via `crosshair_at_center()` using `pointPolygonTest`.
- `src/mouse_control.py` — `MouseController` uses `SendInput` for relative mouse movement (single-shot: pixel offsets → mouse counts → one call) and dual-method clicking (`mouse_event` + `SendInput`).
- `src/config_loader.py` — Thin YAML loader.
- `calibrate.py` — Interactive HSV calibration: freeze a frame (F3), click target balls to sample 9x9 pixel patches, computes min/max HSV range with configurable V margin, saves to `config.yaml`.

**Key design decisions:**

- **Fixed 60Hz sampling** — frame interval is locked to 1/60s regardless of monitor refresh rate.
- **Nearest-to-center only** — each frame independently picks the single target closest to the capture window center. No lock-on tracking, no target chaining.
- **One-shot aiming model** — pixel offset from crosshair to target center is converted to mouse input counts in one step and sent via a single `SendInput`. No micro-stepping, no speed curves.
- **Virtual 1600 DPI mouse** — `counts_per_360 = 1600 * (cm_per_360 / 2.54)`. At runtime, window resolution is read and `degrees_per_pixel_x/y` + `counts_per_pixel_x/y` are computed and saved to `config.yaml`.
- **FOV calculation** — Aim Lab horizontal FOV is 103°. Vertical FOV is derived from the aspect ratio: `vfov = 2 * atan(tan(hfov/2) * height/width)`.
- **Verify before shoot** — after a one-shot move, the next frame verifies the crosshair is inside the target contour (`pointPolygonTest >= 0`) before firing. Each target area is only shot once (tracked by position within 15px tolerance).
- **Shoot cooldown** — `shoot_interval_ms` (default 125ms) enforces minimum time between shots, configurable in `config.yaml`.

## Configuration

All runtime parameters live in `config.yaml` at the project root.

- `detection.hsv_lower` / `hsv_upper` — must be calibrated per user's lighting/display via `calibrate.py`
- `aim.dpi` (1600) and `aim.cm_per_360` (16.329) — define the virtual mouse
- `aim.fov` (103.0) — Aim Lab horizontal FOV
- `aim.shoot_interval_ms` (125) — minimum delay between shots
- `aim.degrees_per_pixel_x/y` and `aim.counts_per_pixel_x/y` — computed at runtime from resolution, DPI, and FOV; written back to config on each launch
