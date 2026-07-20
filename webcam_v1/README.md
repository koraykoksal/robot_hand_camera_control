# LHandPro — Kamera ile El Hareketi Kontrolü (EtherCAT)

Web kameradan **sağ eli** takip eder, parmak kıvrımlarını ölçer, LHandPro robot elini
**EtherCAT** üzerinden gerçek zamanlı yönlendirir ve parmak uçlarındaki
**kuvvet sensörlerini** okur. Çalışırken **SDK/firmware sürümünü** ve **yapılan hareketleri**
konsola yazar. Robot bağlı değilken de çalışan **MOCK modu** vardır.

## Kurulum
```bash
pip install -r requirements.txt
```

### MediaPipe modeli (otomatik)
mediapipe 0.10.x eski `solutions` API'sini kaldırdı; proje yeni **HandLandmarker (Tasks)**
API'sini kullanır. Bunun için küçük bir model dosyası (`hand_landmarker.task`) gerekir.
İlk çalıştırmada `models/` klasörüne **otomatik indirilir** (internet gerekir). İnmezse
elle indirip `models/hand_landmarker.task` olarak koy (URL settings.py → MODEL_URL).

### Windows'ta EtherCAT için Npcap (ZORUNLU)
Python EtherCAT master `pysoem` kütüphanesini kullanır; bu da Windows'ta **Npcap** ister.
1. Arşivdeki `x64/bin/npcap-1.82.exe` dosyasını çalıştır.
2. Kurulumda **"Install Npcap in WinPcap API-compatible Mode"** seçeneğini işaretle.
3. Bilgisayarı **yeniden başlat**.
Npcap kurulu değilse ağ kartları görünmez ("no adapters") hatası alırsın.

## 📁 sdk/ klasörü — SDK dosyalarını buraya koy
Uygulama, SDK'ya kendi içindeki **`sdk/`** klasöründen erişir (`settings.SDK_PYTHON_DIR`
otomatik olarak bu klasörü gösterir). `sdk/BURAYA_SDK_DOSYALARINI_KOY.txt` içinde ayrıntı var.
Özetle `sdk/` şöyle olmalı:
```
sdk/
├── lhandprolib_loader.py        ┐
├── lhandprolib_wrapper.py       │  x64/share/LHandProLib/examples/EtherCAT_python/
├── lhandpro_controller.py       │  klasöründen
├── ethercat_master.py           ┘
├── LHandProLib.dll              ┐
├── msvcp140.dll (+ _1, _2)      │  x64/bin/ klasöründen
├── vcruntime140.dll (+ _1)      │  (VC++ Redistributable x64 kuruluysa runtime'lar opsiyonel)
└── concrt140.dll                ┘
```
Neden DLL'ler de `sdk/` içine? Klasör yapısı değiştiği için loader, DLL'i otomatik ararken
önce **kendi klasörüne** (sdk/) bakar. DLL'leri buraya koyunca sorunsuz bulur.
(Sonu `d.dll` ile bitenler DEBUG sürümüdür, gerekmez. `wpcap.dll`/`Packet.dll` yalnızca C++
SOEM örneği içindir; Python `pysoem` yerine Npcap kullanır, bu iki DLL gerekmez.)

## Çalışınca konsolda ne görürsün
Açılışta bir bilgi banner'ı:
```
============================================================
 LHandPro Kamera Kontrol  v1.0.0
------------------------------------------------------------
  Robot modu     : REAL (gerçek robot)
  İletişim       : ECAT
  SDK yolu       : ...\lhand_camera_control\sdk
  Firmware sürümü: 0.32
  DOF (total/akt): 6 / 6
  EtherCAT slave : 1
  Hız (velocity) : 20000   |  Kuvvet (akım): 600
  Tork kontrolü  : AÇIK
============================================================
```
Ardından her hareket (log frekansıyla) tek satır:
```
[  3.2s|GÖNDER] başp 0.15-> 1500  işrt*0.88-> 8800|1.4N!  orta 0.05->  500  yüzk 0.62-> 6200  serç*0.91-> 9100|2.1N!   << hareket: index, pinky
```
Okuma: `parmak  kıvrım(0..1) -> hedef_pozisyon | kuvvet(N)`. `*` = o parmak hareket ediyor,
`!` = temas eşiği aşıldı (kuvvet var). `GÖNDER` = robota komut gidiyor, `izle` = sadece izleniyor.
Log ayarları `settings.py` → "7) KONSOL ÇIKTILARI" (CONSOLE_LOG, LOG_HZ, LOG_ONLY_ON_MOVE).

## ⚙️ Hız ve Kuvvet ayarları — hepsi settings.py'de
"3) HIZ ve KUVVET AYARLARI":
- MOVE_VELOCITY — parmak hareket hızı (pozisyon hızı)
- ANGULAR_VELOCITY — açı tabanlı kontrol hızı (derece/sn)
- MAX_CURRENT — **kuvvet/tork limiti** (akım ≈ tork). Düşük başla (400-600)!
- ENABLE_TORQUE_CONTROL — tork kontrol modu
- PER_JOINT_MAX_CURRENT — parmak başına ayrı kuvvet limiti

## Çalıştırma
```bash
python main.py
```
1. settings.py → MOCK_ROBOT = True bırak. Kamerayı aç, sağ elini oynat; iskeletin
   renklendiğini, kıvrım/kuvvet barlarını ve konsol loglarını gör. "Algilanan" yazısına bak;
   sağ elin "Left" görünüyorsa h ile hedef eli değiştir ya da m ile aynayı değiştir.
2. Vision düzgünse: Npcap'i kur, sdk/ klasörünü doldur, robotu ağ kartına EtherCAT ile bağla,
   settings.py → MOCK_ROBOT = False. Birden çok NIC varsa ECAT_NIC_INDEX ile doğru kartı seç.
   MAX_CURRENT'ı düşük tut.
3. Programı aç. Robot artık açılışta otomatik bağlanmaz — kamera penceresinde **R** tuşuna
   basınca bağlanır (bağlanınca konsolda firmware sürümü + slave sayısı görünür). Sonra **SPACE**
   ile göndermeyi başlat. JOINT_RANGES (açık/kapalı) değerlerini
   ele bakarak kalibre et; bir eklem ters çalışırsa o eklemin (açık, kapalı) değerlerini yer değiştir.

## Pencere
Açılışta kamera penceresi otomatik **maximize** olur (settings.MAXIMIZE_WINDOW = False ile kapatılır).

## Tuşlar
| Tuş | İşlev |
|-----|-------|
| q / ESC | Çıkış |
| **r** | Robot el bağlantısını yap/kes |
| space | Robota komut göndermeyi aç/kapa (önce R ile bağlan) |
| m | Ayna görüntü aç/kapa |
| h | Hedef eli değiştir (Right ↔ Left) |

## Dosyalar (bu proje)
- settings.py — TÜM ayarlar (sdk yolu, EtherCAT, hız, kuvvet, eklem haritası, sensör, kamera, log)
- hand_tracker.py — MediaPipe ile sağ el tespiti + parmak kıvrımı
- robot_interface.py — kıvrım→pozisyon haritalama, komut, sensör okuma, firmware sürümü, MockHand
- visualizer.py — renklendirme, hareket vurgusu, kuvvet göstergeleri
- main.py — ana döngü, açılış banner'ı, hareket logları
- sdk/ — SDK dosyalarını buraya koy (vendor .py + DLL'ler)

## 16-DOF el kullanıyorsan
settings.HAND_TYPE = 2 yap ve robot_interface.JOINT_ORDER + settings.JOINT_RANGES
haritasını SDK'daki C_LMI16_* sırasına göre 16 eksene genişlet.

## ⚠️ Güvenlik
- Her zaman MOCK_ROBOT=True ile başla.
- MAX_CURRENT düşük başlasın (parmak sıkışırsa düşük akım zarar vermez).
- JOINT_RANGES aralıklarını dar tutup kademeli genişlet.
- SPACE başta KAPALI gelir; kontrolü sende tutar.
