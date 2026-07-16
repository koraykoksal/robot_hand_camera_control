# -*- coding: utf-8 -*-
"""
Merkezi Konfigürasyon Dosyası
==============================

Tüm sistem ayarları bu dosyadan yönetilir. Kodun farklı yerlerine dağılmış
sabit değerler yerine, değişiklikleri tek yerden yapın.

Bu dosyayı nasıl kullanırım?
----------------------------
Kod içinde şöyle import edilir:

    from settings import (
        CONNECT_MAX_ATTEMPTS,
        RECONNECT_DELAY_S,
        ...
    )

Değer değiştirmek için:
    - Sadece bu dosyayı düzenleyin
    - Uygulamayı yeniden başlatın (değerler başlangıçta okunur)

KURAL: Bu dosyada sadece SABIT DEĞERLER olur. Kod mantığı yok.
"""

from __future__ import annotations


# ============================================================================
# BAĞLANTI VE RECONNECT AYARLARI
# ============================================================================
# Bu değerler robot el bağlantısının nasıl kurulacağını ve kopan bağlantıyı
# nasıl kurtaracağını belirler.

# İlk bağlantı için kaç kez denenecek?
# Bağlanma sırasında slave geçici olarak cevap vermeyebilir (firmware settle,
# kablo kontağı vs). Bu değer kadar deneriz.
# Öneri: 3-5 arası
CONNECT_MAX_ATTEMPTS: int = 5

# Her bağlantı denemesi arası bekleme (saniye)
# Slave firmware'inin kendine gelmesi için süre tanırız.
CONNECT_RETRY_DELAY_S: float = 1.5

# Auto-reconnect için kaç kez denenecek?
# Bağlantı koptuğunda sistem otomatik olarak yeniden bağlanmaya çalışır.
# Sonsuz döngü istemiyoruz ama kablo takılma gibi durumlarda yeterli
# süre tanımalıyız.
# ÖNERI: Kablo çıkma senaryosunu düşünürsek YÜKSEK tutmalı (10-20)
RECONNECT_MAX_ATTEMPTS: int = 10

# Her auto-reconnect denemesi arası bekleme (saniye)
# Kullanıcı kabloyu yeniden takmak için zaman kazanır.
RECONNECT_RETRY_DELAY_S: float = 3.0

# Auto-reconnect başlamadan önce bekleme (saniye)
# Slave'in "kendine gelmesi" için süre. Çok kısa olursa slave hazır olmadan
# bağlanmaya çalışırız.
RECONNECT_INITIAL_WAIT_S: float = 5.0

# Auto-reconnect sonsuz döngü modu açık mı?
# True: kablo takılana kadar sonsuz şekilde dener
# False: RECONNECT_MAX_ATTEMPTS kadar dener, sonra vazgeçer
# ENDÜSTRIYEL KULLANIM için önerilen: True
RECONNECT_INFINITE: bool = True

# Sonsuz reconnect modunda denemeler arası bekleme (saniye)
# Çok agresif denemek Windows/driver'ı zorlayabilir, 5-10 ideal.
RECONNECT_INFINITE_DELAY_S: float = 5.0


# ============================================================================
# ETHERCAT PROTOKOL TIMING (DİKKAT - DEĞİŞTİRMEYİN)
# ============================================================================
# Bu değerler EtherCAT protokol timing'i için kritiktir.
# Değiştirmek watchdog tetiklenme riskini artırır.

# IO thread PDO döngü süresi (saniye)
# Her bu süre kadar aralıkla PDO paketi gönderilir.
# Robot el SM watchdog'u 100ms — bundan çok daha hızlı olmalı.
# Windows jitter göz önüne alındığında 4-8ms güvenli aralık.
PDO_CYCLE_S: float = 0.004   # 4 ms

# PDO receive timeout (mikrosaniye)
# Cevap beklerken maksimum süre.
# Genelde cycle süresi ile aynı tutulur.
PDO_RX_TIMEOUT_US: int = 4000   # 4 ms

# Master state check aralığı (saniye)
# IO thread her bu süre kadar master state'ini kontrol eder.
STATE_CHECK_INTERVAL_S: float = 1.0

# OP state'ten düşme tespit eşiği (saniye)
# State bu süre kadar OP değilse recover tetiklenir.
BAD_STATE_RECOVER_THRESHOLD_S: int = 5

# Recover'ın başarısız sayılması için kaç kez denedikten sonra?
# Bu sayıya ulaşılırsa needs_reconnect=True olur ve auto-reconnect tetiklenir.
RECOVER_MAX_FAILURES: int = 3

# SAFEOP state'e geçiş maksimum bekleme (saniye)
# Bağlantı kurulumunda kullanılır.
SAFEOP_TRANSITION_TIMEOUT_S: float = 2.0


# ============================================================================
# HAREKET AYARLARI (Varsayılan değerler)
# ============================================================================
# Bunlar pozisyon dosyasında (positions.json) override edilebilir.

# Varsayılan motor hızı (encoder birim/saniye)
# Aralık: 1000 (çok yavaş) - 20000 (çok hızlı)
# 12000 = normal hız
DEFAULT_VELOCITY: int = 12000

# Varsayılan maksimum akım (motor güç sınırı)
# Aralık: 100-1500
# 500 = normal, 700 = havada test, 400 = hassas kavrama
DEFAULT_MAX_CURRENT: int = 500

# Hareket tamamlanma bekleme süresi (saniye)
# Pozisyona ulaşma için maksimum bekleme.
# Yavaş hareketler için artırın, hızlı test için azaltın.
DEFAULT_MOVE_TIMEOUT_S: float = 5.0

# Pozisyon toleransı (encoder sayımı)
# Hedef ve gerçek pozisyon arasındaki kabul edilebilir fark.
# Küçük = hassas ama mekanik salınım sorun yaratabilir
# Büyük = toleranslı ama yanlış "ulaştı" raporu olabilir
DEFAULT_POS_TOLERANCE: int = 300

# Motorlar arası enable settle süresi (saniye)
# enable_all sırasında motorlar arası bekleme.
ENABLE_SETTLE_S: float = 0.05

# TPDO decode loop döngü süresi (saniye)
# Status monitor thread'inin döngü hızı.
TPDO_SLEEP_S: float = 0.01


# ============================================================================
# ALARM YÖNETİMİ
# ============================================================================
# Motor alarm durumlarının nasıl ele alınacağını belirler.

# Alarm rate-limit penceresi (saniye)
# Bu süre içinde tekrar eden alarmlar sayılır.
ALARM_RATE_WINDOW_S: float = 3.0

# Alarm rate-limit eşiği (sayı)
# Bu sayıda alarm olunca motor kilitlenir (auto-reset durdurulur).
# Kullanıcı manuel müdahale etmeden motor yeniden çalışamaz.
ALARM_RATE_THRESHOLD: int = 2


# ============================================================================
# İZLEME VE LOG
# ============================================================================

# Status monitor log aralığı (saniye)
# Her bu süre kadar [HH:MM:SS] Baglanti-OK / Baglanti-NOK logu basar.
# Konsol kirliliğini azaltmak için 10-30 saniye makul.
MONITOR_INTERVAL_S: float = 10.0

# Verbose log açık mı?
# True: detaylı debug mesajları ("Master state not OP" gibi)
# False: sadece önemli olaylar
VERBOSE_LOGGING: bool = False

# ============================================================================
# PERİYODİK KONTROL DAVRANIŞI (TCP modu için optimize edilebilir)
# ============================================================================
# Bu bayraklar arka plan thread'lerinin davranışını kontrol eder.
# TCP modunda CPU yükünü azaltmak için kapatılabilir.

# Status monitor thread'i çalışsın mı?
# True:  her MONITOR_INTERVAL_S'de motor durumu okunur (CPU yükü)
# False: sadece istek geldiğinde okunur (önerilen TCP için)
ENABLE_STATUS_MONITOR: bool = False

# Otomatik state recover thread'i çalışsın mı?
# True:  her STATE_CHECK_INTERVAL_S'de master state kontrol edilir
#        OP'tan düşerse otomatik recover yapılır
# False: state kontrol edilmez, hareket sırasında sorun olursa fark edilir
#        (DIKKAT: Bu kapatılırsa SM watchdog koruması zayıflar)
ENABLE_STATE_MONITOR: bool = True

# Periyodik bağlantı log mesajları yazdırılsın mı?
# True:  [HH:MM:SS] Baglanti-OK gibi mesajlar her interval'de yazılır
# False: sadece bağlantı durumu DEĞİŞTİĞİNDE yazılır (önerilen)
PERIODIC_CONNECTION_LOG: bool = False


# ============================================================================
# GRIP TESPİT EŞİKLERİ (Nesne kavrama algılama)
# ============================================================================
# Bu değerler robot elin bardak/nesne kavrayıp kavramadığını anlamak için
# kullanılır. Donanımınıza göre kalibre edilmiş olmalı.
#
# Mantık:
#   - Havada hareket:     max_current ~50-100 (çok düşük)
#   - Bardak kavrama:     max_current 400-700 (yüksek, limit yakın)
#   - Aşırı sıkma:        max_current limit + çok yüksek sapma (tehlike)

# GRIP_OK için minimum akım eşiği
# Bu değerin üstünde akım çekilirse "nesne kavrandı" denir
# Normal havada çekilen akımın BİRKAÇ KATI olmalı
GRIP_CURRENT_THRESHOLD: int = 100

# GRIP_OK için minimum pozisyon sapması
# Motor hedefine ulaşamayıp durmuş olması gerekir (nesneye çarptı)
# Küçük sapmalar (50-500) normal, büyük sapmalar (500+) nesne kavraması
GRIP_DEVIATION_THRESHOLD: int = 1500

# OVERGRIP için maksimum akım eşiği
# NOT: Bu sadece tek başına yeterli değil - PLUS pozisyon sapması da yüksek olmalı
# Yani motor limiti aşan akım çekerken hâlâ hedefe çok uzaksa = aşırı sıkma
# Sizin donanımda cur=700 limit olduğu için bu değeri 650-680 arası yapın
OVERGRIP_CURRENT: int = 680

# NO_OBJECT için maksimum pozisyon sapması
# Bu değerin altında sapma varsa "hedefe ulaştı" sayılır
NO_OBJECT_DEVIATION: int = 500


# ============================================================================
# ERKEN GRIP TESPİTİ (Hızlı cevap için)
# ============================================================================
# Motor akım limite dayandığında ve hareket etmiyorsa, tüm timeout'u beklemeden
# "kavrama tamamlandı" diye erken çıkış yapılır.
#
# Bu sayede bardak kavrama 5 saniye yerine 1-2 saniyede tamamlanır.

# Erken çıkış aktif mi?
# True: hareket yavaş durumlarda erken çıkış yapılır (HIZLI)
# False: her zaman timeout_s kadar beklenir (YAVAŞ ama güvenli)
EARLY_EXIT_ON_GRIP: bool = True

# Yüksek akım eşiği (erken çıkış için)
# Motor bu değerden fazla akım çekiyorsa "zorlanıyor" sayılır
EARLY_EXIT_CURRENT_THRESHOLD: int = 400

# Motor kaç milisaniye sabit kalırsa "durdu" sayılır?
EARLY_EXIT_STABLE_MS: int = 400

# Kaç motor bu kriterleri sağlarsa erken çıkış yapılır?
# 6 DOF robot el için 3 (bardak kavramada 5 parmak aktif)
EARLY_EXIT_MIN_MOTORS: int = 3


# ============================================================================
# HAREKET ÖNCESİ HAZIRLIK (PRE-MOVE)
# ============================================================================

# Hareket öncesi otomatik hazırlık açık mı?
# True: her move_to_position öncesi motor alarmları temizlenir,
#       disabled motorlar enable edilir
# False: hiçbir ön hazırlık yapılmaz
# KAPALI kullanmak daha hızlı ama hatalara karşı dayanıksız olur.
PREP_BEFORE_MOVE: bool = True

# Her hareketten önce home yapılsın mı?
# True: HER hareket öncesi motorlar home pozisyonuna gönderilir (yavaşlar)
# False: sadece açıkça istenirse home yapılır
# Bu genelde False tutulur - home yavaş bir işlemdir.
HOME_BEFORE_MOVE: bool = False

# Otomatik reset açık mı?
# True: alarm oluştuğunda otomatik clear_alarm çağrılır
# False: alarm manuel temizlenmedikçe motor disable kalır
AUTO_RESET: bool = True


# ============================================================================
# TEST AYARLARI (test_mp.py için)
# ============================================================================

# Test loop'unda varsayılan iterasyon sayısı
TEST_DEFAULT_ITERATIONS: int = 10

# Test hareketleri arası bekleme (saniye)
# Mekanik settle ve motor soğuması için.
TEST_SETTLE_AFTER_MOVE_S: float = 1.0

# Test pozisyon toleransı (encoder)
# Testte daha toleranslı olmak için normal değerden yüksek.
TEST_POS_TOLERANCE: int = 500


# ============================================================================
# DONANIM TANIMLARI
# ============================================================================

# Robot el serbestlik derecesi
# Donanım 11 DOF'a kadar destekliyor ama kullanılan 6 DOF (5 parmak + 1 rotasyon)
ROBOT_DOF: int = 6

# Motor encoder değer aralığı
MOTOR_MIN: int = 0       # Tam açık (düz parmak)
MOTOR_MAX: int = 10000   # Tam kapalı (kıvrık parmak)



# ============================================================================
# KAMERA AYARLARI
# ============================================================================
# Kullanılacak kamera index'i camera_select.py ile bulunur (aşağıda CAMERA_INDEX).
# Dahili webcam genelde 0, harici USB kamera 1.


# Kamera penceresi modu:
#   "static"     -> CAMERA_WIDTH x CAMERA_HEIGHT sabit boyut
#   "maximize"   -> ekran boyutuna büyüt (başlık çubuğu kalır)
#   "fullscreen" -> tam ekran (kenarlıksız)
CAMERA_WINDOW_MODE: str = "maximize"
CAMERA_WIDTH: int = 640     # kameranın doğal çözünürlüğü = en iyi performans
CAMERA_HEIGHT: int = 480


# Kamera backend'i (string olarak tutulur ki settings.py cv2'ye bağımlı olmasın).
# "dshow" -> Windows DirectShow (önerilen)
# "any"   -> Linux/Mac veya otomatik
CAMERA_BACKEND: str = "dshow"

# ============================================================================
# ETHERCAT ADAPTER (Otomatik bağlantı)
# ============================================================================
# run_api.py açılışta bu index'teki adapter'a otomatik bağlanır.
# Doğru index'i bulmak için: sunucuyu bir kez başlatın; index hatalıysa
# konsola mevcut adapter listesi yazılır, oradan doğru numarayı görüp buraya yazın.
ECAT_ADAPTER_INDEX: int = 5


# Bu kıvrılma değerinin altı %0 sayılır (açık el / gürültü temizliği)
CURL_DEADZONE: float = 0.15

# NOT: EMA_ALPHA artık KULLANILMIYOR (One Euro filtresine geçildi). Etkisi yok.
# EMA_ALPHA: float = 0.3
# Kamera->robot yumuşatma (One Euro filtresi)
DEADBAND: int = 300              # bu encoder farkından küçük değişim gönderilmez
ONE_EURO_MIN_CUTOFF: float = 1.0 # küçük = durağanda daha az titreşim
ONE_EURO_BETA: float = 0.3       # büyük = harekette daha az gecikme

# Başparmak BÜKME (yukarı-aşağı, motor1) kalibrasyonu - IP eklem açısı
# Açık elde BasF %0 olmuyorsa (motor1 içeri gitmiş görünüyorsa):
# thumb_test.py'de elinizi rahat açıp BasF satırındaki canlı "aci" değerini
# okuyun ve THUMB_OPEN_DEG'i o değere (veya 2-3 derece altına) çekin.
THUMB_OPEN_DEG: float = 160.0    # düz/açık başparmak -> motor 0
THUMB_CLOSED_DEG: float = 120.0  # kıvrık başparmak -> motor MAX

# Başparmak rotasyon (sağa-sola / abduction) kalibrasyonu
# ÖLÇÜM DEĞİŞTİ: artık başparmak UCU üzerinden (avuca kapanmayı yakalar).
# Bu yüzden ESKİ değerler geçersiz - thumb_test.py çalıştırıp ekrandaki
# "Oneri" satırındaki değerleri buraya yazın.
ABDUCT_LO_DEG: float = 34.0   # açık el (~33° ölçüldü) -> motor 0
ABDUCT_HI_DEG: float = 18.0   # avuca tam kapanık (~17° ölçüldü) -> motor MAX

THUMB_MIN_CUTOFF: float = 0.3    # başparmağa güçlü yumuşatma (titreme; daha da titrerse 0.2)
ABDUCT_DEADZONE: float = 0.12    # BasR gürültü tabanını 0'a çeker

CAMERA_INDEX: int = 0          # TEK kamera (önden). camera_select.py ile doğrulayın.
CAMERA_INDEX_2: int = 1        # (kullanılmıyor - ikinci kamera kapalı)
CAMERA_2_ENABLED: bool = False # <-- TEK KAMERA MODU (iki kamera iptal)

# Tek kamera modunda hepsi cam1'den gelir (CAMERA_2_ENABLED=False iken kod zaten cam1'e zorlar)
MOTOR_SOURCE = {
    "thumb": "cam1", "thumb_rot": "cam1",
    "index": "cam1", "middle": "cam1",
    "ring": "cam1", "pinky": "cam1",
}

MIN_DETECTION_CONFIDENCE: float = 0.8   # yüksek = daha az yanlış tespit
MIN_TRACKING_CONFIDENCE: float = 0.6
MIN_HAND_SIZE: float = 0.18             # bu boyutun altındaki tespiti reddet
DETECT_DEBOUNCE: int = 3                # el 3 ardışık karede görülmeden "var" sayma



# ============================================================================
# ROBOT PARMAK HIZI (kamera teleop - camera_hand_bridge.py okur)
# ============================================================================
# VELOCITY_MODE:
#   "dynamic" -> robot parmağı SİZİN el hızınızı takip eder (önerilen)
#                yavaş hareket = yavaş motor, hızlı hareket = hızlı motor
#   "fixed"   -> her hareket sabit SAFE_VELOCITY hızıyla yapılır
VELOCITY_MODE: str = "dynamic"

# Dinamik mod hız sınırları (encoder/saniye, donanım aralığı 1000-20000)
# NOT: VEL_MIN = VEL_MAX = 20000 -> hız hep tavanda (en seri tepki, kullanıcı tercihi)
VEL_MIN: int = 20000
VEL_MAX: int = 20000

# Sabit mod hızı (VELOCITY_MODE = "fixed" iken kullanılır)
SAFE_VELOCITY: int = 8000

# Kamera teleop akım limiti (parmak kuvveti; düşük = nazik)
SAFE_MAX_CURRENT: int = 400

# Köprü açılırken robot gönderimi açık mı başlasın?
# Çalışırken 'r' tuşu ile her an açıp kapatabilirsiniz.
# False: kamera salt-izleme modunda başlar (robot bağlı değilken gereksiz istek atılmaz)
ROBOT_START_ON: bool = False

# Hangi el takip edilsin? "right" (sağ), "left" (sol), "any" (fark etmez)
# Robot el SAĞ el olduğu için "right" - kameradaki SOL el tamamen yok sayılır.
# NOT: Sağ eliniz algılanmıyor/ters çalışıyorsa "left" yazın (bazı kamera/ayna
# kombinasyonlarında MediaPipe etiketi ters gelebilir).
HAND_FILTER: str = "right"

# --- El kaybolunca otomatik HOME ---
# El kameradan HOME_DELAY_S süre boyunca görünmezse motorlar 0'a (tam açık /
# home pozisyonu) döner. Gecikme, anlık algılama kopmalarında sıçramayı önler.
HOME_ON_HAND_LOST: bool = True
HOME_DELAY_S: float = 1.0      # el bu kadar saniye kaybolursa home'a dön
HOME_VELOCITY: int = 8000      # home dönüş hızı (kontrollü; 20000 yapmayın)
