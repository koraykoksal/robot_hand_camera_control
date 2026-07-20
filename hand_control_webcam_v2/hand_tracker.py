# -*- coding: utf-8 -*-
"""
hand_tracker.py — v2

El takibi: MediaPipe HandLandmarker (Tasks API).
Kıvrım matematiği ve parametreleri, kullanıcının ÇALIŞAN uygulamasından
(hand_control_webcam / camera_hand_bridge.py) birebir alınmıştır:

  - 4 parmak : PIP eklem açısı, 175°(açık) -> 90°(kapalı)
  - başparmak bükme : IP eklem açısı, 160°(açık) -> 120°(kapalı)
  - başparmak rotasyon: bilekte UÇ(4)-işaretMCP(5) açısı, 45°(açık) -> 12°(kapalı)
  - ölü bölge uygulanıp kalan aralık YENİDEN ÖLÇEKLENİR (sıçrama olmaz)
  - One-Euro filtresi (başparmak için ayrı, daha durağan katsayı)
"""
import math
import os
import threading
import time
import urllib.request
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import cv2
import numpy as np
import mediapipe as mp
from mediapipe.tasks import python as mp_python
from mediapipe.tasks.python import vision

import settings as config

# (mcp, pip, tip) — açı PIP ekleminde ölçülür
FINGER_JOINTS = {
    "index":  (5, 6, 8),
    "middle": (9, 10, 12),
    "ring":   (13, 14, 16),
    "pinky":  (17, 18, 20),
}
THUMB_JOINTS = (2, 3, 4)          # başparmak IP eklemi
FINGER_ORDER = ["thumb", "index", "middle", "ring", "pinky"]


@dataclass
class HandResult:
    found: bool = False
    handedness: str = ""
    curls: Dict[str, float] = field(default_factory=dict)
    thumb_abduction: float = 0.0
    moving: Dict[str, bool] = field(default_factory=dict)
    px_landmarks: List[Tuple[int, int]] = field(default_factory=list)


class OneEuroFilter:
    """One-Euro: durağanken titremeyi keser, hareket ederken gecikme yapmaz."""
    def __init__(self, min_cutoff=1.0, beta=0.3, d_cutoff=1.0):
        self.min_cutoff, self.beta, self.d_cutoff = min_cutoff, beta, d_cutoff
        self.x_prev = None
        self.dx_prev = 0.0
        self.t_prev = None

    @staticmethod
    def _alpha(cutoff, dt):
        tau = 1.0 / (2 * math.pi * cutoff)
        return 1.0 / (1.0 + tau / dt)

    def __call__(self, x, t):
        if self.x_prev is None:
            self.x_prev, self.t_prev = x, t
            return x
        dt = max(t - self.t_prev, 1e-3)
        self.t_prev = t
        dx = (x - self.x_prev) / dt
        a_d = self._alpha(self.d_cutoff, dt)
        self.dx_prev = a_d * dx + (1 - a_d) * self.dx_prev
        cutoff = self.min_cutoff + self.beta * abs(self.dx_prev)
        a = self._alpha(cutoff, dt)
        self.x_prev = a * x + (1 - a) * self.x_prev
        return self.x_prev


# --------------------------------------------------------------------------
# Kıvrım matematiği (çalışan uygulamadan)
# --------------------------------------------------------------------------
def _angle_at(b, a, c) -> float:
    """b noktasındaki açı (derece); vektörler b->a ve b->c."""
    ba = np.array([a[0] - b[0], a[1] - b[1], a[2] - b[2]])
    bc = np.array([c[0] - b[0], c[1] - b[1], c[2] - b[2]])
    nba, nbc = np.linalg.norm(ba), np.linalg.norm(bc)
    if nba < 1e-6 or nbc < 1e-6:
        return 180.0
    cosang = float(np.clip(np.dot(ba, bc) / (nba * nbc), -1.0, 1.0))
    return math.degrees(math.acos(cosang))


def _apply_deadzone(t: float, dz: float) -> float:
    """Ölü bölge uygula ve KALANI YENİDEN ÖLÇEKLE (sıçrama olmasın)."""
    if dz <= 0.0:
        return t
    if t <= dz:
        return 0.0
    return (t - dz) / (1.0 - dz)


def _curl_from_angle(angle_deg: float, open_deg: float, closed_deg: float) -> float:
    if open_deg <= closed_deg:
        return 0.0
    t = (open_deg - angle_deg) / (open_deg - closed_deg)
    t = max(0.0, min(1.0, t))
    return _apply_deadzone(t, config.CURL_DEADZONE)


def _remap_clamp(val: float, lo: float, hi: float) -> float:
    if lo == hi:
        return 0.0
    return max(0.0, min(1.0, (val - lo) / (hi - lo)))


def _ensure_model() -> str:
    path = config.MODEL_PATH
    if os.path.isfile(path):
        return path
    os.makedirs(os.path.dirname(path), exist_ok=True)
    print(f"[MediaPipe] Model indiriliyor:\n  {config.MODEL_URL}")
    try:
        urllib.request.urlretrieve(config.MODEL_URL, path)
        print(f"[MediaPipe] Model indirildi: {path}")
    except Exception as e:
        raise RuntimeError(f"Model indirilemedi ({e}). Elle indirip koy: {path}")
    return path


class HandTracker:
    def __init__(self):
        model_path = _ensure_model()
        common = dict(
            base_options=mp_python.BaseOptions(model_asset_path=model_path),
            num_hands=2,                       # iki el kadrajdayken doğrusunu seçebilmek için
            min_hand_detection_confidence=config.MIN_DETECTION_CONFIDENCE,
            min_hand_presence_confidence=config.MIN_PRESENCE_CONFIDENCE,
            min_tracking_confidence=config.MIN_TRACKING_CONFIDENCE,
        )
        self.mode = getattr(config, "MEDIAPIPE_MODE", "LIVE_STREAM").upper()
        if self.mode == "LIVE_STREAM":
            opts = vision.HandLandmarkerOptions(
                running_mode=vision.RunningMode.LIVE_STREAM,
                result_callback=self._on_result, **common)
        else:
            opts = vision.HandLandmarkerOptions(
                running_mode=vision.RunningMode.VIDEO, **common)
        self.landmarker = vision.HandLandmarker.create_from_options(opts)
        print(f"[MediaPipe] {self.mode} modu.")

        self._lock = threading.Lock()
        self._latest = None
        self._seq = 0
        self._done_seq = -1
        self._cached = HandResult(found=False)

        # One-Euro (başparmak için ayrı katsayı — çalışan uygulamadaki gibi)
        self._oe = {}
        for f in FINGER_ORDER:
            mc = config.THUMB_MIN_CUTOFF if f == "thumb" else config.ONE_EURO_MIN_CUTOFF
            self._oe[f] = OneEuroFilter(mc, config.ONE_EURO_BETA)
        self._oe_rot = OneEuroFilter(config.THUMB_MIN_CUTOFF, config.ONE_EURO_BETA)

        self._prev = {f: 0.0 for f in FINGER_ORDER}
        self._t0 = time.time()
        self._last_ts = -1
        self.infer_ms = 0.0

    def close(self):
        self.landmarker.close()

    # kalibrasyon tuşları v2'de gerekmiyor (açı aralıkları sabit ve kanıtlanmış)
    def calibrate_open(self):
        print("[v2] Kalibrasyon gerekmiyor: açı aralıkları settings.py'de sabit "
              "(ANGLE_OPEN_DEG / ANGLE_CLOSED_DEG).")

    def calibrate_close(self):
        self.calibrate_open()

    def reset_calibration(self):
        self.calibrate_open()

    def _on_result(self, result, output_image, timestamp_ms):
        with self._lock:
            self._latest = result
            self._seq += 1
            now_ms = (time.time() - self._t0) * 1000.0
            self.infer_ms = max(0.0, now_ms - timestamp_ms)

    def _next_ts(self) -> int:
        ts = int((time.time() - self._t0) * 1000)
        if ts <= self._last_ts:
            ts = self._last_ts + 1
        self._last_ts = ts
        return ts

    def process(self, frame_bgr, target_hand: str, mirror: bool = False) -> HandResult:
        h, w = frame_bgr.shape[:2]
        iw = getattr(config, "INFER_WIDTH", 0)
        small = frame_bgr
        if iw and w > iw:
            small = cv2.resize(frame_bgr, (iw, int(h * iw / w)), interpolation=cv2.INTER_AREA)
        rgb = cv2.cvtColor(small, cv2.COLOR_BGR2RGB)
        img = mp.Image(image_format=mp.ImageFormat.SRGB, data=np.ascontiguousarray(rgb))
        ts = self._next_ts()

        if self.mode == "LIVE_STREAM":
            self.landmarker.detect_async(img, ts)
            with self._lock:
                res, seq = self._latest, self._seq
            if seq == self._done_seq or res is None:
                return self._cached
            self._done_seq = seq
            self._cached = self._build(res, h, w, target_hand, mirror)
            return self._cached
        t0 = time.perf_counter()
        res = self.landmarker.detect_for_video(img, ts)
        self.infer_ms = (time.perf_counter() - t0) * 1000.0
        return self._build(res, h, w, target_hand, mirror)

    def _build(self, res, h, w, target_hand, mirror) -> HandResult:
        if not res.hand_landmarks or not res.handedness:
            return HandResult(found=False)

        mp_target = target_hand
        if mirror:
            mp_target = "Left" if target_hand == "Right" else "Right"

        best, best_score = None, -1.0
        for i, hd in enumerate(res.handedness):
            cat = hd[0]
            if cat.category_name == mp_target and cat.score > best_score:
                best, best_score = i, cat.score
        if best is None:
            other = res.handedness[0][0].category_name
            if mirror:
                other = "Left" if other == "Right" else "Right"
            return HandResult(found=False, handedness=other)

        img_lms = res.hand_landmarks[best]
        lm = [(p.x, p.y, p.z) for p in img_lms]

        curls = {}
        # 4 parmak: PIP açısı
        for name, (mcp, pip, tip) in FINGER_JOINTS.items():
            ang = _angle_at(lm[pip], lm[mcp], lm[tip])
            curls[name] = _curl_from_angle(ang, config.ANGLE_OPEN_DEG, config.ANGLE_CLOSED_DEG)
        # başparmak bükme: IP açısı
        t_mcp, t_ip, t_tip = THUMB_JOINTS
        t_ang = _angle_at(lm[t_ip], lm[t_mcp], lm[t_tip])
        curls["thumb"] = _curl_from_angle(t_ang, config.THUMB_OPEN_DEG, config.THUMB_CLOSED_DEG)
        # başparmak rotasyon: bilekte UÇ(4) - işaretMCP(5) açısı
        abd_ang = _angle_at(lm[0], lm[4], lm[5])
        thumb_rot = _apply_deadzone(
            _remap_clamp(abd_ang, config.ABDUCT_LO_DEG, config.ABDUCT_HI_DEG),
            config.ABDUCT_DEADZONE)

        now = time.time()
        moving = {}
        out = {}
        for f in FINGER_ORDER:
            v = float(np.clip(self._oe[f](curls[f], now), 0.0, 1.0))
            out[f] = v
            moving[f] = abs(v - self._prev[f]) > config.MOVE_THRESHOLD
            self._prev[f] = v
        rot = float(np.clip(self._oe_rot(thumb_rot, now), 0.0, 1.0))

        px = [(int(p.x * w), int(p.y * h)) for p in img_lms]
        return HandResult(found=True, handedness=target_hand, curls=out,
                          thumb_abduction=rot, moving=moving, px_landmarks=px)
