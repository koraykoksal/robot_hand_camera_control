# -*- coding: utf-8 -*-
"""
settings.py — Socket (TCP) uygulaması ayarları.

NOT: Bu dosya bilerek 'config.py' DEĞİL 'settings.py' olarak adlandırıldı.
Çünkü robot SDK'sının kendi 'config.py' modülü var (aslında SDK'da eksik gelir;
biz onu robot_interface.py içinde bu dosyadaki değerlerden OTOMATİK üretiyoruz).
İki ayrı 'config.py' olsaydı Python'da isim çakışırdı. Sen her şeyi buradan ayarla.

Robotu ilk kez bağlarken MUTLAKA MOCK_ROBOT = True ile başla.
"""
import os

_APP_DIR = os.path.dirname(os.path.abspath(__file__))

# =========================================================================
# 1) ROBOT SDK YOLU + MOD
# =========================================================================
# SDK dosyalarını uygulama içindeki 'sdk/' klasörüne koy. Erişim buradan yapılır.
# 'sdk/' içine EtherCAT_python .py dosyaları + gerekli DLL'ler bırakılır (README'ye bak).
SDK_PYTHON_DIR = os.path.join(_APP_DIR, "sdk")

# True: gerçek ele komut GİTMEZ (sahte robot). Önce hep True ile test et.
MOCK_ROBOT = False

# "ECAT" | "CANFD" | "RS485"  — robot fiziksel olarak EtherCAT ile çalışıyor.
COMM_MODE = "ECAT"

# =========================================================================
# 2) BAĞLANTI PARAMETRELERİ
# =========================================================================
# --- EtherCAT (ECAT) ---
# Robotun bağlı olduğu ağ kartının (NIC) indeksi. EtherCAT KABLOLU Ethernet ister
# (Wi-Fi olmaz). Doğru indeksi görmek için:  python list_nics.py  çalıştır.
# Senin makinende 【5】 Intel(R) Ethernet I219-LM fiziksel port; robot oradaysa 5 olmalı.
# None => otomatik ilk kartı seçer (genelde YANLIŞ olur), o yüzden elle ayarla.
ECAT_NIC_INDEX = 5
# NOT: Windows'ta EtherCAT için 'pysoem' + 'Npcap' gerekir (aşağıdaki README'ye bak).

# --- RS485 (kullanılmıyorsa dokunma; vendor 'config' için gerekli) ---
RS485_PORT_NAME = None
RS485_BAUD_RATE = 500000
RS485_NODE_ID = 1
CANFD_NODE_ID = 1               # CANFD modu kullanırsan

ENABLE_MOTORS = True
HOME_MOTORS = True              # bağlanınca sıfırla (home)
HOME_WAIT_TIME = 5.0
ENABLE_HOME_CHECK = True        # False yaparsan home olmadan da hareket eder (dikkat)

# Bağlanınca hazırlık + self-test (alarm temizle -> enable -> home -> kuvvet -> durum raporu)
PREPARE_ON_CONNECT = True
# True ise tüm motorlar enable=1 ve alarmsız değilse bağlantı "başarısız" sayılır.
REQUIRE_ALL_ENABLED = False

# El tipi: 0 = 6-DOF, 1 = 6-DOF(S), 2 = 16-DOF   (SDK: LAC_DOF_*)
HAND_TYPE = 0

# =========================================================================
# 3) ⚙️ HIZ ve KUVVET AYARLARI  (senin asıl istediğin bölüm)
# =========================================================================
# --- HIZ ---
# Pozisyon kontrol hızı (move_to_positions için). Büyük = daha hızlı parmak.
MOVE_VELOCITY = 20000
# Açı kontrol hızı (derece/sn) — açı tabanlı kontrol kullanırsan.
ANGULAR_VELOCITY = 200.0

# --- KUVVET / TORK ---
# Maksimum akım ≈ parmağın uygulayabileceği tork/kuvvet limiti.
# GÜVENLİK: DÜŞÜK başla. Parmak bir yere sıkışırsa düşük akım hasar vermez.
# Tipik başlangıç: 400-600. Güvendikçe kademeli artır.
MAX_CURRENT = 400        # calisan uygulamadaki SAFE_MAX_CURRENT

# Tork (kuvvet) kontrol modunu aç. Açıkça kuvvet sınırlı tutmak için True.
ENABLE_TORQUE_CONTROL = True

# İstersen parmak başına ayrı akım (kuvvet) limiti ver. None => hepsi MAX_CURRENT.
# Anahtarlar: thumb_abduction, thumb_flexion, index, middle, ring, pinky
PER_JOINT_MAX_CURRENT = {
    # "index": 500,
    # "thumb_flexion": 700,
}

# =========================================================================
# 4) EKLEM HARİTASI (6-DOF)  — açık/kapalı enkoder pozisyonları
# =========================================================================
# Kamera parmağı -> ROBOT MOTOR ID eşlemesi.
# Robotta parmaklar ters bağlıysa BURADAN düzelt. Örn. bu robotta orta ve yüzük
# ters çalışıyorsa: "middle": 5, "ring": 4  yaparak yer değiştir.
# (Varsayılan sıra motor 1..6: baş-yana, baş-bük, işaret, orta, yüzük, serçe)
MOTOR_MAP = {
    # ÇALIŞAN uygulamadan: motor 1 = başparmak BÜKME, motor 2 = başparmak ROTASYON
    "thumb_flexion":   1,
    "thumb_abduction": 2,
    "index":           3,
    "middle":          4,
    "ring":            5,
    "pinky":           6,
}
# Kameradan gelen 0.0(açık)..1.0(kapalı) değeri buraya map edilir.
# Ele bakarak kalibre et. Yön ters çalışırsa (açık, kapalı) değerlerini yer değiştir.
JOINT_RANGES = {
    "thumb_abduction": (0, 8000),
    "thumb_flexion":   (0, 10000),
    "index":           (0, 10000),
    "middle":          (0, 10000),
    "ring":            (0, 10000),
    "pinky":           (0, 10000),
}

# Robota komut gönderme frekansı (Hz).
ROBOT_SEND_HZ = 15        # calisan uygulamadaki SEND_HZ

# =========================================================================
# 5) KUVVET SENSÖRLERİ
# =========================================================================
USE_FORCE_SENSORS = True
FINGERTIP_SENSOR_IDS = {         # SDK: C_LSS_FINGER_x_1 (parmak ucu)
    "thumb":  1,
    "index":  3,
    "middle": 5,
    "ring":   7,
    "pinky":  9,
}
FORCE_CONTACT_THRESHOLD = 0.3    # bu N üstünde "temas" say ve ekranda vurgula
FORCE_DISPLAY_MAX = 5.0          # kuvvet barı tepe değeri (görselleştirme)


# =========================================================================
# 6) SOCKET (TCP) SUNUCUSU
# =========================================================================
TCP_HOST = "0.0.0.0"        # tüm ağ arayüzlerinden bağlantı kabul et
TCP_PORT = 9090
BUFFER_SIZE = 4096
MAX_CLIENTS = 8

# İstemciden istek gelmediğinde uygulama HATAYA DÜŞMEZ; bu süre dolunca
# sadece döngü devam eder (bağlantı açık kalır).
CLIENT_IDLE_TIMEOUT_S = 60.0
ACCEPT_TIMEOUT_S = 1.0      # accept() bloklamasın; Ctrl+C ile temiz çıkış
TCP_KEEPALIVE = True

# Cevap ekleri: "BARDAK_AL" -> "BARDAK_ALOK" / "BARDAK_ALNOK"
REPLY_OK = "OK"
REPLY_NOK = "NOK"

# Açılışta robota otomatik bağlan (False ise CONNECT komutu ile bağlanır)
AUTO_CONNECT_ROBOT = True
# Robot bağlı değilken komut gelirse otomatik yeniden bağlanmayı dene
AUTO_RECONNECT = True

# Hareket sonrası hedefe ulaşma toleransı ve bekleme
MOVE_TIMEOUT_S = 5.0
POS_TOLERANCE = 300

POSES_PATH = os.path.join(_APP_DIR, "poses.json")
LOG_REQUESTS = True
