# -*- coding: utf-8 -*-
"""
robot_tools.py — Kameradan BAĞIMSIZ robot el bakım/hazırlık aracı.

Home, alarm temizleme, enable/disable, kuvvet (akım) ayarı, self-test ve
sıfıra gitme gibi işlemleri kamera uygulamasını açmadan tek tek tetikler.

Çalıştır:
  python robot_tools.py            -> menü açılır
  python robot_tools.py home       -> bağlan + home + çık
  python robot_tools.py clear      -> bağlan + alarm temizle + çık
  python robot_tools.py enable     -> motorları enable et
  python robot_tools.py disable    -> motorları disable et
  python robot_tools.py zero       -> tüm parmakları 0 (açık) konuma getir
  python robot_tools.py status     -> self-test tablosu
  python robot_tools.py force 500  -> kuvvet (max akım) limitini 500 yap

MOCK_ROBOT=True iken sahte robotla çalışır (donanım gerekmez).
"""
import sys

import settings as config
from robot_interface import create_hand, JOINT_ORDER, _current_for, _map_to_position


def _status_table(hand):
    rows = hand.self_check()
    print(f"{'motor':<20}{'enable':<8}{'alarm':<7}{'aci':<8}{'akim'}")
    for m in rows:
        en = "1" if m["enabled"] else "0"
        al = "-" if m["alarm"] == 0 else str(m["alarm"])
        flag = "" if (m["enabled"] and m["alarm"] == 0) else "  <-- DIKKAT"
        print(f"{str(m['id'])+' '+m['name']:<20}{en:<8}{al:<7}{m['angle']:<8.1f}{m['current']}{flag}")
    bad = [m for m in rows if not m["enabled"] or m["alarm"] != 0]
    print("Durum:", "⚠️ bazı motorlar hazır değil" if bad else "✅ tüm motorlar enable=1, alarmsız")


def _home(hand):
    print("Home (sıfırlama)...")
    if hasattr(hand, "controller") and hand.controller:
        hand.controller.home(config.HOME_WAIT_TIME)
    else:
        hand.home()
    print("Home tamam.")


def _clear(hand):
    print("Alarm temizleniyor...")
    if hasattr(hand, "controller") and hand.controller:
        hand.controller.clear_alarm()
    print("Alarm temizlendi.")


def _enable(hand, on=True):
    print(("Enable" if on else "Disable") + " ediliyor...")
    if hasattr(hand, "controller") and hand.controller:
        hand.controller.enable_motors(on)
    print("Tamam.")


def _zero(hand):
    print("Tüm parmaklar 0 (açık) konuma getiriliyor...")
    if hasattr(hand, "controller") and hand.controller:
        hand.controller.move_to_zero(config.MOVE_VELOCITY, config.MAX_CURRENT, 1.0)
    print("Tamam.")


def _force(hand, value):
    print(f"Kuvvet (max akım) limiti {value} yapılıyor...")
    if hasattr(hand, "lhp") and hand.lhp:
        for i in range(max(hand.dof_active, 6)):
            try:
                hand.lhp.set_max_current(i + 1, int(value))
            except Exception as e:
                print(f"  motor {i+1}: {e}")
    print("Tamam. (Kalıcı olması için settings.MAX_CURRENT'i de güncelle.)")


def run_command(hand, cmd, arg=None):
    if cmd == "home":
        _home(hand)
    elif cmd == "clear":
        _clear(hand)
    elif cmd == "enable":
        _enable(hand, True)
    elif cmd == "disable":
        _enable(hand, False)
    elif cmd == "zero":
        _zero(hand)
    elif cmd == "status":
        _status_table(hand)
    elif cmd == "force":
        _force(hand, arg if arg is not None else config.MAX_CURRENT)
    else:
        print("Bilinmeyen komut:", cmd)


MENU = """
==================== ROBOT ARAÇLARI ====================
  1) Home (sıfırla)
  2) Alarm temizle
  3) Motorları enable et
  4) Motorları disable et
  5) Sıfıra git (parmaklar açık)
  6) Self-test (durum tablosu)
  7) Kuvvet (max akım) ayarla
  q) Çıkış
=======================================================
Seçim: """


def menu_loop(hand):
    actions = {"1": "home", "2": "clear", "3": "enable",
               "4": "disable", "5": "zero", "6": "status"}
    while True:
        try:
            sel = input(MENU).strip().lower()
        except (EOFError, KeyboardInterrupt):
            break
        if sel == "q":
            break
        elif sel in actions:
            run_command(hand, actions[sel])
        elif sel == "7":
            val = input(f"Yeni kuvvet (akım) limiti [{config.MAX_CURRENT}]: ").strip()
            run_command(hand, "force", int(val) if val.isdigit() else config.MAX_CURRENT)
        else:
            print("Geçersiz seçim.")


def main():
    print(f"Robot modu: {'MOCK (sahte)' if config.MOCK_ROBOT else 'REAL'} | "
          f"İletişim: {config.COMM_MODE}")
    hand = create_hand()
    if not hand.connect():
        print("❌ Robot bağlanamadı. settings.py / donanım / Npcap / sdk DLL'lerini kontrol et.")
        return
    try:
        args = sys.argv[1:]
        if args:
            cmd = args[0].lower()
            arg = int(args[1]) if len(args) > 1 and args[1].isdigit() else None
            run_command(hand, cmd, arg)
        else:
            menu_loop(hand)
    finally:
        hand.disconnect()
        print("Kapatıldı.")


if __name__ == "__main__":
    main()
