# -*- coding: utf-8 -*-
"""
hand_tracker_cv.py — SAF OpenCV el takibi (MediaPipe YOK).

Boru hattı:
  ROI kırp -> ten rengi maskesi (YCrCb) -> morfoloji -> en büyük kontur (el)
  -> distance transform ile AVUÇ MERKEZİ + YARIÇAPI
  -> bilek yönünden el yönelimi
  -> her parmak için AÇISAL SEKTÖR içindeki en uzak kontur noktası = parmak uzunluğu
  -> uzunluk / avuç yarıçapı -> kıvrım (0=açık, 1=kapalı)

Kalibrasyon:
  'o' : el AÇIKKEN bas  -> parmak sektör açıları + açık uzunluklar öğrenilir
  'c' : YUMRUK yapıp bas -> kapalı uzunluklar öğrenilir
  'k' : ten rengi öğren (avuç ortasından örnek alır)
"""
import json
import os
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import cv2
import numpy as np

import settings as config

FINGER_ORDER = ["thumb", "index", "middle", "ring", "pinky"]


class OneEuro:
    def __init__(self, min_cutoff=1.0, beta=0.0, d_cutoff=1.0):
        self.min_cutoff, self.beta, self.d_cutoff = min_cutoff, beta, d_cutoff
        self.x_prev = None
        self.dx_prev = 0.0
        self.t_prev = None

    @staticmethod
    def _alpha(cutoff, dt):
        tau = 1.0 / (2 * np.pi * cutoff)
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


@dataclass
class HandResult:
    found: bool = False
    handedness: str = ""
    curls: Dict[str, float] = field(default_factory=dict)
    thumb_abduction: float = 0.0
    moving: Dict[str, bool] = field(default_factory=dict)
    px_landmarks: List[Tuple[int, int]] = field(default_factory=list)
    # OpenCV'ye özel çizim verileri
    contour: Optional[np.ndarray] = None
    palm_center: Optional[Tuple[int, int]] = None
    palm_radius: float = 0.0
    finger_tips: Dict[str, Tuple[int, int]] = field(default_factory=dict)
    mask: Optional[np.ndarray] = None
    roi: Tuple[int, int, int, int] = (0, 0, 0, 0)


def _roi_rect(w: int, h: int) -> Tuple[int, int, int, int]:
    rx, ry, rw, rh = config.ROI_REL
    x, y = int(rx * w), int(ry * h)
    return x, y, int(rw * w), int(rh * h)


class CvHandTracker:
    def __init__(self):
        self.skin_cr = tuple(config.SKIN_CR)
        self.skin_cb = tuple(config.SKIN_CB)

        # Kalibrasyon: parmak sektör açıları (el yönelimine göre bağıl, derece)
        # ve açık/kapalı uzunluk oranları (avuç yarıçapı katı)
        self.sector_deg: Dict[str, float] = {
            # varsayılan: parmaklar yukarı; başparmak yana açık
            "thumb": -62.0, "index": -26.0, "middle": -3.0,
            "ring": 20.0, "pinky": 43.0,
        }
        self.open_len: Dict[str, float] = {f: config.FINGER_MAX_R for f in FINGER_ORDER}
        self.close_len: Dict[str, float] = {f: config.FINGER_MIN_R for f in FINGER_ORDER}
        self._load_calibration()

        self._oe = {f: OneEuro(config.OE_MIN_CUTOFF, config.OE_BETA) for f in FINGER_ORDER}
        self._smoothed = {f: 0.0 for f in FINGER_ORDER}
        self._prev = {f: 0.0 for f in FINGER_ORDER}
        self._last_len: Dict[str, float] = {f: 0.0 for f in FINGER_ORDER}
        self._last_orient = None
        self.infer_ms = 0.0
        self._last_frame_bgr = None

    def close(self):
        pass

    # ---------------- kalibrasyon ----------------
    def _load_calibration(self):
        try:
            if os.path.isfile(config.CV_CALIB_PATH):
                d = json.load(open(config.CV_CALIB_PATH, encoding="utf-8"))
                self.sector_deg.update(d.get("sector", {}))
                self.open_len.update(d.get("open_len", {}))
                self.close_len.update(d.get("close_len", {}))
                sk = d.get("skin")
                if sk:
                    self.skin_cr, self.skin_cb = tuple(sk[0]), tuple(sk[1])
                print(f"[CV Kalibrasyon] Yüklendi: {config.CV_CALIB_PATH}")
        except Exception as e:
            print(f"[CV Kalibrasyon] Yüklenemedi: {e}")

    def _save_calibration(self):
        try:
            os.makedirs(os.path.dirname(config.CV_CALIB_PATH), exist_ok=True)
            json.dump({"sector": self.sector_deg, "open_len": self.open_len,
                       "close_len": self.close_len,
                       "skin": [list(self.skin_cr), list(self.skin_cb)]},
                      open(config.CV_CALIB_PATH, "w", encoding="utf-8"))
        except Exception as e:
            print(f"[CV Kalibrasyon] Kaydedilemedi: {e}")

    def calibrate_skin(self):
        """Avuç merkezinden ten rengi örnekle ('k')."""
        if self._last_frame_bgr is None:
            print("[CV] Ten rengi için kare yok.")
            return
        frame = self._last_frame_bgr
        h, w = frame.shape[:2]
        x, y, rw, rh = _roi_rect(w, h)
        cx, cy = x + rw // 2, y + rh // 2
        patch = frame[max(0, cy - 25):cy + 25, max(0, cx - 25):cx + 25]
        if patch.size == 0:
            return
        ycrcb = cv2.cvtColor(patch, cv2.COLOR_BGR2YCrCb)
        cr, cb = ycrcb[:, :, 1].astype(float), ycrcb[:, :, 2].astype(float)
        t = config.SKIN_LEARN_TOL
        self.skin_cr = (int(cr.mean() - t * cr.std() - 5), int(cr.mean() + t * cr.std() + 5))
        self.skin_cb = (int(cb.mean() - t * cb.std() - 5), int(cb.mean() + t * cb.std() + 5))
        self._save_calibration()
        print(f"[CV] Ten rengi öğrenildi Cr={self.skin_cr} Cb={self.skin_cb} "
              "(elini ROI ortasına koyup bastığından emin ol)")

    def calibrate_open(self):
        """El AÇIKKEN: parmak sektörlerini ve açık uzunlukları öğren ('o')."""
        info = self._last_geometry
        if not info:
            print("[CV] El bulunamadı; açık kalibrasyon yapılamadı.")
            return
        tips = info["tips_sorted"]     # [(rel_angle_deg, length_ratio), ...] açıya göre
        if len(tips) < 4:
            print(f"[CV] Sadece {len(tips)} parmak ucu görüldü; elini tam aç ve tekrar dene.")
            return
        names = FINGER_ORDER if len(tips) >= 5 else FINGER_ORDER[1:]
        for name, (ang, ln) in zip(names, tips[:len(names)]):
            self.sector_deg[name] = float(ang)
            self.open_len[name] = float(ln)
        self._save_calibration()
        print("[CV] AÇIK el kalibrasyonu:",
              {k: (round(self.sector_deg[k], 1), round(self.open_len[k], 2)) for k in names})

    def calibrate_close(self):
        """YUMRUK yapıp bas ('c'): kapalı uzunlukları öğren."""
        if not self._last_len:
            print("[CV] El bulunamadı.")
            return
        for f in FINGER_ORDER:
            if self._last_len.get(f, 0) > 0:
                self.close_len[f] = float(self._last_len[f])
        self._save_calibration()
        print("[CV] KAPALI el kalibrasyonu:",
              {k: round(v, 2) for k, v in self.close_len.items()})

    def reset_calibration(self):
        self.open_len = {f: config.FINGER_MAX_R for f in FINGER_ORDER}
        self.close_len = {f: config.FINGER_MIN_R for f in FINGER_ORDER}
        try:
            if os.path.isfile(config.CV_CALIB_PATH):
                os.remove(config.CV_CALIB_PATH)
        except Exception:
            pass
        print("[CV Kalibrasyon] Sıfırlandı.")

    # ---------------- ana işlem ----------------
    _last_geometry = None

    def _skin_mask(self, roi_bgr: np.ndarray) -> np.ndarray:
        blur = cv2.GaussianBlur(roi_bgr, (config.BLUR_KERNEL, config.BLUR_KERNEL), 0)
        ycrcb = cv2.cvtColor(blur, cv2.COLOR_BGR2YCrCb)
        lo = np.array([0, self.skin_cr[0], self.skin_cb[0]], np.uint8)
        hi = np.array([255, self.skin_cr[1], self.skin_cb[1]], np.uint8)
        mask = cv2.inRange(ycrcb, lo, hi)
        k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE,
                                      (config.MORPH_KERNEL, config.MORPH_KERNEL))
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, k, iterations=2)
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, k, iterations=2)
        mask = cv2.medianBlur(mask, 5)
        return mask

    def process(self, frame_bgr: np.ndarray, target_hand: str,
                mirror: bool = False) -> HandResult:
        t0 = time.perf_counter()
        self._last_frame_bgr = frame_bgr
        h, w = frame_bgr.shape[:2]
        rx, ry, rw, rh = _roi_rect(w, h)
        roi = frame_bgr[ry:ry + rh, rx:rx + rw]
        if roi.size == 0:
            return HandResult(found=False, roi=(rx, ry, rw, rh))

        mask = self._skin_mask(roi)
        cnts, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        if not cnts:
            self._last_geometry = None
            return HandResult(found=False, mask=mask, roi=(rx, ry, rw, rh))

        # EL DIŞINDAKİ NESNELERİ ELE: her konturu el-benzerlik testinden geçir,
        # sadece geçenler arasından en iyisini seç (en büyüğü değil).
        cnt = self._pick_hand(cnts, rw, rh)
        if cnt is None:
            self._last_geometry = None
            return HandResult(found=False, mask=mask, roi=(rx, ry, rw, rh))

        # Avuç merkezi ve yarıçapı: distance transform'un tepe noktası
        filled = np.zeros(mask.shape, np.uint8)
        cv2.drawContours(filled, [cnt], -1, 255, -1)
        dist = cv2.distanceTransform(filled, cv2.DIST_L2, 5)
        _, palm_r, _, palm_c = cv2.minMaxLoc(dist)
        if palm_r < 8:
            return HandResult(found=False, mask=mask, roi=(rx, ry, rw, rh))
        pc = np.array(palm_c, dtype=np.float32)

        pts = cnt.reshape(-1, 2).astype(np.float32)
        vec = pts - pc
        radii = np.linalg.norm(vec, axis=1) / palm_r          # avuç yarıçapı katı
        angles = np.degrees(np.arctan2(vec[:, 1], vec[:, 0]))  # -180..180

        # El yönelimi: bilek = avucun altındaki kütle yönü.
        # Parmaklar bileğin TAM TERSİNE bakar -> temel yön = wrist_ang - 180.
        lower = vec[:, 1] > 0
        if lower.sum() > 10:
            wrist_ang = float(np.degrees(np.arctan2(
                np.median(vec[lower][:, 1]), np.median(vec[lower][:, 0]))))
        else:
            wrist_ang = 90.0
        orient = self._wrap(wrist_ang - 180.0)   # 0 sektörü = parmakların baktığı yön
        if self._last_orient is None:
            self._last_orient = orient
        else:
            # açısal yumuşatma (sarma güvenli)
            d = self._wrap(orient - self._last_orient)
            self._last_orient = self._wrap(self._last_orient + 0.25 * d)
        orient = self._last_orient

        # Parmak uçları (kalibrasyon için): dışbükey gövde tepe noktaları
        tips_sorted = self._detect_tips(pts, pc, palm_r, radii, angles, orient)

        # Her parmak: kendi sektöründeki en uzak kontur noktası
        curls, tips_px, lens = {}, {}, {}
        for f in FINGER_ORDER:
            target = self._wrap(self.sector_deg[f] + orient)
            d = np.abs(self._wrap_arr(angles - target))
            sel = d <= config.SECTOR_HALF_DEG
            if not sel.any():
                lens[f] = self.close_len.get(f, config.FINGER_MIN_R)
                curls[f] = 1.0
                continue
            idx = np.argmax(radii * sel)
            ln = float(radii[idx])
            lens[f] = ln
            tips_px[f] = (int(pts[idx][0]) + rx, int(pts[idx][1]) + ry)
            curls[f] = self._len_to_curl(f, ln)

        self._last_len = lens
        self._last_geometry = {"tips_sorted": tips_sorted}

        # yumuşatma + hareket
        now = time.time()
        moving = {}
        for f in FINGER_ORDER:
            v = float(np.clip(self._oe[f](curls[f], now), 0.0, 1.0))
            self._smoothed[f] = v
            moving[f] = abs(v - self._prev[f]) > config.MOVE_THRESHOLD
            self._prev[f] = v

        # baş parmak yana açılma: baş parmak ile işaret sektörü arası fark
        abd = float(np.clip(
            abs(self._wrap(self.sector_deg["thumb"] - self.sector_deg["index"])) / 45.0,
            0.0, 1.0))

        self.infer_ms = (time.perf_counter() - t0) * 1000.0
        cnt_shift = cnt.copy()
        cnt_shift[:, :, 0] += rx
        cnt_shift[:, :, 1] += ry
        return HandResult(
            found=True, handedness=target_hand,
            curls={f: self._smoothed[f] for f in FINGER_ORDER},
            thumb_abduction=abd, moving=moving,
            contour=cnt_shift,
            palm_center=(int(pc[0]) + rx, int(pc[1]) + ry),
            palm_radius=float(palm_r), finger_tips=tips_px,
            mask=mask, roi=(rx, ry, rw, rh),
        )

    def _pick_hand(self, cnts, roi_w: int, roi_h: int):
        """El olmayan nesneleri (yüz, kol, arka plan lekesi, mobilya) ele.

        Testler:
          - alan: çok küçük (gürültü) ve çok büyük (arka plan/duvar) olmasın
          - doluluk (solidity): el 0.35-0.95; yuvarlak/dolu leke (yüz) ~>0.95
          - en-boy oranı: aşırı uzun/ince şeritler (kol, kablo) elenir
          - ROI'yi neredeyse tamamen doldurmasın
        """
        roi_area = float(max(roi_w * roi_h, 1))
        best, best_score = None, -1.0
        for c in cnts:
            area = cv2.contourArea(c)
            if area < config.MIN_CONTOUR_AREA:
                continue
            if area > config.MAX_AREA_RATIO * roi_area:      # duvar/arka plan
                continue
            hull = cv2.convexHull(c)
            hull_area = cv2.contourArea(hull)
            if hull_area <= 1:
                continue
            solidity = area / hull_area
            if not (config.SOLIDITY_RANGE[0] <= solidity <= config.SOLIDITY_RANGE[1]):
                continue                                      # yüz gibi dolu leke elenir
            x, y, bw, bh = cv2.boundingRect(c)
            ar = bw / float(max(bh, 1))
            if not (config.ASPECT_RANGE[0] <= ar <= config.ASPECT_RANGE[1]):
                continue                                      # kol/şerit elenir
            if bw > 0.95 * roi_w and bh > 0.95 * roi_h:
                continue
            # Skor: el genelde orta doluluklu ve makul büyüklükte
            score = area / roi_area * (1.0 - abs(solidity - 0.65))
            if score > best_score:
                best, best_score = c, score
        return best

    # ---------------- yardımcılar ----------------
    @staticmethod
    def _wrap(a: float) -> float:
        return (a + 180.0) % 360.0 - 180.0

    @staticmethod
    def _wrap_arr(a: np.ndarray) -> np.ndarray:
        return (a + 180.0) % 360.0 - 180.0

    def _len_to_curl(self, f: str, ln: float) -> float:
        """Parmak uzunluğu (avuç yarıçapı katı) -> kıvrım 0(açık)..1(kapalı)."""
        op = self.open_len.get(f, config.FINGER_MAX_R)
        cl = self.close_len.get(f, config.FINGER_MIN_R)
        if op - cl < 0.15:            # kalibrasyon bozuksa varsayılan aralık
            op, cl = config.FINGER_MAX_R, config.FINGER_MIN_R
        v = (op - ln) / (op - cl)     # uzun = açık(0), kısa = kapalı(1)
        v = float(np.clip(v, 0.0, 1.0))
        return 0.0 if v < config.CURL_DEADZONE else v

    def _detect_tips(self, pts, pc, palm_r, radii, angles, orient):
        """Kalibrasyon için parmak uçlarını bul: yarıçapı büyük yerel tepe noktaları."""
        cand = np.where(radii > 1.6)[0]
        if len(cand) == 0:
            return []
        groups, used = [], np.zeros(len(pts), bool)
        order = cand[np.argsort(-radii[cand])]
        for i in order:
            if used[i]:
                continue
            d = np.abs(self._wrap_arr(angles - angles[i]))
            near = d < 12.0
            used |= near
            j = np.argmax(radii * near)
            groups.append((float(self._wrap(angles[j] - orient)), float(radii[j])))
            if len(groups) >= 6:
                break
        groups.sort(key=lambda g: g[0])     # açıya göre sırala (baş parmak -> serçe)
        return groups
