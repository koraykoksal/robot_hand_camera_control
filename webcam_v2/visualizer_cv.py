# -*- coding: utf-8 -*-
"""
visualizer_cv.py — v2 (OpenCV) çizimi:
ROI kutusu, el konturu, avuç dairesi, parmak uçları (kıvrıma göre renkli),
kıvrım/kuvvet barları ve küçük maske önizlemesi.
"""
from typing import Dict

import cv2
import numpy as np

import settings as config

FONT = cv2.FONT_HERSHEY_SIMPLEX
FINGERS = ["thumb", "index", "middle", "ring", "pinky"]
_FLBL = {"thumb": "Bas", "index": "Isa", "middle": "Ort", "ring": "Yuz", "pinky": "Ser"}


def _curl_color(curl: float):
    curl = float(np.clip(curl, 0, 1))
    return (0, int(255 * (1 - curl)), int(255 * curl))


def _panel(frame, x1, y1, x2, y2, alpha=0.5):
    x1, y1 = max(0, x1), max(0, y1)
    x2, y2 = min(frame.shape[1], x2), min(frame.shape[0], y2)
    if x2 <= x1 or y2 <= y1:
        return
    roi = frame[y1:y2, x1:x2]
    cv2.addWeighted(np.zeros_like(roi), alpha, roi, 1 - alpha, 0, roi)


def _text(frame, s, org, scale=0.36, color=(220, 220, 220), thick=1):
    cv2.putText(frame, s, org, FONT, scale, color, thick, cv2.LINE_AA)


def draw(frame, hand, forces: Dict[str, float], sending, mirror,
         target_hand, fps, robot_connected=False, infer_ms=0.0):
    h, w = frame.shape[:2]

    # ROI kutusu
    rx, ry, rw, rh = hand.roi if hand.roi != (0, 0, 0, 0) else (0, 0, 0, 0)
    if rw:
        col = (90, 220, 90) if hand.found else (70, 130, 220)
        cv2.rectangle(frame, (rx, ry), (rx + rw, ry + rh), col, 1, cv2.LINE_AA)
        _text(frame, "ELINI BU KUTUYA KOY (parmaklar yukari)", (rx + 6, ry + 16),
              0.36, col, 1)

    if hand.found:
        # el konturu
        if hand.contour is not None:
            cv2.drawContours(frame, [hand.contour], -1, (200, 200, 60), 1, cv2.LINE_AA)
        # avuç dairesi
        if hand.palm_center:
            cv2.circle(frame, hand.palm_center, int(hand.palm_radius), (255, 190, 60), 1, cv2.LINE_AA)
            cv2.circle(frame, hand.palm_center, 4, (255, 255, 255), -1, cv2.LINE_AA)
        # parmak uçları: avuçtan çizgi + kıvrım rengi
        for f, tip in hand.finger_tips.items():
            curl = hand.curls.get(f, 0.0)
            color = _curl_color(curl)
            thick = 3 if hand.moving.get(f, False) else 2
            if hand.palm_center:
                cv2.line(frame, hand.palm_center, tip, color, thick, cv2.LINE_AA)
            cv2.circle(frame, tip, 6, color, -1, cv2.LINE_AA)
            if hand.moving.get(f, False):
                cv2.circle(frame, tip, 12, (0, 255, 255), 2, cv2.LINE_AA)
            _text(frame, _FLBL[f], (tip[0] + 8, tip[1] - 6), 0.36, color, 1)
            fr = forces.get(f, 0.0)
            if fr >= config.FORCE_CONTACT_THRESHOLD:
                r = int(9 + 14 * min(fr / config.FORCE_DISPLAY_MAX, 1.0))
                cv2.circle(frame, tip, r, (255, 0, 255), 2, cv2.LINE_AA)

    # maske önizleme (sağ üst köşe)
    if config.SHOW_MASK and hand.mask is not None and hand.mask.size:
        mh = 110
        mw = int(hand.mask.shape[1] * mh / hand.mask.shape[0])
        small = cv2.resize(hand.mask, (mw, mh))
        small = cv2.cvtColor(small, cv2.COLOR_GRAY2BGR)
        x0 = w - mw - 6
        frame[26:26 + mh, x0:x0 + mw] = small
        cv2.rectangle(frame, (x0, 26), (x0 + mw, 26 + mh), (120, 120, 120), 1)
        _text(frame, "maske", (x0 + 4, 26 + mh - 4), 0.32, (200, 200, 200), 1)

    _hud(frame, hand, forces, sending, target_hand, fps, robot_connected, infer_ms)
    return frame


def _hud(frame, hand, forces, sending, target_hand, fps, robot_connected, infer_ms):
    h, w = frame.shape[:2]
    _panel(frame, 0, 0, w, 20)
    st = "el var" if hand.found else "el yok"
    _text(frame, f"v2 OpenCV   Hedef: {target_hand}   [{st}]", (8, 14), 0.38, (200, 255, 200))

    if not robot_connected:
        rob, rc = "ROBOT: bagli degil (R)", (60, 170, 255)
    elif sending:
        rob, rc = "ROBOT: bagli - GONDERIYOR", (80, 255, 80)
    else:
        rob, rc = "ROBOT: bagli - beklemede (SPACE)", (60, 255, 255)
    (tw, _), _ = cv2.getTextSize(rob, FONT, 0.38, 1)
    _text(frame, rob, (w - tw - 8, 14), 0.38, rc)

    row_h = 15
    ph = row_h * len(FINGERS) + 18
    y0 = h - ph - 16
    _panel(frame, 0, y0, 168, y0 + ph)
    _text(frame, "kivrim      kuvvet", (8, y0 + 11), 0.32, (165, 165, 165))
    for i, f in enumerate(FINGERS):
        y = y0 + 17 + i * row_h
        curl = hand.curls.get(f, 0.0) if hand.found else 0.0
        force = forces.get(f, 0.0)
        _text(frame, _FLBL[f], (8, y + 9), 0.32, (210, 210, 210))
        cv2.rectangle(frame, (40, y), (100, y + 9), (60, 60, 60), -1)
        cv2.rectangle(frame, (40, y), (40 + int(60 * curl), y + 9), _curl_color(curl), -1)
        fn = min(force / config.FORCE_DISPLAY_MAX, 1.0)
        cv2.rectangle(frame, (106, y), (162, y + 9), (60, 60, 60), -1)
        fc = (255, 0, 255) if force >= config.FORCE_CONTACT_THRESHOLD else (125, 125, 125)
        cv2.rectangle(frame, (106, y), (106 + int(56 * fn), y + 9), fc, -1)

    _panel(frame, 0, h - 16, w, h)
    perf = f"FPS {fps:.0f}" + (f" | {infer_ms:.0f}ms" if config.SHOW_PERF else "")
    _text(frame, f"{perf}  [q]cikis [r]robot [space]gonder [k]ten-rengi "
                 f"[o]acik-el [c]yumruk [x]sifirla [z]home", (8, h - 5), 0.34, (175, 175, 175))
