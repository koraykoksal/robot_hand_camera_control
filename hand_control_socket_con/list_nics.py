# -*- coding: utf-8 -*-
"""
list_nics.py — EtherCAT için ağ kartlarını (NIC) listeler.
Buradaki indeks numarasını settings.ECAT_NIC_INDEX'e yaz.

Çalıştır:  python list_nics.py

Robot KABLOLU Ethernet ister (Wi-Fi/Bluetooth/WAN Miniport olmaz).
Genelde "Intel(R) Ethernet ... I219-LM" gibi fiziksel bir Ethernet kartıdır.
"""
import sys
import settings as config

sys.path.insert(0, config.SDK_PYTHON_DIR)

try:
    from ethercat_master import EthercatMaster
except Exception as e:
    print("ethercat_master içe aktarılamadı. sdk/ klasörünü ve pysoem/Npcap kurulumunu kontrol et.")
    print("Hata:", e)
    sys.exit(1)

print("Ağ kartları taranıyor...\n")
m = EthercatMaster()
names = m.scanNetworkInterfaces()   # filtrelenmiş listeyi 【0..N】 diye yazdırır
print(f"\nToplam {len(names)} kart bulundu.")
print("Yukarıdaki 【indeks】 numarasından, robotun bağlı olduğu KABLOLU Ethernet kartını seç")
print("ve settings.py içinde  ECAT_NIC_INDEX = <indeks>  olarak ayarla.")
