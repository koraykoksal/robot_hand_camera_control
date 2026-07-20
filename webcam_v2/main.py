# -*- coding: utf-8 -*-
"""
main.py — Ana döngü.

Zincir:  kamera -> el takibi(sağ el) -> parmak kıvrımı -> robot ele komut
                                                    -> kuvvet sensörü oku -> ekrana çiz

Tuşlar:
  q / ESC : çık
  r       : robot el bağlantısını yap/kes
  space   : robota komut göndermeyi aç/kapa (önce R ile bağlan)
  m       : ayna görüntü aç/kapa
  h       : hedef eli değiştir (Right <-> Left)
  o       : AÇIK el kalibrasyonu (elini tam aç, sonra bas) -> kıvrım 0 noktası
  c       : KAPALI el kalibrasyonu (yumruk yap, sonra bas) -> kıvrım 1 noktası
  x       : kalibrasyonu sıfırla
  z       : robot eli yeniden home + self-test (motor enable/alarm kontrolü)
"""
import time

import cv2

import settings as config
from camera import open_camera
from hand_tracker_cv import CvHandTracker
from robot_interface import create_hand, compute_targets, JOINT_ORDER
import visualizer_cv as visualizer

APP_NAME = "LHandPro Kamera Kontrol (v2 OpenCV)"
APP_VERSION = "1.0.0"
WINDOW_NAME = "LHandPro - Kamera Kontrol v2 (OpenCV)"
FINGERS = ["thumb", "index", "middle", "ring", "pinky"]
_FSHORT = {"thumb": "başp", "index": "işrt", "middle": "orta", "ring": "yüzk", "pinky": "serç"}


def print_start_header():
    print("=" * 60)
    print(f" {APP_NAME}  v{APP_VERSION}")
    print("-" * 60)
    print(f"  Robot modu     : {'MOCK (sahte)' if config.MOCK_ROBOT else 'REAL (gerçek)'}")
    print(f"  İletişim       : {config.COMM_MODE}")
    print(f"  SDK yolu       : {config.SDK_PYTHON_DIR}")
    print(f"  Hız (velocity) : {config.MOVE_VELOCITY}   |  Kuvvet (akım): {config.MAX_CURRENT}")
    print(f"  Tork kontrolü  : {'AÇIK' if config.ENABLE_TORQUE_CONTROL else 'KAPALI'}")
    print("=" * 60)
    print("  ROBOT BAĞLI DEĞİL. Bağlamak için kamera penceresinde 'R' tuşuna bas.")
    print("=" * 60)


def print_robot_info(hand_robot):
    info = hand_robot.get_info()
    print("-" * 60)
    print(f"  ✅ ROBOT BAĞLANDI ({info['mode']})")
    print(f"  Firmware sürümü: {info['firmware']}")
    print(f"  DOF (total/akt): {info['dof_total']} / {info['dof_active']}")
    print(f"  EtherCAT slave : {info['slaves']}")
    print(f"  Enable (motor) : {info.get('enabled', '?')}")
    # Motor durum tablosu (enable / alarm / açı)
    status = getattr(hand_robot, "motor_status", [])
    if status:
        print("  " + "-" * 52)
        print(f"  {'motor':<20}{'enable':<8}{'alarm':<7}{'aci':<8}{'akim'}")
        for m in status:
            en = "1" if m["enabled"] else "0"
            al = "-" if m["alarm"] == 0 else str(m["alarm"])
            flag = "" if (m["enabled"] and m["alarm"] == 0) else "  <-- DIKKAT"
            print(f"  {str(m['id'])+' '+m['name']:<20}{en:<8}{al:<7}"
                  f"{m['angle']:<8.1f}{m['current']}{flag}")
        print("  " + "-" * 52)
        bad = [m for m in status if not m["enabled"] or m["alarm"] != 0]
        if bad:
            print("  ⚠️  Bazı motorlar enable değil / alarmda. "
                  "Kabloyu/gücü kontrol et, 'z' ile tekrar home dene.")
        else:
            print("  Tüm motorlar enable=1 ve alarmsız. Hazır.")
    print("-" * 60)


def log_movement(t0, hand, forces, positions, sending, connected):
    """Yapılan hareketi tek satır olarak konsola yazar."""
    t = time.time() - t0
    parts = []
    pos_map = dict(zip(JOINT_ORDER, positions)) if positions else {}
    for f in FINGERS:
        curl = hand.curls.get(f, 0.0)
        pos = pos_map.get("thumb_flexion" if f == "thumb" else f, 0)
        mark = "*" if hand.moving.get(f, False) else " "
        force = forces.get(f, 0.0)
        fmark = "!" if force >= config.FORCE_CONTACT_THRESHOLD else ""
        parts.append(f"{_FSHORT[f]}{mark}{curl:.2f}->{pos:>5}{('|'+format(force,'.1f')+'N'+fmark) if force else ''}")
    moving = [f for f in FINGERS if hand.moving.get(f, False)]
    if not connected:
        tag = "bağlısız"
    elif sending:
        tag = "GÖNDER"
    else:
        tag = "izle"
    print(f"[{t:6.1f}s|{tag}] " + "  ".join(parts) +
          (f"   << hareket: {', '.join(moving)}" if moving else ""))


def maximize_window(name):
    """Pencereyi büyüt (Windows'ta gerçek maximize; diğerlerinde ekrana sığdır)."""
    try:
        import ctypes
        user32 = ctypes.windll.user32
        hwnd = user32.FindWindowW(None, name)
        if hwnd:
            user32.ShowWindow(hwnd, 3)  # SW_MAXIMIZE
            return
    except Exception:
        pass
    try:  # yedek: ekran boyutuna getir
        import ctypes
        user32 = ctypes.windll.user32
        sw, sh = user32.GetSystemMetrics(0), user32.GetSystemMetrics(1)
        cv2.resizeWindow(name, sw, sh)
        cv2.moveWindow(name, 0, 0)
    except Exception:
        pass


def connect_robot(hand_robot):
    """R tuşuyla çağrılır. Başarılıysa True döner."""
    print("[R] Robot bağlantısı deneniyor...")
    try:
        if hand_robot.connect():
            print_robot_info(hand_robot)
            return True
        print("❌ Robot bağlanamadı. settings.py / donanım / Npcap / sdk DLL'lerini kontrol et.")
    except Exception as e:
        print(f"❌ Robot bağlantı hatası: {e}")
    return False


def main():
    print_start_header()
    hand_robot = create_hand()
    robot_connected = False

    # --- Kamera + takip ---
    tracker = CvHandTracker()
    cap, used = open_camera()
    if cap is None:
        tracker.close()
        return

    cv2.namedWindow(WINDOW_NAME, cv2.WINDOW_NORMAL | cv2.WINDOW_KEEPRATIO)
    window_maximized = False

    mirror = config.MIRROR_VIEW
    target_hand = config.TARGET_HAND
    sending = False
    send_period = 1.0 / max(config.ROBOT_SEND_HZ, 1)
    last_send = 0.0
    log_period = 1.0 / max(config.LOG_HZ, 1)
    last_log = 0.0
    t0 = time.time()
    forces = {}
    prev_t = time.time()
    fps = 0.0
    miss = 0

    print("Hazır. Pencere açık. Robotu bağlamak için 'R', komut için 'SPACE'.")
    print("SIRAYLA: 1) elini ROI kutusuna koy  2) 'K' ten rengini ogret  "
          "3) elini tam AC + 'O'  4) YUMRUK + 'C'")
    try:
        while True:
            ok, frame = cap.read()
            if not ok or frame is None or frame.size == 0:
                miss += 1
                if miss <= 3:
                    time.sleep(0.05)
                    continue
                print("Kamera kare vermiyor (kablo/başka uygulama?). Çıkılıyor.")
                break
            miss = 0

            if mirror:
                frame = cv2.flip(frame, 1)

            hand = tracker.process(frame, target_hand, mirror)

            now = time.time()
            # Robota gönderim — sadece bağlıysa + sending açıksa + el bulunduysa
            if robot_connected and hand.found and sending and (now - last_send) >= send_period:
                hand_robot.send(hand.curls, hand.thumb_abduction)
                last_send = now

            # Kuvvet sensörleri — sadece robot bağlıysa
            if robot_connected and config.USE_FORCE_SENSORS:
                forces = hand_robot.read_forces()
            else:
                forces = {}

            # Konsol logu
            if config.CONSOLE_LOG and hand.found and (now - last_log) >= log_period:
                any_move = any(hand.moving.get(f, False) for f in FINGERS)
                if any_move or not config.LOG_ONLY_ON_MOVE:
                    targets = compute_targets(hand.curls, hand.thumb_abduction)
                    log_movement(t0, hand, forces, targets, sending, robot_connected)
                    last_log = now

            # FPS
            dt = now - prev_t
            prev_t = now
            if dt > 0:
                fps = 0.9 * fps + 0.1 * (1.0 / dt)

            visualizer.draw(frame, hand, forces, sending, mirror,
                            target_hand, fps, robot_connected, tracker.infer_ms)
            cv2.imshow(WINDOW_NAME, frame)

            # İlk kare gösterildikten sonra pencereyi büyüt
            if config.MAXIMIZE_WINDOW and not window_maximized:
                maximize_window(WINDOW_NAME)
                window_maximized = True

            key = cv2.waitKey(1) & 0xFF
            if key in (ord('q'), 27):
                break
            elif key == ord('r'):
                if not robot_connected:
                    robot_connected = connect_robot(hand_robot)
                else:
                    print("[R] Robot bağlantısı kesiliyor...")
                    sending = False
                    try:
                        hand_robot.disconnect()
                    finally:
                        robot_connected = False
            elif key == ord(' '):
                if not robot_connected:
                    print("Önce 'R' ile robotu bağla.")
                else:
                    sending = not sending
                    print("Robota gönderim " + ("AÇILDI." if sending else "KAPATILDI."))
            elif key == ord('m'):
                mirror = not mirror
            elif key == ord('h'):
                target_hand = "Left" if target_hand == "Right" else "Right"
                print(f"Hedef el: {target_hand}")
            elif key == ord('o'):
                if hand.found:
                    tracker.calibrate_open()
                else:
                    print("Kalibrasyon için el görünmüyor. Elini aç ve tekrar 'O'ya bas.")
            elif key == ord('c'):
                if hand.found:
                    tracker.calibrate_close()
                else:
                    print("Kalibrasyon için el görünmüyor. Yumruk yap ve tekrar 'C'ye bas.")
            elif key == ord('k'):
                tracker.calibrate_skin()
            elif key == ord('x'):
                tracker.reset_calibration()
            elif key == ord('z'):
                if robot_connected:
                    print("[Z] Yeniden home + self-test...")
                    hand_robot.home()
                    print_robot_info(hand_robot)
                else:
                    print("Önce 'R' ile robotu bağla.")
    except KeyboardInterrupt:
        pass
    finally:
        cap.release()
        cv2.destroyAllWindows()
        tracker.close()
        if robot_connected:
            hand_robot.disconnect()
        print("Kapatıldı.")


if __name__ == "__main__":
    main()
