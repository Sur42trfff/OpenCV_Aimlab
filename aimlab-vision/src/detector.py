"""基于 HSV 的目标球检测，每帧仅返回距捕获窗口中心最近的一个目标。"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import cv2
import numpy as np


@dataclass
class Target:
    x: int
    y: int
    area: float
    distance_to_center: float
    contour: np.ndarray = field(default_factory=lambda: np.array([]), compare=False, repr=False)


@dataclass
class DetectResult:
    target: Target | None
    candidates: list[Target]
    mask: np.ndarray


class TargetDetector:
    def __init__(
        self,
        hsv_lower: list[int],
        hsv_upper: list[int],
        min_area: int = 80,
        max_area: int = 12000,
        morph_kernel: int = 5,
        edge_margin: int = 40,
        target_circle_radius: int = 12,
        use_dynamic_radius: bool = True,
        center_shoot_radius: float = 7.0,
    ):
        lo = np.array(hsv_lower, dtype=np.uint8)
        hi = np.array(hsv_upper, dtype=np.uint8)
        for i in range(3):
            if lo[i] > hi[i]:
                lo[i], hi[i] = hi[i], lo[i]
        self._lower = lo
        self._upper = hi
        self._min_area = min_area
        self._max_area = max_area
        self._edge_margin = edge_margin
        self._circle_r = target_circle_radius
        self._dynamic_radius = use_dynamic_radius
        self._center_shoot_r = center_shoot_radius
        k = max(3, morph_kernel | 1)
        self._kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k))

    def circle_radius(self, target: Target) -> int:
        if not self._dynamic_radius:
            return self._circle_r
        from_area = int(math.sqrt(target.area / math.pi))
        return max(self._circle_r, min(from_area, 32))

    def crosshair_at_center(self, target: Target, frame_w: int, frame_h: int) -> bool:
        """准星是否位于目标判定区域内（距目标中心的像素距离 <= center_shoot_radius）。"""
        cx, cy = frame_w // 2, frame_h // 2
        dist = math.hypot(target.x - cx, target.y - cy)
        return dist <= self._center_shoot_r

    def _find_candidates(self, mask: np.ndarray) -> list[Target]:
        h, w = mask.shape[:2]
        cx, cy = w // 2, h // 2
        em = self._edge_margin
        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        candidates: list[Target] = []

        for cnt in contours:
            area = cv2.contourArea(cnt)
            if area < self._min_area or area > self._max_area:
                continue
            m = cv2.moments(cnt)
            if m["m00"] == 0:
                continue
            tx = int(m["m10"] / m["m00"])
            ty = int(m["m01"] / m["m00"])
            if tx < em or ty < em or tx >= w - em or ty >= h - em:
                continue
            dist_center = float(np.hypot(tx - cx, ty - cy))
            candidates.append(Target(tx, ty, area, dist_center, contour=cnt))

        return candidates

    @staticmethod
    def nearest_to_point(candidates: list[Target], x: int, y: int) -> Target | None:
        if not candidates:
            return None
        return min(candidates, key=lambda t: (t.x - x) ** 2 + (t.y - y) ** 2)

    def detect(self, frame_bgr: np.ndarray) -> DetectResult:
        h, w = frame_bgr.shape[:2]
        hsv = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2HSV)
        mask = cv2.inRange(hsv, self._lower, self._upper)
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, self._kernel)
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, self._kernel)

        em = self._edge_margin
        if em > 0:
            mask[:em, :] = 0
            mask[h - em:, :] = 0
            mask[:, :em] = 0
            mask[:, w - em:] = 0

        candidates = self._find_candidates(mask)
        target = None
        if candidates:
            target = min(candidates, key=lambda t: t.distance_to_center)
        return DetectResult(target, candidates, mask)

    def draw_debug(
        self,
        frame_bgr: np.ndarray,
        result: DetectResult,
        shoot_ready: bool = False,
        in_cooldown: bool = False,
    ) -> np.ndarray:
        vis = frame_bgr.copy()
        h, w = vis.shape[:2]
        cx, cy = w // 2, h // 2
        target = result.target
        candidates = result.candidates

        cv2.drawMarker(vis, (cx, cy), (0, 255, 255), cv2.MARKER_CROSS, 18, 2)

        for c in candidates:
            if target and c.x == target.x and c.y == target.y:
                continue
            cv2.circle(vis, (c.x, c.y), 6, (120, 120, 120), 1)

        if target:
            color = (0, 255, 0)
            r = self.circle_radius(target)
            cv2.circle(vis, (target.x, target.y), r, color, 2)
            cr = int(self._center_shoot_r)
            cv2.drawMarker(vis, (target.x, target.y), (0, 255, 255), cv2.MARKER_CROSS, 8, 1)
            cv2.circle(vis, (target.x, target.y), cr, (0, 255, 255), 1)
            at_ctr = self.crosshair_at_center(target, w, h)
            if shoot_ready:
                cv2.circle(vis, (cx, cy), 5, (0, 255, 255), -1)
                cv2.putText(vis, "FIRE", (cx + 8, cy - 8), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 255), 2)
            elif at_ctr and in_cooldown:
                cv2.putText(vis, "CD", (cx + 8, cy - 8), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 165, 255), 2)
            cv2.line(vis, (cx, cy), (target.x, target.y), color, 2)
            label = f"d={target.distance_to_center:.0f} n={len(candidates)}"
            cv2.putText(vis, label, (target.x + 14, target.y - 10),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.45, color, 1, cv2.LINE_AA)
        else:
            cv2.putText(vis, "no target", (8, 24), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 255), 2)

        mask_bgr = cv2.cvtColor(result.mask, cv2.COLOR_GRAY2BGR)
        return np.hstack([vis, mask_bgr])
