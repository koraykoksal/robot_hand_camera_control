# -*- coding: utf-8 -*-
"""
Robotik El Kontrol Uygulaması - Ana Giriş Noktası

Kullanım:
    python main.py                            # config/positions.json varsayılan
    python main.py C:\\yol\\positions.json    # özel pozisyon dosyası

Gereksinimler (Windows):
    - Python 3.10+
    - Npcap (WinPcap API-compatible Mode)
    - pip install pysoem
    - core/LHandProLib.dll (zaten dahil)
"""

from __future__ import annotations

import os
import sys

# ---------------------------------------------------------------------------
# sys.path kurulumu
# ---------------------------------------------------------------------------
# Bu uygulamayı paket olarak değil, düz script olarak çalıştırıyoruz.
# Tüm alt modül klasörlerini sys.path'a ekleyerek
# "from hand_controller import ..." gibi düz import'ların çalışmasını sağlıyoruz.

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))

for _subdir in ("core", "services", "cli", "config"):
    _path = os.path.join(_THIS_DIR, _subdir)
    if _path not in sys.path:
        sys.path.insert(0, _path)

# Proje kökünü de ekle
if _THIS_DIR not in sys.path:
    sys.path.insert(0, _THIS_DIR)


# ---------------------------------------------------------------------------
# Uygulama başlatma
# ---------------------------------------------------------------------------
from menu import main as cli_main


def _resolve_positions_path(arg):
    """Pozisyon dosya yolunu çöz. Argüman verilmediyse config/positions.json."""
    if arg:
        return arg
    return os.path.join(_THIS_DIR, "config", "positions.json")


def _resolve_adapter_config_path():
    """Adapter tercihi dosyası her zaman config/ecat_config.json."""
    return os.path.join(_THIS_DIR, "config", "ecat_config.json")


if __name__ == "__main__":
    pos_file = _resolve_positions_path(sys.argv[1] if len(sys.argv) > 1 else None)
    adapter_file = _resolve_adapter_config_path()
    print(f"📂 Pozisyon dosyası: {pos_file}")
    print(f"📂 Adapter config:   {adapter_file}")
    cli_main(
        positions_file=pos_file,
        adapter_config_file=adapter_file,
    )
