# -*- coding: utf-8 -*-
"""
hand_tracker.py — Kamera karesinden sağ eli tespit eder, parmak kıvrımlarını
0..1 aralığında hesaplar, dış etkenlere karşı sağlamlaştırır ve yumuşatır.

MediaPipe Tasks (HandLandmarker) API'si. İki mod:
- LIVE_STREAM (önerilen): detect_async + callback; bloklamaz, akıcı, düşük gecikme.
- VIDEO: detect_for_video; her karede bekletir (yedek).
İlk çalıştırmada model (hand_landmarker.task) yoksa otomatik indirilir.
"""
import os
import time
import threading
import urllib.request
from dataclasses import dataclass, field
from collections import deque
from typing import Dict, List, Optional, Tuple

import numpy as np
import cv2
import mediapipe as mp
from mediapipe.tasks import python as mp_python
from mediapipe.tasks.python import vision

import settings as config

WRIST = 0
FINGER_LMS = {
    "index":  (5, 6, 7, 8),
    "middle": (9, 10, 11, 12),
    "ring":   (13, 14, 15, 16),
    "pinky":  (17, 18, 19, 20),
}
THUMB_LMS = (1, 2, 3, 4)
FINGER_ORDER = ["thumb", "index", "middle", "ring", "pinky"]


@dataclass
class HandResult:
    found: bool = False
    handedness: str = ""
    curls: Dict[str, float] = field(default_factory=dict)
    thumb_abduction: float = 0.0
    moving: Dict[str, bool] = field(default_factory=dict)
    px_landmarks: List[Tuple[int, int]] = field(default_factory=list)


def _angle(a: np.ndarray, b: np.ndarray, c: np.ndarray) -> float:
    v1, v2 = a - b, c - b
    n1, n2 = np.linalg.norm(v1), np.linalg.norm(v2)
    if n1 < 1e-6 or n2 < 1e-6:
        return 180.0
    cosang = np.clip(np.dot(v1, v2) / (n1 * n2), -1.0, 1.0)
    return float(np.degrees(np.arccos(cosang)))


def _norm(value: float, lo: float, hi: float) -> float:
    return float(np.clip((value - lo) / (hi - lo), 0.0, 1.0))


class OneEuro:
    """One-Euro filtresi: yavaşta titremeyi keser, hızlıda gecikme yapmaz."""
    def __init__(self, min_cutoff=1.0, beta=0.0, d_cutoff=1.0):
        self.min_cutoff, self.beta, self.d_cutoff = min_cutoff, beta, d_cutoff
        self.x_prev = None
        self.dx_prev = 0.0
        self.t_prev = None

    @staticmethod
    def _alpha(cutoff, dt):
        tau = 1.0 / (2 * np.pi * cutoff)
        return 1.0 / (1.0 + tau / dt)

    def __call__(self, x: float, t: float) -> float:
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
        x_hat = a * x + (1 - a) * self.x_prev
        self.x_prev = x_hat
        return x_hat


def _ensure_model() -> str:
    path = config.MODEL_PATH
    if os.path.isfile(path):
        return path
    os.makedirs(os.path.dirname(path), exist_ok=True)
    print(f"[MediaPipe] Model bulunamadı, indiriliyor:\n  {config.MODEL_URL}")
    try:
        urllib.request.urlretrieve(config.MODEL_URL, path)
        print(f"[MediaPipe] Model indirildi: {path}")
    except Exception as e:
        raise RuntimeError(
            f"Model indirilemedi ({e}).\n"
            f"Elle indirip şu yola koy: {path}\nURL: {config.MODEL_URL}"
        )
    return path


class HandTracker:
    def __init__(self):
        model_path = _ensure_model()
        self.mode = getattr(config, "MEDIAPIPE_MODE", "LIVE_STREAM").upper()

        base = mp_python.BaseOptions(model_asset_path=model_path)
        common = dict(
            base_options=base,
            num_hands=getattr(config, "NUM_HANDS", 1),
            min_hand_detection_confidence=config.MIN_DETECTION_CONFIDENCE,
            min_hand_presence_confidence=config.MIN_PRESENCE_CONFIDENCE,
            min_tracking_confidence=config.MIN_TRACKING_CONFIDENCE,
        )

        if self.mode == "LIVE_STREAM":
            options = vision.HandLandmarkerOptions(
                running_mode=vision.RunningMode.LIVE_STREAM,
                result_callback=self._on_result, **common)
            print("[MediaPipe] LIVE_STREAM modu (async, akıcı).")
        else:
            options = vision.HandLandmarkerOptions(
                running_mode=vision.RunningMode.VIDEO, **common)
            print("[MediaPipe] VIDEO modu.")
        self.landmarker = vision.HandLandmarker.create_from_options(options)

        # LIVE_STREAM: callback'ten gelen son sonucu tut (thread-güvenli)
        self._lock = threading.Lock()
        self._latest_result = None
        self._latest_seq = 0
        self._processed_seq = -1
        self._cached = HandResult(found=False)

        # smoothing / hareket
        self._smoothed: Dict[str, float] = {f: 0.0 for f in FINGER_ORDER}
        self._smoothed_abd: float = 0.0
        self._prev: Dict[str, float] = {f: 0.0 for f in FINGER_ORDER}
        self._t0 = time.time()
        self._last_ts = -1
        # One-Euro filtreleri (parmak başına)
        self._oe = {f: OneEuro(config.OE_MIN_CUTOFF, config.OE_BETA) for f in FINGER_ORDER}
        self._oe_abd = OneEuro(config.OE_MIN_CUTOFF, config.OE_BETA)
        # performans ölçümü
        self.infer_ms = 0.0

        # kalibrasyon
        self._last_raw: Dict[str, float] = {f: 0.0 for f in FINGER_ORDER}
        self.open_ref: Dict[str, float] = {f: 0.0 for f in FINGER_ORDER}
        self.close_ref: Dict[str, float] = {f: 1.0 for f in FINGER_ORDER}
        self._has_calib = self._load_calibration()
        self._auto_zero = not self._has_calib
        self._raw_hist: Dict[str, deque] = {f: deque(maxlen=15) for f in FINGER_ORDER}
        if self._auto_zero:
            self.open_ref = {f: 1.0 for f in FINGER_ORDER}

    def close(self):
        self.landmarker.close()

    # ---- kalibrasyon ----
    def _load_calibration(self) -> bool:
        try:
            if os.path.isfile(config.CALIB_PATH):
                import json
                d = json.load(open(config.CALIB_PATH, encoding="utf-8"))
                self.open_ref.update(d.get("open", {}))
                self.close_ref.update(d.get("close", {}))
                print(f"[Kalibrasyon] Yüklendi: {config.CALIB_PATH}")
                return True
        except Exception as e:
            print(f"[Kalibrasyon] Yüklenemedi: {e}")
        return False

    def _save_calibration(self):
        try:
            import json
            os.makedirs(os.path.dirname(config.CALIB_PATH), exist_ok=True)
            json.dump({"open": self.open_ref, "close": self.close_ref},
                      open(config.CALIB_PATH, "w", encoding="utf-8"))
        except Exception as e:
            print(f"[Kalibrasyon] Kaydedilemedi: {e}")

    def calibrate_open(self):
        self._auto_zero = False
        self.open_ref = dict(self._last_raw)
        self._save_calibration()
        print("[Kalibrasyon] AÇIK el (0) kaydedildi:",
              {k: round(v, 2) for k, v in self.open_ref.items()})

    def calibrate_close(self):
        self.close_ref = dict(self._last_raw)
        self._save_calibration()
        print("[Kalibrasyon] KAPALI el (1) kaydedildi:",
              {k: round(v, 2) for k, v in self.close_ref.items()})

    def reset_calibration(self):
        self._auto_zero = True
        self.open_ref = {f: 1.0 for f in FINGER_ORDER}
        self.close_ref = {f: 1.0 for f in FINGER_ORDER}
        self._raw_hist = {f: deque(maxlen=15) for f in FINGER_ORDER}
        try:
            if os.path.isfile(config.CALIB_PATH):
                os.remove(config.CALIB_PATH)
        except Exception:
            pass
        print("[Kalibrasyon] Sıfırlandı (otomatik sıfırlama tekrar aktif).")

    def _apply_calib(self, f: str, raw: float) -> float:
        """Ham kıvrım -> 0(açık)..1(kapalı).
        Açık-el referansının üstüne DİNLENME BANDI kadar çıkılmadıkça sonuç tam 0'dır.
        Böylece parmak tamamen açıkken robota giden pozisyon 0 olur; sadece
        parmak gerçekten kıvrıldıkça değer artar."""
        lo = self.open_ref.get(f, 0.0) + config.CURL_REST_BAND   # sıfır eşiği
        hi = self.close_ref.get(f, 1.0)
        if hi <= lo:                      # kalibrasyon dar/ters ise güvenli aralık
            hi = lo + 0.25
        v = float(np.clip((raw - lo) / (hi - lo), 0.0, 1.0))
        return 0.0 if v < config.CURL_DEADZONE else v

    # ---- MediaPipe ----
    def _on_result(self, result, output_image, timestamp_ms):
        """LIVE_STREAM callback (worker thread). Sadece sonucu sakla."""
        with self._lock:
            self._latest_result = result
            self._latest_seq += 1
            # kare damgası ile şimdi arasındaki fark ~ uçtan uca gecikme
            now_ms = (time.time() - self._t0) * 1000.0
            self.infer_ms = max(0.0, now_ms - timestamp_ms)

    def _next_ts(self) -> int:
        ts = int((time.time() - self._t0) * 1000)
        if ts <= self._last_ts:
            ts = self._last_ts + 1
        self._last_ts = ts
        return ts

    def process(self, frame_bgr: np.ndarray, target_hand: str,
                mirror: bool = False) -> HandResult:
        h, w = frame_bgr.shape[:2]

        # Çıkarım için kareyi küçült (hız). Landmark'lar normalize olduğu için
        # tam çözünürlüğe geri ölçeklemek sorunsuz.
        iw = getattr(config, "INFER_WIDTH", 0)
        small = frame_bgr
        if iw and w > iw:
            small = cv2.resize(frame_bgr, (iw, int(h * iw / w)), interpolation=cv2.INTER_AREA)

        rgb = cv2.cvtColor(small, cv2.COLOR_BGR2RGB)
        mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=np.ascontiguousarray(rgb))
        ts = self._next_ts()
        t_start = time.perf_counter()

        if self.mode == "LIVE_STREAM":
            # Bloklamaz; sonuç callback ile gelir.
            self.landmarker.detect_async(mp_image, ts)
            with self._lock:
                result = self._latest_result
                seq = self._latest_seq
            if seq == self._processed_seq:
                return self._cached          # yeni sonuç yok -> önceki
            self._processed_seq = seq
            if result is None:
                return self._cached
            self._cached = self._build_result(result, h, w, target_hand, mirror)
            return self._cached
        else:
            result = self.landmarker.detect_for_video(mp_image, ts)
            self.infer_ms = (time.perf_counter() - t_start) * 1000.0
            return self._build_result(result, h, w, target_hand, mirror)

    def _build_result(self, res, h, w, target_hand, mirror) -> HandResult:
        if not res.hand_landmarks or not res.handedness:
            return HandResult(found=False)

        mp_target = target_hand
        if mirror:
            mp_target = "Left" if target_hand == "Right" else "Right"

        def to_physical(lbl):
            if mirror:
                return "Left" if lbl == "Right" else "Right"
            return lbl

        best_idx, best_score = None, -1.0
        for i, handed in enumerate(res.handedness):
            cat = handed[0]
            if cat.category_name == mp_target and cat.score > best_score:
                best_idx, best_score = i, cat.score
        if best_idx is None:
            return HandResult(found=False,
                              handedness=to_physical(res.handedness[0][0].category_name))

        img_lms = res.hand_landmarks[best_idx]
        if res.hand_world_landmarks:
            world = res.hand_world_landmarks[best_idx]
            pts = np.array([[lm.x, lm.y, lm.z] for lm in world], dtype=np.float32)
        else:
            pts = np.array([[lm.x, lm.y, lm.z] for lm in img_lms], dtype=np.float32)

        raw_curls = self._compute_curls(pts)
        abduction = self._compute_thumb_abduction(pts)
        self._last_raw = dict(raw_curls)

        if self._auto_zero:
            for f in FINGER_ORDER:
                self._raw_hist[f].append(raw_curls[f])
                if len(self._raw_hist[f]) >= 10:
                    # 20. yüzdelik: tek gürültülü kareye karşı dayanıklı,
                    # yalnızca AŞAĞI iner (yumruk yapınca referans bozulmaz).
                    cand = float(np.percentile(self._raw_hist[f], 20))
                    if cand < self.open_ref[f]:
                        self.open_ref[f] = cand

        now = time.time()
        moving = {}
        use_oe = getattr(config, "USE_ONE_EURO", True)
        a = config.SMOOTHING
        for f in FINGER_ORDER:
            calibrated = self._apply_calib(f, raw_curls[f])
            if use_oe:
                val = self._oe[f](calibrated, now)
            else:
                val = a * self._smoothed[f] + (1 - a) * calibrated
            self._smoothed[f] = float(np.clip(val, 0.0, 1.0))
            delta = abs(self._smoothed[f] - self._prev[f])
            moving[f] = delta > config.MOVE_THRESHOLD
            self._prev[f] = self._smoothed[f]
        if use_oe:
            self._smoothed_abd = float(np.clip(self._oe_abd(abduction, now), 0.0, 1.0))
        else:
            self._smoothed_abd = a * self._smoothed_abd + (1 - a) * abduction

        px = [(int(lm.x * w), int(lm.y * h)) for lm in img_lms]
        return HandResult(
            found=True,
            handedness=target_hand,
            curls={f: self._smoothed[f] for f in FINGER_ORDER},
            thumb_abduction=self._smoothed_abd,
            moving=moving,
            px_landmarks=px,
        )

    def _compute_curls(self, p: np.ndarray) -> Dict[str, float]:
        curls: Dict[str, float] = {}
        for name, (mcp, pip, dip, tip) in FINGER_LMS.items():
            ang = _angle(p[mcp], p[pip], p[tip])
            curls[name] = 1.0 - _norm(ang, 55.0, 175.0)
        cmc, mcp, ip, tip = THUMB_LMS
        ang_thumb = _angle(p[mcp], p[ip], p[tip])
        curls["thumb"] = 1.0 - _norm(ang_thumb, 90.0, 178.0)
        return curls

    def _compute_thumb_abduction(self, p: np.ndarray) -> float:
        ang = _angle(p[THUMB_LMS[1]], p[WRIST], p[FINGER_LMS["index"][0]])
        return _norm(ang, 12.0, 55.0)
