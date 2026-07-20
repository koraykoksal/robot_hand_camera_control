# LHandPro Kamera Kontrol — v2 (SAF OpenCV)

MediaPipe KULLANMAZ. El tespiti ve parmak kıvrımı klasik görüntü işleme ile yapılır:

```
ROI kırp -> ten rengi maskesi (YCrCb) -> morfoloji -> en büyük kontur (el)
 -> distance transform ile AVUÇ MERKEZİ + YARIÇAPI
 -> bilek yönünden el yönelimi
 -> her parmak için AÇISAL SEKTÖR içindeki en uzak kontur noktası = parmak uzunluğu
 -> uzunluk / avuç yarıçapı  ->  kıvrım (0=açık, 1=kapalı)
```

Robot tarafı (EtherCAT, kuvvet sensörü, MOTOR_MAP, home/self-test) v1 ile AYNIDIR.

## Kurulum
```bash
pip install opencv-python numpy pysoem
```
mediapipe GEREKMEZ. Model dosyası da gerekmez.

`sdk/` klasörünü v1'deki gibi doldur (vendor .py'leri + DLL'ler) ve Npcap kurulu olsun.

## Çalıştırma ve KALİBRASYON (sırayla yap — çok önemli)
```bash
python main.py
```
1. **Elini ekrandaki yeşil ROI kutusuna koy**, parmaklar YUKARI, avuç kameraya dönük.
2. **`K`** — ten rengini öğret (el kutunun ortasındayken bas). Sağ üstteki "maske"
   önizlemesinde elin BEYAZ, arka planın SİYAH olması gerekir.
3. **`O`** — elini tamamen AÇ ve bas. Parmak sektör açıları + açık uzunluklar öğrenilir.
4. **`C`** — YUMRUK yap ve bas. Kapalı uzunluklar öğrenilir.
5. **`R`** — robotu bağla (home + self-test otomatik).
6. **SPACE** — hareketleri robota göndermeyi başlat.

Kalibrasyon `models/cv_calibration.json`'a kaydedilir; sonraki açılışta otomatik yüklenir.

## Tuşlar
| Tuş | İşlev |
|---|---|
| q / ESC | Çıkış |
| k | Ten rengini öğren |
| o | AÇIK el kalibrasyonu (sektör açıları + açık uzunluk) |
| c | YUMRUK kalibrasyonu (kapalı uzunluk) |
| x | Kalibrasyonu sıfırla |
| r | Robot bağlan / kes |
| space | Robota göndermeyi aç/kapa |
| z | Robot home + self-test |
| m | Ayna |

## Ayarlar (settings.py, bölüm 8)
- `ROI_REL` — elin duracağı kutu (x, y, genişlik, yükseklik; 0..1 oran)
- `SKIN_CR`, `SKIN_CB` — ten rengi eşikleri ('k' ile otomatik ayarlanır)
- `SECTOR_HALF_DEG` — parmak sektör yarı genişliği. Parmaklar birbirine
  karışıyorsa KÜÇÜLT (örn 10), parmak bulunamıyorsa BÜYÜT (örn 16)
- `FINGER_MIN_R` / `FINGER_MAX_R` — kalibrasyon yoksa kullanılan varsayılan aralık
- `MIN_CONTOUR_AREA` — gürültüyü ele saymamak için alt sınır
- `SHOW_MASK` — maske önizlemesini göster/gizle

## Kamera görüntüsü gelmiyor / pencere donuyor
`camera.py` yapılandırmaları sırayla dener ve her birinde GERÇEKTEN kare gelip
gelmediğini doğrular: DSHOW+MJPG -> MJPG'siz -> MSMF -> ANY -> 640x480 -> varsayılan.
Açılışta konsolda hangisinin tuttuğu yazar: `[Kamera] ✅ 960x540 @ 30 FPS (backend=...)`.
Hiçbiri tutmazsa `settings.CAMERA_INDEX`'i 1 / 2 dene, kamerayı kullanan başka
uygulamayı kapat.

## El dışındaki nesneler
Her kontur el-benzerlik testinden geçer; geçmeyen EL SAYILMAZ:
- alan çok küçük (gürültü) veya ROI'nin `MAX_AREA_RATIO`'sundan büyük (arka plan) -> elenir
- doluluk `SOLIDITY_RANGE` dışında -> yüz gibi dolu/yuvarlak lekeler elenir
- en/boy `ASPECT_RANGE` dışında -> kol/kablo gibi ince şeritler elenir

## Sorun giderme
| Belirti | Çözüm |
|---|---|
| El bulunamıyor | `K` ile ten rengini yeniden öğret; maske önizlemesine bak |
| Arka plan da beyaz görünüyor | Sade/koyu arka plan kullan, ten rengine yakın duvardan kaçın |
| Parmaklar karışıyor | `SECTOR_HALF_DEG`'i küçült, elini dik tut, `O` ile yeniden kalibre et |
| Açık elde değer var | `O` (açık el) kalibrasyonunu tekrarla |
| Yumrukta 1.0'a ulaşmıyor | `C` (yumruk) kalibrasyonunu tekrarla |

## v1 (MediaPipe) ile karşılaştırma
| | v1 MediaPipe | v2 OpenCV |
|---|---|---|
| Doğruluk | Yüksek (21 nokta, 3B) | Orta (kontur tabanlı) |
| Işık/arka plan | Dayanıklı | HASSAS |
| Hız | ~20-40 ms | ~2 ms |
| Kurulum | Model dosyası gerekir | Sadece OpenCV |
| El konumu | Serbest | ROI içinde, dik durmalı |
