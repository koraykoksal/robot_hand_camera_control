# LHandPro Kamera Kontrol — v2

Kullanıcının ÇALIŞAN uygulamasındaki (hand_control_webcam / camera_hand_bridge.py)
kıvrım matematiği ve parametreleri birebir alınmış; robot tarafı EtherCAT SDK'sı ile
entegre edilmiştir.

## v1'e göre DÜZELTİLEN hatalar
| Konu | v1 (hatalı) | v2 (çalışan uygulamadan) |
|---|---|---|
| Motor 1 / 2 | başparmak rotasyon / bükme | **motor1=BÜKME, motor2=ROTASYON** |
| Parmak açı aralığı | 175° -> 55° (çok geç doyuyor) | **175° -> 90°** |
| Başparmak rotasyon | bilek açısı MCP(2) ile | **UÇ(4) ile** (hareketi kaçırmaz) |
| Ölü bölge | sadece kesiyor (sıçrama) | **kesip yeniden ölçekliyor** |
| Gönderim | 25 Hz, deadband 200 | **15 Hz, deadband 300** |
| Akım | 600 | **400 (SAFE_MAX_CURRENT)** |

## Kurulum
```bash
pip install -r requirements.txt
```
`sdk/` klasörünü doldur (vendor .py'leri + DLL'ler), Npcap kurulu olsun.

## Çalıştırma
```bash
python main.py
```
Kalibrasyon YOK — açı aralıkları settings.py'de sabit ve kanıtlanmış.
1. Sağ elini kameraya göster
2. **R** — robotu bağla (home + self-test otomatik)
3. **SPACE** — hareketleri robota göndermeye başla

## Tuşlar
| Tuş | İşlev |
|---|---|
| q / ESC | Çıkış |
| r | Robot bağlan / kes |
| space | Robota göndermeyi aç/kapa |
| z | Robot home + self-test |
| m | Ayna |
| h | Hedef el (Right/Left) |

## Ayarlar (settings.py, bölüm 8)
Parmak "tepki vermiyor" gibi hissedersen:
- `ANGLE_CLOSED_DEG` (90) — büyüt (örn 100) = daha erken tam kapanır/daha hassas
- `CURL_DEADZONE` (0.15) — küçült = küçük hareketler de geçer
- `THUMB_OPEN_DEG/CLOSED_DEG` (160/120) — başparmak aralığı
- `ABDUCT_LO_DEG/HI_DEG` (45/12) — başparmak rotasyon aralığı

Titreme olursa:
- `ONE_EURO_MIN_CUTOFF` (1.0) küçült, `POS_SEND_DEADBAND` (300) büyüt

## Ek özellikler
- **El kadrajdan çıkınca** (`HOME_ON_HAND_LOST`) robot eli 1 sn sonra açık konuma döner
- **Sağlam kamera açıcı** (`camera.py`): DSHOW+MJPG -> MJPG'siz -> MSMF -> ANY -> 640x480
  sırayla denenir, her birinde gerçekten kare gelip gelmediği doğrulanır
- `robot_tools.py` — home / alarm / enable / kuvvet, kameradan bağımsız
