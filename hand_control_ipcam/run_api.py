# -*- coding: utf-8 -*-
"""
REST API Server Başlatıcı
==========================

Kullanım:
    python run_api.py

Varsayılan URL:
    http://localhost:8000

Swagger UI (interaktif API dokümanı):
    http://localhost:8000/docs

Yerel ağdan erişim için (api_settings.py'de API_HOST="0.0.0.0" olmalı):
    http://<PC-IP>:8000

Gereksinimler:
    pip install fastapi uvicorn
"""

from __future__ import annotations

import os
import sys
import multiprocessing as mp


# ---------------------------------------------------------------------------
# sys.path setup
# ---------------------------------------------------------------------------
_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
for _sub in ("core", "services", "cli", "config", "api"):
    _path = os.path.join(_THIS_DIR, _sub)
    if _path not in sys.path:
        sys.path.insert(0, _path)
if _THIS_DIR not in sys.path:
    sys.path.insert(0, _THIS_DIR)


def main():
    """API server'ı başlat."""
    # Önce fastapi/uvicorn'un yüklü olduğunu kontrol et
    try:
        import uvicorn
        import fastapi  # noqa: F401
    except ImportError:
        print("=" * 60)
        print("❌ HATA: fastapi ve uvicorn yüklü değil")
        print("=" * 60)
        print()
        print("Lütfen kurun:")
        print("    pip install fastapi uvicorn")
        print()
        return 1

    # API ayarlarını yükle
    try:
        import api_settings
    except ImportError as e:
        print(f"❌ api_settings yüklenemedi: {e}")
        return 1

    # Banner
    print("=" * 60)
    print(f"  🚀 {api_settings.API_TITLE} v{api_settings.API_VERSION}")
    print("=" * 60)
    print(f"  Host     : {api_settings.API_HOST}")
    print(f"  Port     : {api_settings.API_PORT}")
    print(f"  Log      : {api_settings.API_LOG_LEVEL}")
    print(f"  API Key  : {'Gerekli' if api_settings.API_KEY_REQUIRED else 'Gerekmiyor'}")
    print(f"  Auto-start worker: {api_settings.AUTO_START_WORKER}")
    print("=" * 60)
    print()

    if api_settings.API_HOST == "0.0.0.0":
        # Yerel ağ IP'sini göster
        try:
            import socket
            hostname = socket.gethostname()
            local_ip = socket.gethostbyname(hostname)
            print(f"  📡 Yerel ağdan erişim:")
            print(f"     http://{local_ip}:{api_settings.API_PORT}")
            print(f"     http://{local_ip}:{api_settings.API_PORT}/docs  (Swagger UI)")
            print()
        except Exception:
            pass

    # uvicorn ile server'ı başlat
    # Not: server.py'deki FastAPI app'i import eder
    uvicorn.run(
        "server:app",
        host=api_settings.API_HOST,
        port=api_settings.API_PORT,
        log_level=api_settings.API_LOG_LEVEL,
        reload=False,  # Multiprocessing ile uyumsuz, production'da False
    )

    return 0


if __name__ == "__main__":
    # Windows multiprocessing için kritik
    mp.freeze_support()
    sys.exit(main())
