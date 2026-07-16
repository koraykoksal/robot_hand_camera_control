# -*- coding: utf-8 -*-
"""
Kamera El Takibi -> Robot El Köprüsü
=====================================

Akış:
    Webcam --> MediaPipe Hands (21 landmark)
           --> her parmak için kıvrılma (0..1)
           --> 6 motor encoder değeri (0..10000)
           --> EMA yumuşatma + deadband + rate limit
           --> POST /move  (wait=False, blocking DEĞİL)

Gereksinimler:
    pip install opencv-python mediapipe numpy requests

Çalıştırma:
    1) Önce robot API server'ı başlatın:   python run_api.py
    2) Server'da bağlanın + enable edin:
         POST /connect  {"adapter_index": <idx>}
         POST /enable
    3) Bu scripti çalıştırın:               python camera_hand_bridge.py

    Çıkış: kamera penceresinde 'q' tuşu.

UYARI:
    - MOTOR_MAP'i kendi donanımınızda DOĞRULAYIN. /move ile tek tek
      [10000,0,0,0,0,0] gönderip hangi motorun hangi parmak olduğuna bakın.
    - İlk denemeyi DÜŞÜK akım/hız ile yapın (aşağıdaki SAFE_* değerleri).
    - Server'da motorlar enable olmadan hareket olmaz.
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
# config/ dizinini import yoluna ekle (run_api.py / run_tcp.py ile aynı yöntem)
# Böylece settings.py'deki sabitleri okuyabiliriz.
# ---------------------------------------------------------------------------
_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_CONFIG_DIR = os.path.join(_THIS_DIR, "config")
if _CONFIG_DIR not in sys.path:
    sys.path.insert(0, _CONFIG_DIR)

try:
    import settings as _settings
except Exception as _e:
    _settings = None
    print(f"⚠️ settings.py okunamadı ({_e}); varsayılan kamera ayarları kullanılacak.")


# ===========================================================================
# AYARLAR  (kendinize göre düzenleyin)
# ===========================================================================

# --- Kamera ---
# Kullanılacak kamera index'i ve backend'i config/settings.py'den okunur.
# Doğru index'i camera_select.py ile bulun (dahili webcam genelde 0, USB 1).
# settings.py okunamazsa aşağıdaki varsayılanlar kullanılır.
CAMERA_INDEX = int(getattr(_settings, "CAMERA_INDEX", 1)) if _settings else 1
_backend_name = str(getattr(_settings, "CAMERA_BACKEND", "dshow")).lower() if _settings else "dshow"
# String -> cv2 sabiti. "dshow" sadece Windows'ta anlamlı.
if _backend_name == "dshow" and sys.platform == "win32":
    CAMERA_BACKEND = cv2.CAP_DSHOW
else:
    CAMERA_BACKEND = cv2.CAP_ANY

# Kamera yakalama / pencere boyutu (config/settings.py'den)
CAMERA_WIDTH = int(getattr(_settings, "CAMERA_WIDTH", 960)) if _settings else 960
CAMERA_HEIGHT = int(getattr(_settings, "CAMERA_HEIGHT", 540)) if _settings else 540

# Pencere modu: "static" (sabit boyut) | "maximize" (ekrana büyüt) | "fullscreen" (tam ekran)
CAMERA_WINDOW_MODE = str(getattr(_settings, "CAMERA_WINDOW_MODE", "static")).lower() if _settings else "static"

# --- İkinci kamera (yandan, başparmak için) ---
CAMERA_INDEX_2 = int(getattr(_settings, "CAMERA_INDEX_2", 0)) if _settings else 0
CAMERA_2_ENABLED = bool(getattr(_settings, "CAMERA_2_ENABLED", False)) if _settings else False

# Her motor/parmak hangi kameradan beslensin? "cam1" (önden) veya "cam2" (yandan)
_DEFAULT_MOTOR_SOURCE = {
    "thumb": "cam1", "thumb_rot": "cam1", "index": "cam1",
    "middle": "cam1", "ring": "cam1", "pinky": "cam1",
}
MOTOR_SOURCE = dict(getattr(_settings, "MOTOR_SOURCE", _DEFAULT_MOTOR_SOURCE)) if _settings else dict(_DEFAULT_MOTOR_SOURCE)
# İkinci kamera kapalıysa her şeyi cam1'e zorla (tek kamera modu)
if not CAMERA_2_ENABLED:
    MOTOR_SOURCE = {k: "cam1" for k in MOTOR_SOURCE}

# --- Server ---
SERVER_URL = "http://127.0.0.1:8080"   # api_settings.py: API_PORT=8080
MOVE_ENDPOINT = SERVER_URL + "/move"
HTTP_TIMEOUT_S = 0.5                    # kısa: takılırsa frame atla

# --- Gönderim hızı ---
SEND_HZ = 15.0                          # saniyede kaç komut (10-20 makul)
SEND_PERIOD_S = 1.0 / SEND_HZ

# --- Güvenlik ---
SAFE_VELOCITY = int(getattr(_settings, "SAFE_VELOCITY", 8000)) if _settings else 8000        # sabit hız modunda kullanılır
SAFE_MAX_CURRENT = int(getattr(_settings, "SAFE_MAX_CURRENT", 400)) if _settings else 400    # 100..1500 (düşük = parmak nazik)

# --- Hız modu ---
# "dynamic": robot parmağı, sizin parmağınızla AYNI hızda hareket eder
#            (encoder/sn cinsinden el hızı ölçülüp motor hızı olarak verilir)
# "fixed"  : her hareket sabit SAFE_VELOCITY ile yapılır
VELOCITY_MODE = str(getattr(_settings, "VELOCITY_MODE", "dynamic")).lower() if _settings else "dynamic"
VEL_MIN = int(getattr(_settings, "VEL_MIN", 20000)) if _settings else 20000   # min motor hızı
VEL_MAX = int(getattr(_settings, "VEL_MAX", 20000)) if _settings else 20000   # max motor hızı (donanım tavanı)

# Köprü açılırken robot gönderimi açık mı başlasın? Çalışırken 'r' ile değiştirilir.
# False: kamera salt-izleme modunda başlar (robot bağlı değilken CPU/ağ israfı olmaz)
ROBOT_START_ON = bool(getattr(_settings, "ROBOT_START_ON", True)) if _settings else True

# --- El filtresi ---
# Hangi el takip edilsin? "right" (sağ), "left" (sol), "any" (fark etmez).
# Robot el SAĞ el olduğu için varsayılan "right". Diğer el yok sayılır.
# NOT: Sağ eliniz algılanmıyorsa etiket ters geliyordur -> "left" yazın.
HAND_FILTER = str(getattr(_settings, "HAND_FILTER", "right")).lower() if _settings else "right"
if HAND_FILTER not in ("right", "left", "any"):
    HAND_FILTER = "any"
_HAND_TR = {"right": "SAG EL", "left": "SOL EL"}

# --- El kaybolunca otomatik HOME ---
# El kameradan HOME_DELAY_S süre boyunca kaybolursa motorlar 0'a (tam açık /
# home) döner. Gecikme, anlık algılama kopmalarında robotun sıçramasını önler.
HOME_ON_HAND_LOST = bool(getattr(_settings, "HOME_ON_HAND_LOST", True)) if _settings else True
HOME_DELAY_S = float(getattr(_settings, "HOME_DELAY_S", 1.0)) if _settings else 1.0
HOME_VELOCITY = int(getattr(_settings, "HOME_VELOCITY", 8000)) if _settings else 8000


# --- Motor aralığı (settings.py ile aynı) ---
MOTOR_MIN = 0
MOTOR_MAX = 10000

# --- Yumuşatma / titreşim bastırma (One Euro filtresi) ---
# DEADBAND: bu encoder farkından küçük değişim GÖNDERİLMEZ. Titreşim için artırın.
# ONE_EURO_MIN_CUTOFF: KÜÇÜK = durağanda daha az titreşim (biraz daha gecikme)
# ONE_EURO_BETA      : BÜYÜK = harekette daha az gecikme
DEADBAND = int(getattr(_settings, "DEADBAND", 300)) if _settings else 300
ONE_EURO_MIN_CUTOFF = float(getattr(_settings, "ONE_EURO_MIN_CUTOFF", 1.0)) if _settings else 1.0
ONE_EURO_BETA = float(getattr(_settings, "ONE_EURO_BETA", 0.3)) if _settings else 0.3
# Başparmak (bükme + rotasyon) daha gürültülü; ona DAHA GÜÇLÜ yumuşatma uygula.
# KÜÇÜK = daha pürüzsüz (daha az titreme). Parmaklardan düşük tutulur.
THUMB_MIN_CUTOFF = float(getattr(_settings, "THUMB_MIN_CUTOFF", 0.5)) if _settings else 0.5

# --- Parmak->Motor haritası ---
# Robot motor index'i 1..6. 6 elemanlı liste motor 1..6 sırasıyla.
#   motor 1: başparmak bükme (yukarı-aşağı)   <- curls["thumb"]
#   motor 2: başparmak rotasyon (sağa-sola)   <- curls["thumb_rot"]
#   motor 3: işaret, 4: orta, 5: yüzük, 6: serçe

# --- Kıvrılma açı kalibrasyonu (settings.py'den; yoksa varsayılan) ---
# Parmak DÜZ iken eklem açısı ~180°, KIVRIK iken küçülür.
ANGLE_OPEN_DEG = float(getattr(_settings, "ANGLE_OPEN_DEG", 175.0)) if _settings else 175.0
ANGLE_CLOSED_DEG = float(getattr(_settings, "ANGLE_CLOSED_DEG", 90.0)) if _settings else 90.0

# Başparmak BÜKME ayrı kalibrasyon (genelde daha az bükülür)
# thumb_test.py ile kendi aralığınızı ölçüp settings.py'ye yazın.
THUMB_OPEN_DEG = float(getattr(_settings, "THUMB_OPEN_DEG", 160.0)) if _settings else 160.0
THUMB_CLOSED_DEG = float(getattr(_settings, "THUMB_CLOSED_DEG", 120.0)) if _settings else 120.0

# Başparmak ROTASYON (abduction / sağa-sola) kalibrasyonu
# Bilek(0)-başparmakUCU(4)-işaretMCP(5) açısı. Küçük=avuca kapanık, büyük=yayılmış.
# AÇIK el = başparmak yayılmış = BÜYÜK açı -> 0 (rest); avuca kapanma -> MAX.
# DEĞERLERİ thumb_test.py İLE ÖLÇÜN (ekrandaki "Oneri" satırı). Yön ters ise LO/HI takas.
ABDUCT_LO_DEG = float(getattr(_settings, "ABDUCT_LO_DEG", 45.0)) if _settings else 45.0  # bu açı (yayılmış/açık el) -> motor 0
ABDUCT_HI_DEG = float(getattr(_settings, "ABDUCT_HI_DEG", 12.0)) if _settings else 12.0  # bu açı (avuca kapanık) -> motor MAX

# --- Ölü bölge (deadzone) ---
# Bu değerin altındaki kıvrılma 0%'a çekilir; kalanı yeniden ölçeklenir.
# Böylece açık/gevşek el ve landmark gürültüsü %0 görünür.
# 0.0 = kapalı, 0.15 = küçük kıvrılmaları yok say.
CURL_DEADZONE = float(getattr(_settings, "CURL_DEADZONE", 0.15)) if _settings else 0.15
# Başparmak rotasyonu (BasR) için ayrı, biraz daha geniş ölü bölge (daha gürültülü)
ABDUCT_DEADZONE = float(getattr(_settings, "ABDUCT_DEADZONE", 0.12)) if _settings else 0.12

# Görselleştirme
SHOW_WINDOW = True

# --- Yanlış-pozitif (el olmayan objeyi el sanma) filtreleri ---
# MIN_DETECTION/TRACKING_CONFIDENCE: yüksek = daha az yanlış-pozitif, ama gerçek eli kaçırabilir
# MIN_HAND_SIZE: el kutusu köşegeni / kare köşegeni; bunun altı yok sayılır (küçük/sahte tespit)
# DETECT_DEBOUNCE: el kaç ardışık karede görülürse "gerçek" sayılsın (titrek sahte tespitleri eler)
MIN_DETECTION_CONFIDENCE = float(getattr(_settings, "MIN_DETECTION_CONFIDENCE", 0.8)) if _settings else 0.8
MIN_TRACKING_CONFIDENCE = float(getattr(_settings, "MIN_TRACKING_CONFIDENCE", 0.6)) if _settings else 0.6
MIN_HAND_SIZE = float(getattr(_settings, "MIN_HAND_SIZE", 0.18)) if _settings else 0.18
DETECT_DEBOUNCE = int(getattr(_settings, "DETECT_DEBOUNCE", 3)) if _settings else 3


# ===========================================================================
# MediaPipe landmark indeksleri
# ===========================================================================
WRIST = 0
# (MCP, PIP, TIP) üçlüleri - PIP ekleminde açı ölçeceğiz
FINGER_JOINTS = {
    "index":  (5, 6, 8),
    "middle": (9, 10, 12),
    "ring":   (13, 14, 16),
    "pinky":  (17, 18, 20),
}
THUMB_JOINTS = (2, 3, 4)   # başparmak IP ekleminde


# ===========================================================================
# Geometri yardımcıları
# ===========================================================================
def _angle_at(b, a, c) -> float:
    """b noktasındaki açı (derece). a-b-c üçlüsü, vektörler b->a ve b->c."""
    ba = np.array([a.x - b.x, a.y - b.y, a.z - b.z])
    bc = np.array([c.x - b.x, c.y - b.y, c.z - b.z])
    nba = np.linalg.norm(ba)
    nbc = np.linalg.norm(bc)
    if nba < 1e-6 or nbc < 1e-6:
        return 180.0
    cosang = np.dot(ba, bc) / (nba * nbc)
    cosang = max(-1.0, min(1.0, cosang))
    return math.degrees(math.acos(cosang))


def _apply_deadzone(t: float, dz: float) -> float:
    """0..1 değere ölü bölge uygula: dz altı -> 0, kalanı yeniden ölçekle."""
    if dz <= 0.0:
        return t
    if t <= dz:
        return 0.0
    return (t - dz) / (1.0 - dz)


def _curl_from_angle(angle_deg: float, open_deg: float, closed_deg: float) -> float:
    """Eklem açısını 0(açık)..1(kapalı) kıvrılmaya çevir + ölü bölge uygula."""
    if open_deg <= closed_deg:
        return 0.0
    t = (open_deg - angle_deg) / (open_deg - closed_deg)
    t = max(0.0, min(1.0, t))
    return _apply_deadzone(t, CURL_DEADZONE)


def _remap_clamp(val: float, lo: float, hi: float) -> float:
    """val'i [lo..hi] -> [0..1]'e doğrusal eşler, sınırlar. lo>hi ise ters yönde."""
    if lo == hi:
        return 0.0
    t = (val - lo) / (hi - lo)
    return max(0.0, min(1.0, t))


def compute_curls(lm) -> dict:
    """
    21 landmark -> her parmak için 0..1 değeri.
    Bükme (flexion) "pozisyon"dur; kuvvet DEĞİLDİR (kamera kuvvet ölçemez).
    """
    curls = {}
    # 4 normal parmak: PIP eklem açısı (bükme)
    for name, (mcp, pip, tip) in FINGER_JOINTS.items():
        ang = _angle_at(lm[pip], lm[mcp], lm[tip])
        curls[name] = _curl_from_angle(ang, ANGLE_OPEN_DEG, ANGLE_CLOSED_DEG)
    # Başparmak BÜKME: IP eklem açısı (motor 1)
    t_mcp, t_ip, t_tip = THUMB_JOINTS
    t_ang = _angle_at(lm[t_ip], lm[t_mcp], lm[t_tip])
    curls["thumb"] = _curl_from_angle(t_ang, THUMB_OPEN_DEG, THUMB_CLOSED_DEG)
    # Başparmak ROTASYON / yayılma (abduction, motor 2):
    # bilek(0) köşesinde başparmak UCU(4) ile işaretMCP(5) arasındaki açı.
    # UÇ kullanılır çünkü avuca kapanma hareketinde uç geniş yay çizer
    # (taban eklemi 2B'de neredeyse sabit kalır, hareketi kaçırır).
    abd_ang = _angle_at(lm[0], lm[4], lm[5])
    curls["thumb_rot"] = _apply_deadzone(
        _remap_clamp(abd_ang, ABDUCT_LO_DEG, ABDUCT_HI_DEG), ABDUCT_DEADZONE)
    return curls


def curls_to_motor_positions(curls: dict) -> list[int]:
    """Kıvrılma sözlüğü -> 6 motor encoder değeri (motor 1..6)."""
    def to_enc(c: float) -> int:
        return int(MOTOR_MIN + c * (MOTOR_MAX - MOTOR_MIN))
    return [
        to_enc(curls["thumb"]),       # motor 1: başparmak bükme (yukarı-aşağı)
        to_enc(curls["thumb_rot"]),   # motor 2: başparmak rotasyon (sağa-sola)
        to_enc(curls["index"]),       # motor 3
        to_enc(curls["middle"]),      # motor 4
        to_enc(curls["ring"]),        # motor 5
        to_enc(curls["pinky"]),       # motor 6
    ]


# ===========================================================================
# Yumuşatma + gönderim
# ===========================================================================
class OneEuroFilter:
    """
    One Euro filtresi — gürültülü interaktif sinyaller için.
    Durağanken ağır pürüzleştirir (titreşim ~yok), hareket ederken
    pürüzleştirmeyi gevşetir (gecikme ~yok). Her parmak için bir tane kullanılır.

    min_cutoff: KÜÇÜK = durağanda daha az titreşim (ama biraz daha gecikme)
    beta:       BÜYÜK = harekette daha az gecikme
    """

    def __init__(self, freq: float = 30.0, min_cutoff: float = 1.0,
                 beta: float = 0.3, d_cutoff: float = 1.0):
        self.freq = freq
        self.min_cutoff = min_cutoff
        self.beta = beta
        self.d_cutoff = d_cutoff
        self.x_prev = None
        self.dx_prev = 0.0
        self.t_prev = None

    def _alpha(self, cutoff: float) -> float:
        tau = 1.0 / (2 * math.pi * cutoff)
        te = 1.0 / self.freq
        return 1.0 / (1.0 + tau / te)

    def __call__(self, x: float, t: float | None = None) -> float:
        if self.x_prev is None:
            self.x_prev = x
            self.t_prev = t
            return x
        if t is not None and self.t_prev is not None:
            dt = t - self.t_prev
            if dt > 0:
                self.freq = 1.0 / dt
            self.t_prev = t
        dx = (x - self.x_prev) * self.freq
        a_d = self._alpha(self.d_cutoff)
        dx_hat = a_d * dx + (1 - a_d) * self.dx_prev
        cutoff = self.min_cutoff + self.beta * abs(dx_hat)
        a = self._alpha(cutoff)
        x_hat = a * x + (1 - a) * self.x_prev
        self.x_prev = x_hat
        self.dx_prev = dx_hat
        return x_hat


class SendGate:
    """Deadband kapısı: ardışık komutlar arası fark küçükse gönderme."""

    def __init__(self, deadband: int):
        self.deadband = deadband
        self._last_sent = None

    def update(self, motor_pos: list[int]) -> list[int] | None:
        if self._last_sent is not None:
            diff = max(abs(s - l) for s, l in zip(motor_pos, self._last_sent))
            if diff < self.deadband:
                return None
        self._last_sent = list(motor_pos)
        return motor_pos


def _hand_size_ratio(hand_lm, frame_shape) -> float:
    """El bounding-box köşegeninin kare köşegenine oranı (0..~1)."""
    h, w = frame_shape[:2]
    xs = [p.x for p in hand_lm.landmark]
    ys = [p.y for p in hand_lm.landmark]
    bw = (max(xs) - min(xs)) * w
    bh = (max(ys) - min(ys)) * h
    diag = (bw * bw + bh * bh) ** 0.5
    frame_diag = (w * w + h * h) ** 0.5
    return diag / frame_diag if frame_diag > 0 else 0.0


def draw_bbox(frame, hand_lm, label="EL", color=(0, 255, 0)):
    """Eli çerçeveye alır. Yeşil = takip edilen el, kırmızı = yok sayılan el."""
    h, w = frame.shape[:2]
    xs = [p.x for p in hand_lm.landmark]
    ys = [p.y for p in hand_lm.landmark]
    x1, x2 = int(min(xs) * w), int(max(xs) * w)
    y1, y2 = int(min(ys) * h), int(max(ys) * h)
    pad = 20
    x1, y1 = max(0, x1 - pad), max(0, y1 - pad)
    x2, y2 = min(w, x2 + pad), min(h, y2 + pad)
    cv2.rectangle(frame, (x1, y1), (x2, y2), color, 2)
    cv2.putText(frame, label, (x1, max(12, y1 - 8)),
                cv2.FONT_HERSHEY_SIMPLEX, 0.6, color, 2)


_FONT = cv2.FONT_HERSHEY_SIMPLEX


def _shade(frame, x0, y0, x1, y1, alpha=0.55):
    """Dikdörtgen bölgeyi karart (yarı saydam panel arka planı)."""
    h, w = frame.shape[:2]
    x0, y0 = max(0, x0), max(0, y0)
    x1, y1 = min(w, x1), min(h, y1)
    if x1 <= x0 or y1 <= y0:
        return
    roi = frame[y0:y1, x0:x1]
    frame[y0:y1, x0:x1] = (roi * (1.0 - alpha)).astype(roi.dtype)


def _otext(frame, text, org, scale=0.6, color=(255, 255, 255), thick=2):
    """Konturlu (siyah kenarlı) yazı: her arka planda okunur."""
    cv2.putText(frame, text, org, _FONT, scale, (0, 0, 0), thick + 2, cv2.LINE_AA)
    cv2.putText(frame, text, org, _FONT, scale, color, thick, cv2.LINE_AA)


def draw_bars(frame, curls: dict):
    """
    Parmak kıvrılma barları (sol üst, koyu panel üstünde, kompakt).
    El yoksa curls = sıfır gelir ve barlar %0 görünür.
    """
    order = ["thumb", "thumb_rot", "index", "middle", "ring", "pinky"]
    labels = {"thumb": "BasF", "thumb_rot": "BasR", "index": "Isa",
              "middle": "Ort", "ring": "Yuz", "pinky": "Ser"}
    bx, by, bw, bh, row = 14, 78, 150, 15, 23

    # Panel arka planı
    _shade(frame, bx - 8, by - 12, bx + 56 + bw + 62, by + len(order) * row + 2)

    for i, name in enumerate(order):
        c = curls.get(name, 0.0)
        y = by + i * row
        _otext(frame, labels[name], (bx, y + 12), 0.45, (255, 255, 255), 1)
        cv2.rectangle(frame, (bx + 56, y), (bx + 56 + bw, y + bh),
                      (110, 110, 110), 1)
        fill = int(bw * max(0.0, min(1.0, c)))
        if fill > 0:
            cv2.rectangle(frame, (bx + 56, y), (bx + 56 + fill, y + bh),
                          (0, 200, 255), -1)
        _otext(frame, f"%{int(c * 100):d}", (bx + 56 + bw + 8, y + 12),
               0.45, (0, 220, 255), 1)


def _capsule(frame, p1, p2, radius, color):
    """İki ucu yuvarlak 'kapsül' şekli (parmak segmenti)."""
    cv2.line(frame, p1, p2, color, radius * 2, cv2.LINE_AA)
    cv2.circle(frame, p1, radius, color, -1, cv2.LINE_AA)
    cv2.circle(frame, p2, radius, color, -1, cv2.LINE_AA)


def _rounded_rect(frame, x0, y0, x1, y1, r, color, thickness=-1):
    """Köşeleri yuvarlatılmış dikdörtgen."""
    cv2.rectangle(frame, (x0 + r, y0), (x1 - r, y1), color, thickness, cv2.LINE_AA)
    cv2.rectangle(frame, (x0, y0 + r), (x1, y1 - r), color, thickness, cv2.LINE_AA)
    for cx, cy in ((x0 + r, y0 + r), (x1 - r, y0 + r),
                   (x0 + r, y1 - r), (x1 - r, y1 - r)):
        cv2.circle(frame, (cx, cy), r, color, thickness, cv2.LINE_AA)


def draw_robot_hand(frame, moving: dict):
    """
    Sağ alt köşede estetik robot el şeması. Renk dili:
      GRİ     = boşta
      KIRMIZI = hareket ediyor (kamera komutu işliyor)
    Başparmak: motor1 VEYA motor2 hareketliyse kırmızı.
    """
    h, w = frame.shape[:2]
    pw, ph = 132, 168
    x0, y0 = w - pw - 12, h - ph - 12
    _shade(frame, x0 - 8, y0 - 8, x0 + pw + 8, y0 + ph + 8, 0.62)
    _otext(frame, "ROBOT", (x0 + 38, y0 + 13), 0.48, (0, 255, 0), 1)

    IDLE = (192, 192, 192)
    MOVE = (72, 72, 235)          # kırmızı (BGR)
    SHADOW = (28, 28, 28)
    KNUCKLE = (120, 120, 120)

    def state_color(name):
        return MOVE if moving.get(name) else IDLE

    palm_x0, palm_y0 = x0 + 36, y0 + 96
    palm_x1, palm_y1 = x0 + 118, y0 + 156

    # 4 parmak: kapsül gövde + eklem çizgisi + tırnak vurgusu
    fr = 8                                  # parmak yarıçapı
    xs = [palm_x0 + 8, palm_x0 + 30, palm_x0 + 52, palm_x0 + 74]
    lens = [52, 64, 57, 41]
    for fx, fl, nm in zip(xs, lens, ["index", "middle", "ring", "pinky"]):
        col = state_color(nm)
        base = (fx, palm_y0 - 2)
        tip = (fx, palm_y0 - fl)
        _capsule(frame, (base[0] + 2, base[1] + 2),
                 (tip[0] + 2, tip[1] + 2), fr, SHADOW)        # gölge
        _capsule(frame, base, tip, fr, col)
        ky = palm_y0 - int(fl * 0.55)                          # eklem çizgisi
        cv2.line(frame, (fx - fr + 2, ky), (fx + fr - 2, ky),
                 KNUCKLE, 1, cv2.LINE_AA)
        cv2.circle(frame, (tip[0] - 2, tip[1] - 2), 2,
                   (255, 255, 255), -1, cv2.LINE_AA)           # parlama

    # Avuç: gölge + yuvarlatılmış gövde
    _rounded_rect(frame, palm_x0 + 2, palm_y0 + 2, palm_x1 + 2, palm_y1 + 2,
                  10, SHADOW)
    _rounded_rect(frame, palm_x0, palm_y0, palm_x1, palm_y1, 10, (175, 175, 175))
    cv2.line(frame, (palm_x0 + 8, palm_y0 + 14), (palm_x1 - 8, palm_y0 + 14),
             KNUCKLE, 1, cv2.LINE_AA)                          # avuç çizgisi

    # Başparmak: açılı kapsül (M1/M2 hareket -> kırmızı, dokunma -> mavi)
    tcol = state_color("thumb")
    tbase = (palm_x0 + 8, palm_y0 + 26)
    ttip = (palm_x0 - 18, palm_y0 - 16)
    _capsule(frame, (tbase[0] + 2, tbase[1] + 2),
             (ttip[0] + 2, ttip[1] + 2), 8, SHADOW)
    _capsule(frame, tbase, ttip, 8, tcol)
    cv2.circle(frame, (ttip[0] - 2, ttip[1] - 2), 2,
               (255, 255, 255), -1, cv2.LINE_AA)


# Kalıcı HTTP bağlantısı (keep-alive) — her komutta yeni bağlantı açma maliyetini
# ortadan kaldırır. Yüksek hızlı gönderim için kritik.
_session = requests.Session()


def send_move(position: list[int], velocity: int) -> bool:
    """POST /move  wait=False (blocking değil), kalıcı bağlantı üzerinden."""
    payload = {
        "position": position,
        "velocity": int(velocity),
        "max_current": SAFE_MAX_CURRENT,
        "wait": False,            # <-- KRİTİK: gerçek-zaman için beklemeden dön
    }
    try:
        r = _session.post(MOVE_ENDPOINT, json=payload, timeout=HTTP_TIMEOUT_S)
        return r.status_code < 400
    except requests.RequestException:
        return False


class MoveSender(threading.Thread):
    """
    Arka planda robota komut gönderir; kamera döngüsünü ASLA bloklamaz.

    Kamera döngüsü update(pos, velocity) ile en güncel hedefi + hızı bırakır.
    Bu thread kendi hızında (SEND_HZ) en son hedefi gönderir.
    """

    def __init__(self, period_s: float):
        super().__init__(daemon=True)
        self.period_s = period_s
        self._latest = None       # (position, velocity)
        self._lock = threading.Lock()
        self._running = True
        self.last_status = "—"   # OK / FAIL / — (overlay'de gösterilir)

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
                self._latest = None   # gönderildi, tekrar gönderme
            if latest is not None:
                pos, vel = latest
                self.last_status = "OK" if send_move(pos, vel) else "FAIL"
            # Sabit hızda dön
            dt = time.time() - t0
            time.sleep(max(0.0, self.period_s - dt))


# ===========================================================================
# Ana döngü
# ===========================================================================
def _get_screen_size(default=(1280, 720)):
    """Ekran çözünürlüğünü döndürür (maximize modu için). Windows'ta ctypes ile."""
    try:
        if sys.platform == "win32":
            import ctypes
            u = ctypes.windll.user32
            return int(u.GetSystemMetrics(0)), int(u.GetSystemMetrics(1))
    except Exception:
        pass
    return default


class CamProc:
    """Tek bir kamera: yakalama + MediaPipe + boyut/debounce filtresi."""

    def __init__(self, index, label, mp_hands):
        self.index = index
        self.label = label
        self.cap = cv2.VideoCapture(index, CAMERA_BACKEND)
        if self.cap.isOpened():
            self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, CAMERA_WIDTH)
            self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, CAMERA_HEIGHT)
        self.hands = mp_hands.Hands(
            # El filtresi aktifken 2 ele kadar bak: iki el de kadrajdayken
            # doğru (etiketi eşleşen) eli seçebilmek için.
            max_num_hands=2 if HAND_FILTER != "any" else 1,
            model_complexity=0,
            min_detection_confidence=MIN_DETECTION_CONFIDENCE,
            min_tracking_confidence=MIN_TRACKING_CONFIDENCE,
        )
        self.detect_count = 0

    def opened(self):
        return self.cap.isOpened()

    def read_process(self, mp_hands, mp_draw):
        """
        Bir kare oku + işle.
        Döner: (frame, raw_curls or None, hand_present)
        frame her zaman döner (kare yoksa siyah).
        """
        ok, frame = self.cap.read()
        if not ok:
            frame = np.zeros((CAMERA_HEIGHT, CAMERA_WIDTH, 3), dtype=np.uint8)
            return frame, None, False

        frame = cv2.flip(frame, 1)
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        result = self.hands.process(rgb)

        # Aday seçimi: el filtresi (sağ/sol) + boyut kontrolü
        valid = None
        valid_label = ""
        wrong_hands = []   # filtreye takılan (yok sayılan) eller: (landmark, etiket)
        if result.multi_hand_landmarks:
            handed = result.multi_handedness or []
            for i, cand in enumerate(result.multi_hand_landmarks):
                label = ""
                if i < len(handed) and handed[i].classification:
                    label = handed[i].classification[0].label.lower()  # "left"/"right"
                if HAND_FILTER != "any" and label != HAND_FILTER:
                    wrong_hands.append((cand, label))
                    continue
                if _hand_size_ratio(cand, frame.shape) >= MIN_HAND_SIZE:
                    valid = cand
                    valid_label = label
                    break

        if valid is not None:
            self.detect_count = min(DETECT_DEBOUNCE, self.detect_count + 1)
        else:
            self.detect_count = 0
        present = self.detect_count >= DETECT_DEBOUNCE

        raw = None
        if present:
            raw = compute_curls(valid.landmark)
            if SHOW_WINDOW:
                mp_draw.draw_landmarks(frame, valid, mp_hands.HAND_CONNECTIONS)
                draw_bbox(frame, valid, _HAND_TR.get(valid_label, "EL"))

        if SHOW_WINDOW:
            # Yok sayılan (yanlış) eller: KIRMIZI çerçeve ile işaretle
            for wlm, wlabel in wrong_hands:
                draw_bbox(frame, wlm, _HAND_TR.get(wlabel, "DIGER EL"),
                          color=(0, 0, 255))
            col = (0, 255, 0) if present else (0, 165, 255)
            _otext(frame, self.label, (10, frame.shape[0] - 12), 0.5, col, 1)
            # Yanlış el kadrajda ama doğru el yok -> KIRMIZI hata uyarısı
            if wrong_hands and not present and HAND_FILTER != "any":
                msg = f"{_HAND_TR.get(HAND_FILTER, 'EL')} BEKLENIYOR (diger el yok sayildi)"
                _otext(frame, msg, (10, frame.shape[0] - 36), 0.5, (0, 0, 255), 1)
        return frame, raw, present

    def release(self):
        try:
            self.cap.release()
        except Exception:
            pass
        try:
            self.hands.close()
        except Exception:
            pass


def main():
    mp_hands = mp.solutions.hands
    mp_draw = mp.solutions.drawing_utils

    print("Kameralar açılıyor...")
    cam1 = CamProc(CAMERA_INDEX, "CAM1 ONDEN (parmaklar)", mp_hands)
    if not cam1.opened():
        print(f"HATA: 1. kamera (index {CAMERA_INDEX}) açılamadı.")
        return 1

    cam2 = None
    if CAMERA_2_ENABLED:
        cam2 = CamProc(CAMERA_INDEX_2, "CAM2 YANDAN (basparmak)", mp_hands)
        if not cam2.opened():
            print(f"UYARI: 2. kamera (index {CAMERA_INDEX_2}) açılamadı; tek kamera moduna geçiliyor.")
            cam2.release()
            cam2 = None

    cams = {"cam1": cam1}
    if cam2 is not None:
        cams["cam2"] = cam2
    print(f"Aktif kamera sayısı: {len(cams)}")

    # Pencere kurulumu — modu settings.py'deki CAMERA_WINDOW_MODE belirler
    WINDOW_NAME = "Hand -> Robot (q: cikis)"
    if SHOW_WINDOW:
        cv2.namedWindow(WINDOW_NAME, cv2.WINDOW_NORMAL)
        if CAMERA_WINDOW_MODE == "fullscreen":
            cv2.setWindowProperty(
                WINDOW_NAME, cv2.WND_PROP_FULLSCREEN, cv2.WINDOW_FULLSCREEN)
        elif CAMERA_WINDOW_MODE == "maximize":
            sw, sh = _get_screen_size()
            cv2.resizeWindow(WINDOW_NAME, sw, sh)
            cv2.moveWindow(WINDOW_NAME, 0, 0)
        else:  # "static" — iki kamera yan yana ise genişlik iki kat
            w = CAMERA_WIDTH * len(cams)
            cv2.resizeWindow(WINDOW_NAME, w, CAMERA_HEIGHT)

    # Parmak başına One Euro filtresi (başparmak daha güçlü yumuşatma)
    _finger_names = ["thumb", "thumb_rot", "index", "middle", "ring", "pinky"]
    filters = {}
    for n in _finger_names:
        mc = THUMB_MIN_CUTOFF if n in ("thumb", "thumb_rot") else ONE_EURO_MIN_CUTOFF
        filters[n] = OneEuroFilter(freq=SEND_HZ, min_cutoff=mc, beta=ONE_EURO_BETA)
    gate = SendGate(DEADBAND)
    fps_hist = deque(maxlen=30)
    last_curls = {n: 0.0 for n in _finger_names}  # kaynak kamera yoksa son değeri tut
    prev_sent_pos = None     # dinamik hız: son gönderilen pozisyon
    prev_sent_time = None    # dinamik hız: son gönderim zamanı
    prev_pos_vis = None      # robot el görseli: hareket tespiti için önceki pozisyon
    last_move_t = {n: 0.0 for n in ["thumb", "index", "middle", "ring", "pinky"]}
    MOVE_EPS = 120           # bu encoder farkından büyük değişim = "hareket ediyor"
    MOVE_HOLD_S = 0.35       # kırmızı bu kadar süre kalır (titreşip sönmesin)
    robot_on = ROBOT_START_ON  # 'r' tuşu ile aç/kapat
    hand_lost_since = None     # el kaybolma anı (otomatik home için)
    homed = False              # home komutu gönderildi mi

    sender = MoveSender(period_s=SEND_PERIOD_S)
    sender.start()

    print(f"Hazır. Server: {SERVER_URL}  |  Gönderim: {SEND_HZ:.0f} Hz")
    print("Çıkış için kamera penceresinde 'q'.")

    try:
        while True:
            t0 = time.time()

            # 1) Her kamerayı işle
            frames = {}
            cam_raw = {}      # "cam1"/"cam2" -> raw_curls dict veya None
            present = {}
            for key, cam in cams.items():
                frame, raw, pr = cam.read_process(mp_hands, mp_draw)
                frames[key] = frame
                cam_raw[key] = raw
                present[key] = pr

            # 2) MOTOR_SOURCE'a göre birleştir; kaynak kamera yoksa son değeri tut
            now = time.time()
            curls = {}
            updated_any = False
            for n in _finger_names:
                src = MOTOR_SOURCE.get(n, "cam1")
                src_raw = cam_raw.get(src)
                if src_raw is None and src != "cam1":
                    src_raw = cam_raw.get("cam1")   # kaynak yoksa cam1'e düş
                if src_raw is not None:
                    curls[n] = filters[n](src_raw[n], now)
                    last_curls[n] = curls[n]
                    updated_any = True
                else:
                    curls[n] = last_curls[n]         # tut

            any_hand = any(present.values())
            motor_pos = None
            cur_vel = SAFE_VELOCITY
            pos_now = curls_to_motor_positions(curls) if (any_hand and updated_any) else None

            # Robot el görseli için parmak başına hareket tespiti
            now_vis = time.time()
            if pos_now is not None and prev_pos_vis is not None:
                d = [abs(a - b) for a, b in zip(pos_now, prev_pos_vis)]
                fm = {"thumb": max(d[0], d[1]), "index": d[2],
                      "middle": d[3], "ring": d[4], "pinky": d[5]}
                for k, v in fm.items():
                    if v > MOVE_EPS:
                        last_move_t[k] = now_vis
            if pos_now is not None:
                prev_pos_vis = pos_now
            moving = {k: (now_vis - t) < MOVE_HOLD_S for k, t in last_move_t.items()}

            if pos_now is not None and robot_on:
                motor_pos = gate.update(pos_now)
                if motor_pos is not None:
                    # Hız: dinamik modda elin o anki hızı (encoder/sn), aksi halde sabit
                    if VELOCITY_MODE == "dynamic" and prev_sent_pos is not None \
                            and prev_sent_time is not None:
                        dt_send = time.time() - prev_sent_time
                        if dt_send > 1e-3:
                            max_delta = max(abs(a - b)
                                            for a, b in zip(motor_pos, prev_sent_pos))
                            speed = max_delta / dt_send          # encoder/sn
                            cur_vel = int(max(VEL_MIN, min(VEL_MAX, speed)))
                        else:
                            cur_vel = VEL_MIN
                    elif VELOCITY_MODE == "dynamic":
                        cur_vel = VEL_MIN                        # ilk gönderim
                    else:
                        cur_vel = SAFE_VELOCITY                  # sabit mod
                    prev_sent_pos = list(motor_pos)
                    prev_sent_time = time.time()
                    sender.update(motor_pos, cur_vel)

            # El kayboldu mu? Gecikme sonrası motorları 0'a (home) döndür.
            if HOME_ON_HAND_LOST:
                if not any_hand:
                    if hand_lost_since is None:
                        hand_lost_since = time.time()
                    elif not homed and (time.time() - hand_lost_since) >= HOME_DELAY_S:
                        homed = True
                        # Barlar ve iç durum sıfırlansın
                        for n in _finger_names:
                            last_curls[n] = 0.0
                            curls[n] = 0.0
                            mc = THUMB_MIN_CUTOFF if n in ("thumb", "thumb_rot") \
                                else ONE_EURO_MIN_CUTOFF
                            filters[n] = OneEuroFilter(
                                freq=SEND_HZ, min_cutoff=mc, beta=ONE_EURO_BETA)
                        gate = SendGate(DEADBAND)
                        prev_sent_pos = None
                        prev_sent_time = None
                        # Robot açıksa home komutunu kontrollü hızla gönder
                        if robot_on:
                            sender.update([MOTOR_MIN] * 6, HOME_VELOCITY)
                            for k in last_move_t:        # görselde kısa kırmızı
                                last_move_t[k] = time.time()
                            print("El kayboldu -> robot HOME pozisyonuna döndü.")
                else:
                    hand_lost_since = None
                    homed = False

            # 3) Gösterim: kameraları yan yana birleştir + barlar + durum
            dt = time.time() - t0
            if dt > 0:
                fps_hist.append(1.0 / dt)
            if SHOW_WINDOW:
                tiles = [cv2.resize(frames[k], (CAMERA_WIDTH, CAMERA_HEIGHT))
                         for k in cams]
                combined = cv2.hconcat(tiles) if len(tiles) > 1 else tiles[0]

                # Üst durum şeridi (2 satır: durum + motor değerleri)
                _shade(combined, 0, 0, combined.shape[1], 58, 0.55)
                fps = sum(fps_hist) / len(fps_hist) if fps_hist else 0.0
                cstat = " ".join(
                    f"{k.upper()}:{'OK' if present[k] else '--'}" for k in cams)
                state_txt = "EL VAR" if any_hand else ("EL YOK (HOME)" if homed else "EL YOK")
                tx_txt = sender.last_status if robot_on else "--"
                line1 = f"FPS:{fps:4.1f}  TX:{tx_txt}  {state_txt}  [{cstat}]"
                color = (0, 255, 0) if any_hand else (0, 165, 255)
                _otext(combined, line1, (12, 22), 0.52, color, 1)

                # Robot bağlantı/gönderim durumu (sağ üst)
                rob_txt = "ROBOT: ACIK" if robot_on else "ROBOT: KAPALI (r: ac)"
                rcol = (0, 255, 0) if robot_on else (0, 165, 255)
                rx = combined.shape[1] - (150 if robot_on else 230)
                _otext(combined, rob_txt, (rx, 22), 0.52, rcol, 1)

                if any_hand and motor_pos:
                    line2 = f"V:{cur_vel}  M:" + ",".join(str(v) for v in motor_pos)
                    _otext(combined, line2, (12, 46), 0.48, (200, 255, 200), 1)

                draw_bars(combined, curls)
                draw_robot_hand(combined, moving)
                cv2.imshow(WINDOW_NAME, combined)
                key = cv2.waitKey(1) & 0xFF
                if key == ord("q"):
                    break
                elif key == ord("r"):
                    robot_on = not robot_on
                    # Tekrar açılışta ilk kareyi hemen göndermek için kapıyı sıfırla
                    gate = SendGate(DEADBAND)
                    prev_sent_pos = None
                    prev_sent_time = None
                    print("ROBOT:", "ACIK" if robot_on else "KAPALI")

    except KeyboardInterrupt:
        pass
    finally:
        sender.stop()
        for cam in cams.values():
            cam.release()
        if SHOW_WINDOW:
            cv2.destroyAllWindows()
        print("Kapandı.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
