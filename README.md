# Aim Lab Vision

A Windows-only computer vision aimbot for [Aim Lab](https://aimlab.gg/). It captures the screen via the Desktop Duplication API, detects target balls using HSV color masking, and moves the mouse using a virtual 1600 DPI one-shot aiming model with auto-click.

> ⚠️ **Disclaimer:** This project is for educational and research purposes only. Using it in online games may violate the game's terms of service. Use at your own risk.

## Requirements

- **Windows** (uses `SendInput` / Win32 APIs)
- **Python 3.12** — Python 3.14's `venv` is known broken on Windows; 3.12 is recommended
- Aim Lab running in a window (not fullscreen exclusive)

## Quick start

### 1. Setup

```bash
setup.bat
```

This creates a `.venv` virtual environment (Python 3.12) and installs all dependencies:

- `dxcam` — screen capture via Desktop Duplication API
- `opencv-python` — HSV color masking & contour detection
- `numpy` — array math
- `PyYAML` — configuration parsing
- `pynput` — global hotkey listener

### 2. Calibrate HSV color range

Before using the aimbot, you must calibrate the target color range for your monitor's color profile and lighting conditions:

```bash
calibrate.bat
```

1. Aim Lab opens on screen with target balls visible
2. Press **F3** to freeze the current frame
3. Click on target balls to sample 9×9 pixel patches
4. The script computes min/max HSV range (with configurable V margin) and saves it to `config.yaml`

Repeat calibration if you change monitor, brightness, or room lighting.

### 3. Run

```bash
run.bat
```

The aimbot starts and waits for input. Make sure Aim Lab is running before you toggle.

## Hotkeys

| Key  | Action                                 |
| ---- | -------------------------------------- |
| `F8` | Toggle aiming & shooting on/off        |
| `F9` | Quit the program                       |

Default keys can be changed in `config.yaml` under `hotkeys`.

## How it works

The main loop runs a strict sequential pipeline at a fixed **60 Hz**:

1. **Capture** — grab a BGR frame from the target window or monitor region
2. **Detect** — convert to HSV, apply morphological open/close, find contours, select the single target **nearest to the frame center**
3. **Aim** — if the crosshair is off target, compute the pixel offset and convert it to mouse counts using the virtual DPI model; send one `SendInput` relative move
4. **Verify & Shoot** — on the **next** frame, check whether the crosshair is inside the target contour (`pointPolygonTest`); if so, fire one click. Each target area is only shot once (tracked by position within 15px tolerance). Minimum time between shots is enforced (default: 30ms).

### Virtual mouse model

- Assumes a **1600 DPI** virtual mouse
- Uses a fixed `cm_per_360` sensitivity value (default: 16.329 cm for a full 360° turn)
- At startup, reads the game window resolution and computes `degrees_per_pixel_x/y` and `counts_per_pixel_x/y`, then writes them back to `config.yaml`
- Aim Lab default horizontal FOV: **103°**; vertical FOV is derived from the aspect ratio

### One-shot aiming

Pixel offset from crosshair to target center is converted to mouse input counts in a single step and sent via one `SendInput` call. No micro-stepping, no speed curves, no PID loop — the next frame verifies and corrects if needed.

## Configuration

All parameters live in `config.yaml`:

```yaml
capture:
  mode: window              # "window" or "monitor"
  window_process: AimLab_tb.exe  # process name for window mode
  region_scale: 1.0         # scale factor for capture region (1.0 = native)
  monitor: 1                # monitor index for monitor mode

detection:
  hsv_lower: [136, 147, 139]  # calibrated via calibrate.py
  hsv_upper: [156, 223, 255]  # calibrated via calibrate.py
  min_area: 80              # minimum contour area (px²)
  max_area: 12000           # maximum contour area (px²)
  morph_kernel: 5           # morphological open/close kernel size
  edge_margin: 40           # ignore targets within N pixels of frame edges
  target_circle_radius: 12  # drawn target circle radius
  use_dynamic_radius: true  # scale radius based on target area
  center_shoot_radius: 15   # max pixels from contour center considered "same target"

aim:
  dpi: 1600                 # virtual mouse DPI
  cm_per_360: 16.329        # cm/360 sensitivity (measure in Aim Lab)
  fov: 103.0                # Aim Lab horizontal FOV
  shoot_interval_ms: 30     # minimum ms between shots

hotkeys:
  toggle: f8                # toggle aimbot on/off
  quit: f9                  # exit program

debug:
  show_window: false        # show debug overlay window
  window_scale: 1.0         # debug window scale

profiler:
  enabled: true             # log frame timing to CSV
  log_file: profiler_log.csv
  flush_interval_frames: 60
```

### Tuning `cm_per_360`

For accurate aiming, set `cm_per_360` to your actual cm/360 sensitivity. To measure it:

1. In Aim Lab, go to Settings → Controls → Sensitivity
2. Note your in-game sensitivity value
3. Multiply by your mouse's cm/360 at 1× sensitivity, or measure directly by doing a full 360° turn and measuring the physical mouse distance

## Project structure

```
aimlab-vision/
├── calibrate.py            # Interactive HSV calibration tool
├── calibrate.bat           # Launcher for calibration
├── config.yaml             # All runtime configuration
├── requirements.txt        # Python dependencies
├── run.bat                 # Launcher for the aimbot
├── setup.bat               # First-time setup script
└── src/
    ├── main.py             # Entry point & main loop
    ├── capture.py          # Screen/window capture (dxcam)
    ├── config_loader.py    # YAML config loader
    ├── detector.py         # HSV target detection
    ├── mouse_control.py    # SendInput mouse movement & clicking
    └── profiler.py         # Frame timing profiler
```
