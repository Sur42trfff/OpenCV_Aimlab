"""Frame pipeline profiler — accumulates per-stage timings and flushes to a log file."""

from __future__ import annotations

import csv
import time
from collections import defaultdict
from pathlib import Path


class FrameProfiler:
    def __init__(
        self,
        enabled: bool = False,
        log_path: Path | None = None,
        flush_interval_frames: int = 60,
    ):
        self.enabled = enabled
        self.log_path = log_path or Path("profiler_log.csv")
        self.flush_interval = flush_interval_frames
        self._records: list[dict[str, float]] = []
        self._frame_count = 0
        self._last_flush_count = 0
        self._header_written = False
        self._stage_names = [
            "capture",
            "detect",
            "aim",
            "debug_draw",
            "frame_align_sleep",
            "total",
        ]

    @property
    def frame_count(self) -> int:
        return self._frame_count

    def record(
        self,
        capture_ms: float,
        detect_ms: float,
        aim_ms: float,
        debug_draw_ms: float,
        frame_align_sleep_ms: float,
        total_ms: float,
    ) -> None:
        if not self.enabled:
            return
        self._records.append({
            "frame": self._frame_count,
            "capture_ms": capture_ms,
            "detect_ms": detect_ms,
            "aim_ms": aim_ms,
            "debug_draw_ms": debug_draw_ms,
            "frame_align_sleep_ms": frame_align_sleep_ms,
            "total_ms": total_ms,
        })
        self._frame_count += 1
        if self._frame_count - self._last_flush_count >= self.flush_interval:
            self.flush()

    def flush(self) -> None:
        if not self.enabled or not self._records:
            return
        mode = "a" if self._header_written else "w"
        fieldnames = ["frame"] + [f"{s}_ms" for s in self._stage_names]
        with open(self.log_path, mode, encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            if not self._header_written:
                writer.writeheader()
                self._header_written = True
            writer.writerows(self._records)
        self._records.clear()
        self._last_flush_count = self._frame_count

    def summary(self) -> str:
        """Compute rolling stats from all flushed+in-memory records (not implemented — reads file)."""
        if not self.log_path.exists():
            return "no data"
        with open(self.log_path, encoding="utf-8") as f:
            reader = csv.DictReader(f)
            rows = list(reader)
        if not rows:
            return "no data"
        stats: dict[str, list[float]] = defaultdict(list)
        for row in rows:
            for key, val in row.items():
                if key.endswith("_ms") and val:
                    stats[key].append(float(val))
        lines = []
        for name, values in sorted(stats.items()):
            if not values:
                continue
            avg = sum(values) / len(values)
            mx = max(values)
            mn = min(values)
            lines.append(f"  {name:>22s}: avg={avg:6.2f}  min={mn:6.2f}  max={mx:6.2f}  samples={len(values)}")
        return "\n".join(lines)
