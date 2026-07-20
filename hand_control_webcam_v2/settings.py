# -*- coding: utf-8 -*-
"""
settings.py — v2 (OpenCV) — Tüm ayarlar burada (hız ve kuvvet dahil).

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
MOCK_ROBOT = True

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
# 6) KAMERA + EL TAKİBİ
# =========================================================================
CAMERA_INDEX = 0
FRAME_WIDTH = 960
FRAME_HEIGHT = 540
MAXIMIZE_WINDOW = True           # açılışta kamera penceresini büyüt (maximize)

# --- PERFORMANS (FPS / gecikme) ---
# Windows'ta DirectShow + MJPG çoğu webcam'de FPS'i 2-3 katına çıkarır
# (varsayılan YUY2 formatı yüksek çözünürlükte 5-15 FPS'e düşer).
CAMERA_BACKEND = "DSHOW"         # "DSHOW" (Windows önerilen) | "MSMF" | "ANY"
CAMERA_FOURCC = "MJPG"           # "MJPG" | "" (kapat)
CAMERA_FPS = 60                  # kameradan istenen FPS
CAMERA_BUFFERSIZE = 1            # gecikmeyi azaltır (eski kareleri biriktirmez)

# Çıkarım (inference) için kareyi küçült; çizim tam çözünürlükte kalır.
# 480-640 arası genelde doğruluk kaybı olmadan belirgin hız kazandırır. 0 = kapalı.
INFER_WIDTH = 480

# Kaç el aranacak. 1 = avuç-tespit maliyeti yarıya iner (tek el kullanıyoruz).
NUM_HANDS = 1

# Ekranda FPS + çıkarım süresini (ms) göster (performansı ölçmek için).
SHOW_PERF = True

# MediaPipe Tasks model dosyası (0.10.x'te eski 'solutions' API'si kaldırıldı;
# HandLandmarker Tasks API'si bir .task model dosyası ister). Yoksa otomatik indirilir.
# İstersen "full" modeli indirip bu yola koyabilirsin (aynı dosya adıyla), o kullanılır.
MODEL_PATH = os.path.join(_APP_DIR, "models", "hand_landmarker.task")
MODEL_URL = ("https://storage.googleapis.com/mediapipe-models/hand_landmarker/"
             "hand_landmarker/float16/1/hand_landmarker.task")

# MediaPipe çalışma modu: "LIVE_STREAM" (önerilen, async/akıcı) | "VIDEO" (senkron/yedek)
MEDIAPIPE_MODE = "LIVE_STREAM"

TARGET_HAND = "Right"            # sağ el
MIRROR_VIEW = True

MIN_DETECTION_CONFIDENCE = 0.8
MIN_TRACKING_CONFIDENCE = 0.6
MIN_PRESENCE_CONFIDENCE = 0.5

# --- Yumuşatma ---
# One-Euro filtresi (MediaPipe'ın da önerdiği): yavaş hareketde titremeyi keser,
# hızlı harekette GECİKME YAPMAZ. Basit EMA'dan belirgin daha iyi.
# (v2'de kullanilmiyor) USE_ONE_EURO = True
# Ölçülen denge: EMA kadar durağan, ama harekete neredeyse anında yanıt.
# (v2'de kullanilmiyor) OE_MIN_CUTOFF = 2.5              # küçült -> daha durağan/yumuşak, büyüt -> daha çevik
# (v2'de kullanilmiyor) OE_BETA = 1.0                    # büyüt -> hızlı harekette daha az gecikme
SMOOTHING = 0.65                 # sadece USE_ONE_EURO=False iken kullanılır (EMA)
MOVE_THRESHOLD = 0.05            # parmak "hareket ediyor" eşiği

# TİTREME ÖNLEME + "hareketsiz parmağa komut gönderme":
# Bir parmağın hedef pozisyonu bu kadar (enkoder sayacı) değişmedikçe O PARMAĞA
# yeni komut/kuvvet GÖNDERİLMEZ (parmak başına). Böylece hareketsiz parmak
# sürekli komut/kuvvet almaz; sadece hareket eden parmak güncellenir.
POS_SEND_DEADBAND = 300   # calisan uygulamadaki DEADBAND

# --- Kalibrasyon (açık el = 0, kapalı el = 1) ---
# Gerçek parmak açıkken bile tam düz olmadığı için ham kıvrım 0 çıkmaz.
# Uygulamada: elini AÇ -> 'O', yumruk yap -> 'C'. Değerler diske kaydedilir.
CALIB_PATH = os.path.join(_APP_DIR, "models", "calibration.json")
# DİNLENME BANDI (ham kıvrım uzayında): parmak, açık-el referansının bu kadar
# üstüne çıkmadıkça pozisyon TAM 0 kalır. Açık elde parmaklar doğal olarak hafif
# kıvrık göründüğü ve kamera gürültüsü olduğu için gereklidir.
# Büyütürsen açık el daha kesin 0 olur ama hareketi algılamak için parmağı biraz
# daha fazla kıvırman gerekir. 0.15-0.25 arası iyi çalışır.
# (v2'de kullanilmiyor) CURL_REST_BAND = 0.20
# Son güvenlik: normalize sonuç bunun altındaysa 0 yap.
CURL_DEADZONE = 0.03

# =========================================================================
# 7) KONSOL ÇIKTILARI (loglar)
# =========================================================================
CONSOLE_LOG = True               # yapılan hareketleri konsola yaz
LOG_HZ = 5                       # log frekansı (Hz) — 25 Hz'de konsolu boğmamak için
LOG_ONLY_ON_MOVE = False         # True: sadece bir parmak hareket ederken logla




# =========================================================================
# 8) KIVRIM AÇI ARALIKLARI (çalışan uygulamadan birebir)
# =========================================================================
# 4 parmak: PIP eklem açısı. 175 = düz/açık, 90 = tam kıvrık.
# (90 yerine 55 kullanmak parmağın çok geç doymasına, "tepki vermiyor" hissine yol açar)
ANGLE_OPEN_DEG = 175.0
ANGLE_CLOSED_DEG = 90.0

# Başparmak BÜKME: IP eklem açısı. Dar aralık -> hassas.
THUMB_OPEN_DEG = 160.0
THUMB_CLOSED_DEG = 120.0

# Başparmak ROTASYON (yayılma): bilekte UÇ(4)-işaretMCP(5) açısı.
# 45° (yayılmış/açık el) -> motor 0 ;  12° (avuca kapanık) -> motor MAX
ABDUCT_LO_DEG = 45.0
ABDUCT_HI_DEG = 12.0

# Ölü bölgeler (uygulanır ve KALAN ARALIK YENİDEN ÖLÇEKLENİR)
CURL_DEADZONE = 0.15
ABDUCT_DEADZONE = 0.12

# One-Euro katsayıları (çalışan uygulamadan)
ONE_EURO_MIN_CUTOFF = 1.0
ONE_EURO_BETA = 0.3
THUMB_MIN_CUTOFF = 0.5           # başparmak daha durağan

# El kadrajdan çıkınca robot eli sıfıra (açık) döndür
HOME_ON_HAND_LOST = True
HAND_LOST_DELAY_S = 1.0
