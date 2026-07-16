# -*- coding: utf-8 -*-
"""
Kamera Seçim / Test Aracı
=========================

Sadece OpenCV kullanır -> Python 3.14'te de çalışır (mediapipe GEREKMEZ).

Amaç:
  1) Sistemdeki kameraları bul (hangi index'ler açılıyor)
  2) Her birinin canlı görüntüsünü göster
  3) Doğru USB kamerayı seçip index'ini config/settings.py'ye KAYDET

Ayarlar config/settings.py'den okunur:
  CAMERA_INDEX   : başlangıçta gösterilecek / kayıtlı kamera
  CAMERA_BACKEND : "dshow" (Windows) veya "any"

Kullanım:
  python camera_select.py

Önizleme penceresindeki tuşlar:
  n  veya  SPACE : sonraki kameraya geç
  s              : bu kamerayı SEÇ (ardından settings.py'ye yazmayı sorar)
  q  veya  ESC   : çıkış

İsteğe bağlı (kamera İSİMLERİNİ de görmek için):
  py -m pip install pygrabber
"""

import os
import re
import sys
import cv2

# ---------------------------------------------------------------------------
# config/ dizinini import yoluna ekle (camera_hand_bridge.py ile aynı yöntem)
# ---------------------------------------------------------------------------
_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_CONFIG_DIR = os.path.join(_THIS_DIR, "config")
_SETTINGS_PATH = os.path.join(_CONFIG_DIR, "settings.py")
if _CONFIG_DIR not in sys.path:
    sys.path.insert(0, _CONFIG_DIR)

try:
    import settings as _settings
except Exception as _e:
    _settings = None
    print(f"⚠️ settings.py okunamadı ({_e}); varsayılan ayarlar kullanılacak.")

# settings.py'den oku (yoksa varsayılan)
CONFIGURED_INDEX = int(getattr(_settings, "CAMERA_INDEX", 1)) if _settings else 1
_backend_name = str(getattr(_settings, "CAMERA_BACKEND", "dshow")).lower() if _settings else "dshow"
if _backend_name == "dshow" and sys.platform == "win32":
    BACKEND = cv2.CAP_DSHOW
else:
    BACKEND = cv2.CAP_ANY

# Pencere boyutu / modu (camera_hand_bridge.py ile aynı ayarlar)
CAMERA_WIDTH = int(getattr(_settings, "CAMERA_WIDTH", 960)) if _settings else 960
CAMERA_HEIGHT = int(getattr(_settings, "CAMERA_HEIGHT", 540)) if _settings else 540
CAMERA_WINDOW_MODE = str(getattr(_settings, "CAMERA_WINDOW_MODE", "static")).lower() if _settings else "static"

WINDOW_NAME = "Kamera Secimi"

# Kaç index denenecek (0..MAX_PROBE-1)
MAX_PROBE = 6


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


def _setup_window():
    """Pencereyi settings.py'deki moda göre kurar (static/maximize/fullscreen)."""
    cv2.namedWindow(WINDOW_NAME, cv2.WINDOW_NORMAL)
    if CAMERA_WINDOW_MODE == "fullscreen":
        cv2.setWindowProperty(
            WINDOW_NAME, cv2.WND_PROP_FULLSCREEN, cv2.WINDOW_FULLSCREEN)
    elif CAMERA_WINDOW_MODE == "maximize":
        sw, sh = _get_screen_size()
        cv2.resizeWindow(WINDOW_NAME, sw, sh)
        cv2.moveWindow(WINDOW_NAME, 0, 0)
    else:  # "static"
        cv2.resizeWindow(WINDOW_NAME, CAMERA_WIDTH, CAMERA_HEIGHT)


def get_device_names():
    """pygrabber yüklüyse kamera isimlerini döndürür (Windows)."""
    try:
        from pygrabber.dshow_graph import FilterGraph
        return FilterGraph().get_input_devices()
    except Exception:
        return None


def probe_available(max_index):
    """Açılabilen ve kare okunabilen kamera index'lerini bulur."""
    found = []
    print("\nKameralar taranıyor (birkaç saniye sürebilir)...")
    for i in range(max_index):
        cap = cv2.VideoCapture(i, BACKEND)
        if cap.isOpened():
            ok, _ = cap.read()
            if ok:
                w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
                h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
                fps = cap.get(cv2.CAP_PROP_FPS)
                found.append(i)
                mark = "  <== settings.py'deki kayıtlı kamera" if i == CONFIGURED_INDEX else ""
                print(f"  [index {i}] AÇILDI   {w}x{h} @ {fps:.0f}fps{mark}")
            else:
                print(f"  [index {i}] açıldı ama kare okunamadı (atlanıyor)")
        cap.release()
    return found


def preview(indices, names, start_pos=0):
    """Bulunan kameraları sırayla önizler; kullanıcı 's' ile seçer."""
    _setup_window()  # settings.py'deki moda göre (static/maximize/fullscreen)
    pos = start_pos
    while True:
        idx = indices[pos]
        cap = cv2.VideoCapture(idx, BACKEND)
        if not cap.isOpened():
            print(f"index {idx} açılamadı, atlanıyor")
            pos = (pos + 1) % len(indices)
            continue

        label_name = names[idx] if (names and idx < len(names)) else ""
        print(f"\n>>> Önizleme: index {idx}  {label_name}")
        print("    n/SPACE: sonraki | s: SEC | q/ESC: cikis")

        switch = False
        while True:
            ok, frame = cap.read()
            if not ok:
                print(f"index {idx} kare veremedi, sonrakine geçiliyor")
                pos = (pos + 1) % len(indices)
                switch = True
                break

            frame = cv2.flip(frame, 1)  # ayna görüntüsü
            txt = f"index {idx}" + (f"  ({label_name})" if label_name else "")
            if idx == CONFIGURED_INDEX:
                txt += "  [KAYITLI]"
            cv2.putText(frame, txt, (10, 32),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 0), 2)
            cv2.putText(frame, "n: sonraki   s: SEC   q: cikis", (10, 68),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 200, 255), 2)
            cv2.imshow(WINDOW_NAME, frame)

            key = cv2.waitKey(1) & 0xFF
            if key in (ord("n"), ord(" ")):
                pos = (pos + 1) % len(indices)
                switch = True
                break
            elif key == ord("s"):
                cap.release()
                cv2.destroyAllWindows()
                return idx
            elif key in (ord("q"), 27):  # 27 = ESC
                cap.release()
                cv2.destroyAllWindows()
                return None

        cap.release()
        if not switch:
            break
    return None


def save_index_to_settings(index):
    """
    Seçilen index'i config/settings.py'deki CAMERA_INDEX satırına yazar.
    Satır yoksa dosyanın sonuna bir KAMERA AYARLARI bölümü ekler.
    """
    if not os.path.exists(_SETTINGS_PATH):
        print(f"❌ settings.py bulunamadı: {_SETTINGS_PATH}")
        return False
    try:
        with open(_SETTINGS_PATH, "r", encoding="utf-8") as f:
            content = f.read()

        # "CAMERA_INDEX = .." veya "CAMERA_INDEX: int = .." satırını yakala
        pattern = re.compile(r"^CAMERA_INDEX\s*(:[^=\n]+)?=\s*.+$", re.MULTILINE)
        new_line = f"CAMERA_INDEX: int = {index}"

        if pattern.search(content):
            content = pattern.sub(new_line, content, count=1)
        else:
            # Bölüm hiç yoksa dosyanın sonuna ekle
            content = content.rstrip() + "\n\n\n" + (
                "# " + "=" * 76 + "\n"
                "# KAMERA AYARLARI\n"
                "# " + "=" * 76 + "\n"
                "# Kullanılacak kamera index'i. camera_select.py ile bulun.\n"
                f"CAMERA_INDEX: int = {index}\n"
                "# Kamera backend'i: \"dshow\" (Windows) veya \"any\"\n"
                'CAMERA_BACKEND: str = "dshow"\n'
            )

        with open(_SETTINGS_PATH, "w", encoding="utf-8") as f:
            f.write(content)
        return True
    except Exception as e:
        print(f"❌ settings.py yazma hatası: {e}")
        return False


def main():
    print(f"settings.py kayıtlı kamera index'i: {CONFIGURED_INDEX}")

    names = get_device_names()
    if names:
        print("\nSistemdeki kamera cihazları (isim):")
        for i, n in enumerate(names):
            mark = "  <== kayıtlı" if i == CONFIGURED_INDEX else ""
            print(f"  [index {i}] {n}{mark}")
    else:
        print("(Kamera isimlerini de görmek isterseniz: py -m pip install pygrabber)")

    indices = probe_available(MAX_PROBE)
    if not indices:
        print("\n❌ HİÇ KAMERA BULUNAMADI.")
        print("   - USB kamera takılı mı?")
        print("   - Başka bir uygulama (Zoom/Teams/Kamera app) kamerayı kullanıyor olabilir, kapatın.")
        print("   - USB'yi çıkarıp başka porta takmayı deneyin.")
        return 1

    print(f"\nAçılabilen index'ler: {indices}")

    # Önizlemeye kayıtlı kameradan başla (varsa)
    start_pos = indices.index(CONFIGURED_INDEX) if CONFIGURED_INDEX in indices else 0
    chosen = preview(indices, names, start_pos=start_pos)

    if chosen is None:
        print("\nSeçim yapılmadı.")
        return 0

    print("\n" + "=" * 52)
    print(f"  ✅ SEÇİLEN KAMERA INDEX: {chosen}")
    print("=" * 52)

    # settings.py'ye yazmayı teklif et
    try:
        ans = input(f"\nBu index ({chosen}) config/settings.py'ye yazılsın mı? [e/h]: ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        ans = "h"

    if ans in ("e", "evet", "y", "yes"):
        if save_index_to_settings(chosen):
            print(f"✅ settings.py güncellendi: CAMERA_INDEX = {chosen}")
            print("   camera_hand_bridge.py artık bu kamerayı kullanacak.")
        else:
            print("⚠️ Otomatik yazma başarısız. Elle ekleyin:")
            print(f"   config/settings.py -> CAMERA_INDEX: int = {chosen}")
    else:
        print("Kaydedilmedi. İsterseniz elle:")
        print(f"   config/settings.py -> CAMERA_INDEX: int = {chosen}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
