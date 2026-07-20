# -*- coding: utf-8 -*-
"""
main.py — Robot el TCP (socket) sunucusunu başlatır.

Çalıştır:
    python main.py

İlk testte settings.MOCK_ROBOT = True bırak (robot olmadan protokolü dene).
"""
import signal
import sys

import settings as config
from socket_server import RobotSocketServer

APP = "LHandPro Socket Sunucusu"
VER = "1.0.0"


def main():
    print("=" * 62)
    print(f" {APP}  v{VER}")
    print("-" * 62)
    print(f"  Robot modu : {'MOCK (sahte)' if config.MOCK_ROBOT else 'REAL (gercek)'}")
    print(f"  Iletisim   : {config.COMM_MODE}")
    print(f"  Dinleme    : {config.TCP_HOST}:{config.TCP_PORT}")
    print(f"  Cevap ekleri: {config.REPLY_OK} / {config.REPLY_NOK}")
    print("=" * 62)

    server = RobotSocketServer()

    if config.AUTO_CONNECT_ROBOT:
        server.connect_robot()          # başarısız olsa da sunucu ayakta kalır
    else:
        print("[Robot] Otomatik bağlanma kapalı; 'CONNECT' komutunu bekliyor.")

    if not server.start():
        return 1

    def _bye(sig, frm):
        print("\n[Kapatiliyor...]")
        server.stop()
        sys.exit(0)

    signal.signal(signal.SIGINT, _bye)
    try:
        signal.signal(signal.SIGTERM, _bye)
    except Exception:
        pass

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
