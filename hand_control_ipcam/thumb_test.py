# -*- coding: utf-8 -*-
"""
thumb_test.py — Başparmak izolasyon / kalibrasyon testi
========================================================

Sadece BAŞPARMAĞI test eder ve robotu sadece başparmak motorlarıyla sürer:

    BasF = bükme   (yukarı-aşağı, flexion)   -> MOTOR 1
    BasR = rotasyon (sağa-sola, abduction)   -> MOTOR 2
    Motor 3-6 her zaman 0 (açık) gönderilir.

Ne işe yarar:
    1) Kameranın başparmak hareketini DOĞRU algıladığını doğrulamak
       (barlar + canlı ham açı değerleri)
    2) Kendi elinize özel kalibrasyon aralığını ÖLÇMEK:
       başparmağı uç pozisyonlara götürün; araç gördüğü min/max açıları
       kaydeder ve settings.py için ÖNERİLEN değerleri ekranda gösterir.
    3) Robotta motor1/motor2'nin doğru yönde hareket ettiğini test etmek.

Tuşlar:
    r = robot gönderimini AÇ/KAPAT (başlangıç: KAPALI — önce kamerayı doğrulayın)
    c = min/max açı istatistiklerini sıfırla
    q = çıkış

Çalıştırma:
    1) python run_api.py   (robot bağlı + enable)
    2) python thumb_test.py
"""

from __future__ import annotations

import os
import sys
import time
import math
import threading
from collections import deque

import cv2
import numpy as np
import mediapipe as mp
import requests

# ---------------------------------------------------------------------------
# config/ dizinini import yoluna ekle (camera_hand_bridge.py ile aynı yöntem)
# ---------------------------------------------------------------------------
_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_CONFIG_DIR = os.path.join(_THIS_DIR, "config")
if _CONFIG_DIR not in sys.path:
    sys.path.insert(0, _CONFIG_DIR)

try:
    import settings as _settings
except Exception as _e:
    _settings = None
    print(f"⚠️ settings.py okunamadı ({_e}); varsayılan ayarlar kullanılacak.")


def _get(name, default):
    return getattr(_settings, name, default) if _settings else default


# ===========================================================================
# Ayarlar (settings.py'den; yoksa varsayılan)
# ===========================================================================
CAMERA_INDEX = int(_get("CAMERA_INDEX", 0))
_BACKEND_STR = str(_get("CAMERA_BACKEND", "dshow")).lower()
CAMERA_BACKEND = cv2.CAP_DSHOW if _BACKEND_STR == "dshow" else cv2.CAP_ANY
CAMERA_WIDTH = int(_get("CAMERA_WIDTH", 640))
CAMERA_HEIGHT = int(_get("CAMERA_HEIGHT", 480))
CAMERA_WINDOW_MODE = str(_get("CAMERA_WINDOW_MODE", "static")).lower()

# Başparmak BÜKME kalibrasyonu (IP eklem açısı, derece)
THUMB_OPEN_DEG = float(_get("THUMB_OPEN_DEG", 160.0))    # düz başparmak -> %0
THUMB_CLOSED_DEG = float(_get("THUMB_CLOSED_DEG", 120.0))  # kıvrık -> %100

# Başparmak ROTASYON kalibrasyonu (bilek-başparmakUCU-işaretMCP açısı)
ABDUCT_LO_DEG = float(_get("ABDUCT_LO_DEG", 45.0))   # yayılmış/açık el -> %0
ABDUCT_HI_DEG = float(_get("ABDUCT_HI_DEG", 12.0))   # avuca kapanık -> %100

CURL_DEADZONE = float(_get("CURL_DEADZONE", 0.15))
ABDUCT_DEADZONE = float(_get("ABDUCT_DEADZONE", 0.12))

THUMB_MIN_CUTOFF = float(_get("THUMB_MIN_CUTOFF", 0.5))
ONE_EURO_BETA = float(_get("ONE_EURO_BETA", 0.3))

MIN_DETECTION_CONFIDENCE = float(_get("MIN_DETECTION_CONFIDENCE", 0.8))
MIN_TRACKING_CONFIDENCE = float(_get("MIN_TRACKING_CONFIDENCE", 0.6))
MIN_HAND_SIZE = float(_get("MIN_HAND_SIZE", 0.18))
DETECT_DEBOUNCE = int(_get("DETECT_DEBOUNCE", 3))

DEADBAND = int(_get("DEADBAND", 300))
SAFE_VELOCITY = int(_get("SAFE_VELOCITY", 8000))
SAFE_MAX_CURRENT = int(_get("SAFE_MAX_CURRENT", 400))

# Hangi el takip edilsin? "right" / "left" / "any" (köprüyle aynı ayar)
HAND_FILTER = str(_get("HAND_FILTER", "right")).lower()
if HAND_FILTER not in ("right", "left", "any"):
    HAND_FILTER = "any"
_HAND_TR = {"right": "SAG EL", "left": "SOL EL"}

MOTOR_MIN = int(_get("MOTOR_MIN", 0))
MOTOR_MAX = int(_get("MOTOR_MAX", 10000))

SERVER_URL = "http://127.0.0.1:8080"
MOVE_ENDPOINT = SERVER_URL + "/move"
HTTP_TIMEOUT_S = 0.5
SEND_HZ = 15.0
SEND_PERIOD_S = 1.0 / SEND_HZ

# MediaPipe landmark indexleri
WRIST = 0
T_CMC, T_MCP, T_IP, T_TIP = 1, 2, 3, 4
I_MCP = 5


# ===========================================================================
# Yardımcılar
# ===========================================================================
def _angle_at(v, a, b) -> float:
    """
    v köşesinde a ve b'ye giden vektörler arası açı (derece).
    3B hesaplanır (x,y,z) - camera_hand_bridge.py ile BİREBİR AYNI yöntem.
    (Aynı olmak zorunda: bu aracın ölçtüğü kalibrasyon değerleri köprüde kullanılıyor.)
    """
    ax, ay, az = a.x - v.x, a.y - v.y, a.z - v.z
    bx, by, bz = b.x - v.x, b.y - v.y, b.z - v.z
    na = math.sqrt(ax * ax + ay * ay + az * az)
    nb = math.sqrt(bx * bx + by * by + bz * bz)
    if na < 1e-6 or nb < 1e-6:
        return 180.0
    cosv = max(-1.0, min(1.0, (ax * bx + ay * by + az * bz) / (na * nb)))
    return math.degrees(math.acos(cosv))


def _apply_deadzone(t: float, dz: float) -> float:
    if dz <= 0.0:
        return t
    if t <= dz:
        return 0.0
    return (t - dz) / (1.0 - dz)


def _remap_clamp(val: float, lo: float, hi: float) -> float:
    if lo == hi:
        return 0.0
    t = (val - lo) / (hi - lo)
    return max(0.0, min(1.0, t))


def _hand_size_ratio(hand_lm, frame_shape) -> float:
    h, w = frame_shape[:2]
    xs = [p.x for p in hand_lm.landmark]
    ys = [p.y for p in hand_lm.landmark]
    bw = (max(xs) - min(xs)) * w
    bh = (max(ys) - min(ys)) * h
    return math.hypot(bw, bh) / math.hypot(w, h)


class OneEuroFilter:
    """Durağanda titremez, hızlı harekette gecikmez."""

    def __init__(self, freq, min_cutoff=1.0, beta=0.0, d_cutoff=1.0):
        self.freq = freq
        self.min_cutoff = min_cutoff
        self.beta = beta
        self.d_cutoff = d_cutoff
        self._x_prev = None
        self._dx_prev = 0.0
        self._t_prev = None

    @staticmethod
    def _alpha(cutoff, freq):
        tau = 1.0 / (2.0 * math.pi * cutoff)
        te = 1.0 / freq
        return 1.0 / (1.0 + tau / te)

    def __call__(self, x, t=None):
        if t is not None and self._t_prev is not None and t > self._t_prev:
            self.freq = 1.0 / (t - self._t_prev)
        self._t_prev = t
        if self._x_prev is None:
            self._x_prev = x
            return x
        dx = (x - self._x_prev) * self.freq
        a_d = self._alpha(self.d_cutoff, self.freq)
        dx_hat = a_d * dx + (1 - a_d) * self._dx_prev
        cutoff = self.min_cutoff + self.beta * abs(dx_hat)
        a = self._alpha(cutoff, self.freq)
        x_hat = a * x + (1 - a) * self._x_prev
        self._x_prev = x_hat
        self._dx_prev = dx_hat
        return x_hat


_session = requests.Session()


def send_move(position, velocity) -> bool:
    payload = {
        "position": position,
        "velocity": int(velocity),
        "max_current": SAFE_MAX_CURRENT,
        "wait": False,
    }
    try:
        r = _session.post(MOVE_ENDPOINT, json=payload, timeout=HTTP_TIMEOUT_S)
        return r.status_code < 400
    except requests.RequestException:
        return False


class MoveSender(threading.Thread):
    def __init__(self, period_s):
        super().__init__(daemon=True)
        self.period_s = period_s
        self._latest = None
        self._lock = threading.Lock()
        self._running = True
        self.last_status = "—"

    def update(self, position, velocity):
        with self._lock:
            self._latest = (position, int(velocity))

    def stop(self):
        self._running = False

    def run(self):
        while self._running:
            t0 = time.time()
            with self._lock:
                latest = self._latest
                self._latest = None
            if latest is not None:
                pos, vel = latest
                self.last_status = "OK" if send_move(pos, vel) else "FAIL"
            dt = time.time() - t0
            time.sleep(max(0.0, self.period_s - dt))


def _get_screen_size(default=(1280, 720)):
    try:
        if sys.platform == "win32":
            import ctypes
            u = ctypes.windll.user32
            u.SetProcessDPIAware()
            return int(u.GetSystemMetrics(0)), int(u.GetSystemMetrics(1))
    except Exception:
        pass
    return default


# ===========================================================================
# Çizim
# ===========================================================================
def draw_thumb_overlay(frame, lm):
    """Başparmak zinciri + abduction açısının görsel gösterimi."""
    h, w = frame.shape[:2]

    def px(i):
        return int(lm[i].x * w), int(lm[i].y * h)

    # Başparmak zinciri (sarı, kalın): CMC->MCP->IP->TIP
    chain = [T_CMC, T_MCP, T_IP, T_TIP]
    for a, b in zip(chain[:-1], chain[1:]):
        cv2.line(frame, px(a), px(b), (0, 255, 255), 3)
    for i in chain:
        cv2.circle(frame, px(i), 6, (0, 255, 255), -1)

    # Abduction açısı: bilek->başparmakUCU ve bilek->işaretMCP (camgöbeği)
    cv2.line(frame, px(WRIST), px(T_TIP), (255, 255, 0), 2)
    cv2.line(frame, px(WRIST), px(I_MCP), (255, 255, 0), 2)
    cv2.circle(frame, px(WRIST), 6, (255, 255, 0), -1)


def draw_panel(frame, flex_pct, abd_pct, t_ang, abd_ang,
               stats, robot_on, tx, fps, m1, m2, hand_present):
    """Büyük test paneli: barlar + canlı açılar + min/max + öneriler."""
    x0, y0 = 10, 56
    bw, bh, gap = 360, 30, 78

    def bar(y, label, pct, ang, mn, mx):
        cv2.putText(frame, label, (x0, y - 8),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.62, (255, 255, 255), 2)
        cv2.rectangle(frame, (x0, y), (x0 + bw, y + bh), (70, 70, 70), 2)
        fill = int(bw * max(0.0, min(1.0, pct)))
        if fill > 0:
            cv2.rectangle(frame, (x0, y), (x0 + fill, y + bh),
                          (0, 200, 255), -1)
        cv2.putText(frame, f"%{int(pct * 100):3d}", (x0 + bw + 12, y + 23),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 200, 255), 2)
        info = f"aci: {ang:5.1f}   min: {mn:5.1f}   max: {mx:5.1f}"
        cv2.putText(frame, info, (x0, y + bh + 22),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.55, (180, 255, 180), 1)

    if hand_present:
        bar(y0, "BasF  M1  (yukari-asagi / bukme)",
            flex_pct, t_ang, stats["flex_min"], stats["flex_max"])
        bar(y0 + gap, "BasR  M2  (saga-sola / rotasyon)",
            abd_pct, abd_ang, stats["abd_min"], stats["abd_max"])
    else:
        bar(y0, "BasF  M1  (yukari-asagi / bukme)", 0.0, 0.0, stats["flex_min"], stats["flex_max"])
        bar(y0 + gap, "BasR  M2  (saga-sola / rotasyon)", 0.0, 0.0, stats["abd_min"], stats["abd_max"])

    # Önerilen kalibrasyon (yeterli aralık görüldüyse)
    y = y0 + 2 * gap + 8
    if stats["flex_max"] - stats["flex_min"] > 10:
        s = (f"Oneri: THUMB_OPEN_DEG = {stats['flex_max'] - 3:.0f}   "
             f"THUMB_CLOSED_DEG = {stats['flex_min'] + 3:.0f}")
        cv2.putText(frame, s, (x0, y),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 255, 0), 1)
        y += 24
    if stats["abd_max"] - stats["abd_min"] > 8:
        s = (f"Oneri: ABDUCT_LO_DEG = {stats['abd_max'] - 2:.0f}   "
             f"ABDUCT_HI_DEG = {stats['abd_min'] + 2:.0f}")
        cv2.putText(frame, s, (x0, y),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 255, 0), 1)
        y += 24

    # Durum satırı (üst)
    rob = "ROBOT: ACIK" if robot_on else "ROBOT: KAPALI (r ile ac)"
    rcol = (0, 255, 0) if robot_on else (0, 165, 255)
    state = "EL VAR" if hand_present else "EL YOK"
    cv2.putText(frame, f"FPS:{fps:4.1f}  TX:{tx}  {state}  M1:{m1}  M2:{m2}",
                (10, 28), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 0), 2)
    cv2.putText(frame, rob, (frame.shape[1] - 330, 28),
                cv2.FONT_HERSHEY_SIMPLEX, 0.65, rcol, 2)

    # Alt yardım satırı
    cv2.putText(frame, "r: robot ac/kapat   c: min-max sifirla   q: cikis",
                (10, frame.shape[0] - 12),
                cv2.FONT_HERSHEY_SIMPLEX, 0.55, (200, 200, 200), 1)


# ===========================================================================
# Ana döngü
# ===========================================================================
def main():
    print("Başparmak testi başlıyor...")
    print(f"Kamera index: {CAMERA_INDEX}  |  Server: {SERVER_URL}")
    print("Robot gönderimi BAŞLANGIÇTA KAPALI. Pencerede 'r' ile açın.")

    cap = cv2.VideoCapture(CAMERA_INDEX, CAMERA_BACKEND)
    if not cap.isOpened():
        print(f"HATA: kamera (index {CAMERA_INDEX}) açılamadı.")
        return 1
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, CAMERA_WIDTH)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, CAMERA_HEIGHT)

    WINDOW_NAME = "Basparmak Testi (q: cikis)"
    cv2.namedWindow(WINDOW_NAME, cv2.WINDOW_NORMAL)
    if CAMERA_WINDOW_MODE == "fullscreen":
        cv2.setWindowProperty(WINDOW_NAME, cv2.WND_PROP_FULLSCREEN,
                              cv2.WINDOW_FULLSCREEN)
    elif CAMERA_WINDOW_MODE == "maximize":
        sw, sh = _get_screen_size()
        cv2.resizeWindow(WINDOW_NAME, sw, sh)
        cv2.moveWindow(WINDOW_NAME, 0, 0)
    else:
        cv2.resizeWindow(WINDOW_NAME, CAMERA_WIDTH, CAMERA_HEIGHT)

    mp_hands = mp.solutions.hands
    mp_draw = mp.solutions.drawing_utils
    hands = mp_hands.Hands(
        max_num_hands=2 if HAND_FILTER != "any" else 1,
        model_complexity=0,
        min_detection_confidence=MIN_DETECTION_CONFIDENCE,
        min_tracking_confidence=MIN_TRACKING_CONFIDENCE,
    )

    f_flex = OneEuroFilter(SEND_HZ, min_cutoff=THUMB_MIN_CUTOFF, beta=ONE_EURO_BETA)
    f_abd = OneEuroFilter(SEND_HZ, min_cutoff=THUMB_MIN_CUTOFF, beta=ONE_EURO_BETA)

    sender = MoveSender(SEND_PERIOD_S)
    sender.start()

    stats = {"flex_min": 999.0, "flex_max": -999.0,
             "abd_min": 999.0, "abd_max": -999.0}
    robot_on = False
    detect_count = 0
    last_sent = None          # deadband kapısı (sadece M1/M2)
    fps_hist = deque(maxlen=30)

    def to_enc(c):
        return int(MOTOR_MIN + c * (MOTOR_MAX - MOTOR_MIN))

    try:
        while True:
            t0 = time.time()
            ok, frame = cap.read()
            if not ok:
                continue
            frame = cv2.flip(frame, 1)
            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            result = hands.process(rgb)

            valid = None
            if result.multi_hand_landmarks:
                handed = result.multi_handedness or []
                for i, cand in enumerate(result.multi_hand_landmarks):
                    label = ""
                    if i < len(handed) and handed[i].classification:
                        label = handed[i].classification[0].label.lower()
                    if HAND_FILTER != "any" and label != HAND_FILTER:
                        continue   # yanlış el -> yok say
                    if _hand_size_ratio(cand, frame.shape) >= MIN_HAND_SIZE:
                        valid = cand
                        break
            detect_count = min(DETECT_DEBOUNCE, detect_count + 1) if valid else 0
            hand_present = detect_count >= DETECT_DEBOUNCE

            flex_pct = abd_pct = 0.0
            t_ang = abd_ang = 0.0
            m1 = m2 = 0

            if hand_present:
                lm = valid.landmark
                # Ham açılar
                t_ang = _angle_at(lm[T_IP], lm[T_MCP], lm[T_TIP])     # bükme (IP)
                abd_ang = _angle_at(lm[WRIST], lm[T_TIP], lm[I_MCP])  # rotasyon (UÇ tabanlı)

                # Min/max istatistikleri (kalibrasyon için)
                stats["flex_min"] = min(stats["flex_min"], t_ang)
                stats["flex_max"] = max(stats["flex_max"], t_ang)
                stats["abd_min"] = min(stats["abd_min"], abd_ang)
                stats["abd_max"] = max(stats["abd_max"], abd_ang)

                # Açı -> 0..1 (+ ölü bölge) -> filtre
                raw_flex = _apply_deadzone(
                    _remap_clamp(t_ang, THUMB_OPEN_DEG, THUMB_CLOSED_DEG),
                    CURL_DEADZONE)
                raw_abd = _apply_deadzone(
                    _remap_clamp(abd_ang, ABDUCT_LO_DEG, ABDUCT_HI_DEG),
                    ABDUCT_DEADZONE)
                now = time.time()
                flex_pct = f_flex(raw_flex, now)
                abd_pct = f_abd(raw_abd, now)
                m1, m2 = to_enc(flex_pct), to_enc(abd_pct)

                # Robot gönderimi (sadece M1/M2; 3-6 hep açık)
                if robot_on:
                    cur = (m1, m2)
                    if last_sent is None or \
                       max(abs(cur[0] - last_sent[0]),
                           abs(cur[1] - last_sent[1])) > DEADBAND:
                        sender.update([m1, m2, 0, 0, 0, 0], SAFE_VELOCITY)
                        last_sent = cur

                mp_draw.draw_landmarks(frame, valid, mp_hands.HAND_CONNECTIONS)
                draw_thumb_overlay(frame, lm)

            dt = time.time() - t0
            if dt > 0:
                fps_hist.append(1.0 / dt)
            fps = sum(fps_hist) / len(fps_hist) if fps_hist else 0.0

            draw_panel(frame, flex_pct, abd_pct, t_ang, abd_ang,
                       stats, robot_on, sender.last_status, fps,
                       m1, m2, hand_present)
            cv2.imshow(WINDOW_NAME, frame)

            key = cv2.waitKey(1) & 0xFF
            if key == ord("q"):
                break
            elif key == ord("r"):
                robot_on = not robot_on
                last_sent = None  # tekrar açılınca ilk kareyi hemen gönder
                print("ROBOT:", "ACIK" if robot_on else "KAPALI")
            elif key == ord("c"):
                stats.update(flex_min=999.0, flex_max=-999.0,
                             abd_min=999.0, abd_max=-999.0)
                print("min/max sıfırlandı.")

    except KeyboardInterrupt:
        pass
    finally:
        sender.stop()
        cap.release()
        cv2.destroyAllWindows()
        hands.close()
        print("Kapandı.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
