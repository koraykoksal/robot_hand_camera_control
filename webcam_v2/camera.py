# -*- coding: utf-8 -*-
"""
camera.py — Sağlam kamera açma.

Bazı webcam'ler DSHOW/MJPG/60FPS kombinasyonunu desteklemez; bu durumda
görüntü hiç gelmez ve pencere DONUK kalır. Burada birden çok yapılandırma
sırayla denenir ve her birinde GERÇEKTEN kare gelip gelmediği doğrulanır.
"""
import cv2

import settings as config

_BACKENDS = {
    "DSHOW": getattr(cv2, "CAP_DSHOW", 0),
    "MSMF": getattr(cv2, "CAP_MSMF", 0),
    "ANY": cv2.CAP_ANY,
}


def _try_open(index, backend_name, fourcc, width, height, fps):
    be = _BACKENDS.get(backend_name, cv2.CAP_ANY)
    cap = cv2.VideoCapture(index, be) if be else cv2.VideoCapture(index)
    if not cap.isOpened():
        cap.release()
        return None, "acilamadi"

    if fourcc:
        try:
            cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*fourcc))
        except Exception:
            pass
    if width and height:
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
    if fps:
        cap.set(cv2.CAP_PROP_FPS, fps)
    try:
        cap.set(cv2.CAP_PROP_BUFFERSIZE, getattr(config, "CAMERA_BUFFERSIZE", 1))
    except Exception:
        pass

    # GERÇEKTEN kare geliyor mu? (ilk kareler bazen boş gelir, birkaç deneme)
    ok = False
    for _ in range(8):
        got, frame = cap.read()
        if got and frame is not None and frame.size:
            ok = True
            break
    if not ok:
        cap.release()
        return None, "kare gelmedi"
    return cap, "ok"


def open_camera():
    """Sırayla dener; ilk ÇALIŞAN yapılandırmayı döndürür. Bulamazsa (None, None)."""
    idx = config.CAMERA_INDEX
    w, h = config.FRAME_WIDTH, config.FRAME_HEIGHT
    be = getattr(config, "CAMERA_BACKEND", "ANY").upper()
    fcc = getattr(config, "CAMERA_FOURCC", "")
    fps = getattr(config, "CAMERA_FPS", 0)

    # Denenecek yapılandırmalar: istenen -> giderek daha güvenli
    attempts = [
        (be, fcc, w, h, fps),
        (be, "", w, h, 0),            # MJPG olmadan
        ("MSMF", "", w, h, 0),
        ("ANY", "", w, h, 0),
        ("ANY", "", 640, 480, 0),     # düşük çözünürlük
        ("ANY", "", 0, 0, 0),         # kameranın varsayılanı
    ]
    seen = set()
    for a in attempts:
        if a in seen:
            continue
        seen.add(a)
        cap, msg = _try_open(idx, a[0], a[1], a[2], a[3], a[4])
        if cap is not None:
            aw = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
            ah = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
            afps = cap.get(cv2.CAP_PROP_FPS)
            print(f"[Kamera] ✅ {aw}x{ah} @ {afps:.0f} FPS  "
                  f"(backend={a[0]}, fourcc={a[1] or 'varsayilan'})")
            return cap, a
        print(f"[Kamera] denendi backend={a[0]} fourcc={a[1] or '-'} "
              f"{a[2]}x{a[3]} -> {msg}")

    print(f"❌ Kamera açılamadı (index={idx}). Başka uygulama kamerayı kullanıyor olabilir "
          f"veya settings.CAMERA_INDEX yanlış (0/1/2 deneyin).")
    return None, None
