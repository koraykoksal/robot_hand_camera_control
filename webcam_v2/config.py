# -*- coding: utf-8 -*-
"""
config.py — Tüm ayarlar tek yerde.
Robotu ilk kez bağlarken MUTLAKA MOCK_ROBOT = True ile başla,
vision (kamera) tarafını doğrula, sonra False yapıp gerçek ele geç.
"""

# =========================================================================
# 1) ROBOT SDK YOLU
# =========================================================================
# Paylaştığın 7z içindeki RS485_python (veya CANFD_python) klasörünün yolu.
# İçinde lhandpro_controller.py, lhandprolib_wrapper.py, config.py, DLL vb. olmalı.
# Örn Windows: r"C:\lhand_api_windows\x64\share\LHandProLib\examples\RS485_python"
SDK_PYTHON_DIR = r"C:\lhand_api_windows\x64\share\LHandProLib\examples\RS485_python"

# Robota gerçekten komut göndermeden önce True bırak (sadece kamera + sahte robot).
# Vision tarafı düzgün çalışınca False yap.
MOCK_ROBOT = True

# İletişim tipi: "RS485" | "CANFD" | "ECAT"
COMM_MODE = "RS485"

# RS485 için (Aygıt Yöneticisi'nden COM portuna bak). None => otomatik seç.
RS485_PORT_NAME = None          # örn "COM5"
RS485_BAUD_RATE = 500000
RS485_NODE_ID = 1

# Bağlanınca motorları etkinleştir + sıfırla (home). İlk denemede True kalsın.
ENABLE_MOTORS = True
HOME_MOTORS = True
HOME_WAIT_TIME = 5.0

# =========================================================================
# 2) ROBOT EL — EKLEM HARİTASI (6-DOF)
# =========================================================================
# 6-DOF motor sırası (SDK enum C_LMI6 ile birebir):
#   motor 1: baş parmak yana açılma (thumb abduction)
#   motor 2: baş parmak bükülme     (thumb flexion)
#   motor 3: işaret parmağı bükülme
#   motor 4: orta parmak bükülme
#   motor 5: yüzük parmağı bükülme
#   motor 6: serçe parmağı bükülme
#
# Her eklem için (acik_pozisyon, kapali_pozisyon) enkoder sayacı.
# Kameradan gelen normalize edilmiş değer 0.0(açık)..1.0(kapalı) buraya map edilir.
# GÜVENLİK: küçük aralıkla başla, ele bakarak yönü/limiti kalibre et.
# Yön ters çalışırsa (acik, kapali) değerlerini yer değiştir.
JOINT_RANGES = {
    "thumb_abduction": (0, 8000),
    "thumb_flexion":   (0, 10000),
    "index":           (0, 10000),
    "middle":          (0, 10000),
    "ring":            (0, 10000),
    "pinky":           (0, 10000),
}

# Robota komut gönderirken kullanılacak hız ve akım (akım = tork limiti gibi düşün).
# GÜVENLİK: düşük akımla başla; parmak bir yere sıkışırsa düşük akım zarar vermez.
MOVE_VELOCITY = 20000
MAX_CURRENT = 600          # önce düşük tut (örn 400-600), güvenince artır

# =========================================================================
# 3) KUVVET SENSÖRLERİ
# =========================================================================
USE_FORCE_SENSORS = True
# Parmak ucu (tip) sensör id'leri — SDK enum C_LSS_FINGER_x_1
FINGERTIP_SENSOR_IDS = {
    "thumb":  1,   # C_LSS_FINGER_1_1
    "index":  3,   # C_LSS_FINGER_2_1
    "middle": 5,   # C_LSS_FINGER_3_1
    "ring":   7,   # C_LSS_FINGER_4_1
    "pinky":  9,   # C_LSS_FINGER_5_1
}
# Bu kuvvetin (Newton) üstünde parmak "temas ediyor/bastırıyor" say ve ekranda vurgula.
FORCE_CONTACT_THRESHOLD = 0.3
# Ekrandaki kuvvet barının tepe değeri (görselleştirme ölçeği).
FORCE_DISPLAY_MAX = 5.0

# =========================================================================
# 4) KAMERA + EL TAKİBİ
# =========================================================================
CAMERA_INDEX = 0
FRAME_WIDTH = 960
FRAME_HEIGHT = 540

# Hangi eli kontrol edeceğiz. MediaPipe etiketi. Sağ el için "Right".
# NOT: Ayna görüntüde etiket ters dönebilir; ekrandaki "Detected:" yazısına bak,
#      gerekirse 'h' tuşu ile hedef eli değiştir.
TARGET_HAND = "Right"
MIRROR_VIEW = True          # selfie gibi ayna görüntü (daha doğal). 'm' ile değiştir.

MIN_DETECTION_CONFIDENCE = 0.7   # yükseltmek dış etkenlere/yanlış tespitlere karşı sağlamlaştırır
MIN_TRACKING_CONFIDENCE = 0.6
MIN_PRESENCE_CONFIDENCE = 0.5    # el görünürlüğü bunun altındaysa kareyi yok say

# Parmak kıvrımı yumuşatma (EMA). 0=yumuşatma yok/titrek, 1=çok yavaş.
SMOOTHING = 0.5
# Bir parmağın "hareket ediyor" sayılması için kare başına min kıvrım değişimi.
MOVE_THRESHOLD = 0.05

# Robota komut gönderme frekansı (Hz). Kamera daha hızlı olsa da robotu boğmayalım.
ROBOT_SEND_HZ = 25
