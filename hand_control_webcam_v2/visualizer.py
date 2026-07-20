# -*- coding: utf-8 -*-
"""
visualizer.py — Kamera üzerine çizim:
- Sağ elin iskeleti (landmark + bağlantılar)
- Her parmağı kıvrım miktarına göre RENKLENDİR (yeşil=açık -> kırmızı=kapalı)
- HAREKET EDEN parmağı ayrıca kalın/parlak çiz
- Parmak ucundaki kuvvet sensörünü daire + bar olarak göster (temas varsa vurgu)

Not: OpenCV Hershey fontları Türkçe ş/ç/ğ gibi karakterleri gösteremez;
ekran yazıları bilerek ASCII tutuldu.
"""
from typing import Dict, List, Tuple

import cv2
import numpy as np

import settings as config

FONT = cv2.FONT_HERSHEY_SIMPLEX

FINGER_CHAINS = {
    "thumb":  [0, 1, 2, 3, 4],
    "index":  [0, 5, 6, 7, 8],
    "middle": [0, 9, 10, 11, 12],
    "ring":   [0, 13, 14, 15, 16],
    "pinky":  [0, 17, 18, 19, 20],
}
FINGERTIP_LM = {"thumb": 4, "index": 8, "middle": 12, "ring": 16, "pinky": 20}
FINGERS = ["thumb", "index", "middle", "ring", "pinky"]
_FLBL = {"thumb": "Bas", "index": "Isa", "middle": "Ort", "ring": "Yuz", "pinky": "Ser"}


def _curl_color(curl: float) -> Tuple[int, int, int]:
    """0(açık)=yeşil, 1(kapalı)=kırmızı. BGR."""
    curl = float(np.clip(curl, 0, 1))
    return (0, int(255 * (1 - curl)), int(255 * curl))


def _panel(frame, x1, y1, x2, y2, alpha=0.45):
    """Yarı saydam koyu panel (yazılar daha okunaklı olsun diye)."""
    x1, y1 = max(0, x1), max(0, y1)
    x2, y2 = min(frame.shape[1], x2), min(frame.shape[0], y2)
    if x2 <= x1 or y2 <= y1:
        return
    roi = frame[y1:y2, x1:x2]
    dark = np.zeros_like(roi)
    cv2.addWeighted(dark, alpha, roi, 1 - alpha, 0, roi)


def _text(frame, s, org, scale=0.42, color=(235, 235, 235), thick=1):
    cv2.putText(frame, s, org, FONT, scale, color, thick, cv2.LINE_AA)


def draw(frame, hand, forces: Dict[str, float],
         sending: bool, mirror: bool, target_hand: str, fps: float,
         robot_connected: bool = False, infer_ms: float = 0.0):
    if hand.found:
        pts: List[Tuple[int, int]] = hand.px_landmarks
        for finger, chain in FINGER_CHAINS.items():
            curl = hand.curls.get(finger, 0.0)
            moving = hand.moving.get(finger, False)
            color = _curl_color(curl)
            thickness = 4 if moving else 2
            for i in range(len(chain) - 1):
                cv2.line(frame, pts[chain[i]], pts[chain[i + 1]], color, thickness, cv2.LINE_AA)
            for idx in chain[1:]:
                cv2.circle(frame, pts[idx], 3, color, -1, cv2.LINE_AA)

            tip = pts[FINGERTIP_LM[finger]]
            if moving:
                cv2.circle(frame, tip, 11, (0, 255, 255), 2, cv2.LINE_AA)

            f = forces.get(finger, 0.0)
            if f >= config.FORCE_CONTACT_THRESHOLD:
                r = int(8 + 14 * min(f / config.FORCE_DISPLAY_MAX, 1.0))
                cv2.circle(frame, tip, r, (255, 0, 255), 2, cv2.LINE_AA)
                _text(frame, f"{f:.1f}N", (tip[0] + 7, tip[1] - 7),
                      0.38, (255, 0, 255), 1)

        cv2.circle(frame, pts[0], 5, (255, 255, 255), -1, cv2.LINE_AA)

    _draw_hud(frame, hand, forces, sending, mirror, target_hand, fps, robot_connected, infer_ms)
    return frame


def _draw_hud(frame, hand, forces, sending, mirror, target_hand, fps, robot_connected, infer_ms):
    h, w = frame.shape[:2]

    # --- üst çubuk ---
    _panel(frame, 0, 0, w, 20, alpha=0.5)
    status = "el var" if hand.found else "el yok"
    det = hand.handedness or "-"
    _text(frame, f"v2   Hedef: {target_hand}   Algilanan: {det}   [{status}]",
          (8, 14), 0.38, (200, 255, 200), 1)

    if not robot_connected:
        rob, rcol = "ROBOT: bagli degil (R)", (60, 170, 255)
    elif sending:
        rob, rcol = "ROBOT: bagli - GONDERIYOR", (80, 255, 80)
    else:
        rob, rcol = "ROBOT: bagli - beklemede (SPACE)", (60, 255, 255)
    (tw, _), _ = cv2.getTextSize(rob, FONT, 0.38, 1)
    _text(frame, rob, (w - tw - 8, 14), 0.38, rcol, 1)

    # --- sol alt: kıvrım + kuvvet barları (kompakt) ---
    row_h = 15
    panel_h = row_h * len(FINGERS) + 18
    y0 = h - panel_h - 16
    _panel(frame, 0, y0, 168, y0 + panel_h, alpha=0.5)
    _text(frame, "kivrim      kuvvet", (8, y0 + 11), 0.32, (165, 165, 165), 1)
    for i, f in enumerate(FINGERS):
        y = y0 + 17 + i * row_h
        curl = hand.curls.get(f, 0.0) if hand.found else 0.0
        force = forces.get(f, 0.0)
        _text(frame, _FLBL[f], (8, y + 9), 0.32, (210, 210, 210), 1)
        # kıvrım barı
        cv2.rectangle(frame, (40, y), (100, y + 9), (60, 60, 60), -1)
        cv2.rectangle(frame, (40, y), (40 + int(60 * curl), y + 9), _curl_color(curl), -1)
        # kuvvet barı
        fn = min(force / config.FORCE_DISPLAY_MAX, 1.0)
        cv2.rectangle(frame, (106, y), (162, y + 9), (60, 60, 60), -1)
        fcol = (255, 0, 255) if force >= config.FORCE_CONTACT_THRESHOLD else (125, 125, 125)
        cv2.rectangle(frame, (106, y), (106 + int(56 * fn), y + 9), fcol, -1)

    # --- alt ipucu ---
    _panel(frame, 0, h - 16, w, h, alpha=0.5)
    perf = f"FPS {fps:.0f}" + (f" | {infer_ms:.0f}ms" if config.SHOW_PERF else "")
    _text(frame, f"{perf}   [q]cikis [r]robot [space]gonder [m]ayna [h]el "
                 f"[z]home",
          (8, h - 5), 0.34, (175, 175, 175), 1)
