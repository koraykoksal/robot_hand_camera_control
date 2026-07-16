# Robotik El Kontrol Uygulaması

Insan̉sı robotik el (6 DOF) için sadeleştirilmiş kontrol uygulaması.
EtherCAT üzerinden haberleşir, CLI arayüz sunar. Mevcut `rogolob22.py`
dosyasındaki çalışan mantık korunmuş ama GUI / TCP / kamera / RPS oyunu
gibi gereksiz katmanlardan arındırılmıştır.

## Klasör yapısı

```
hand_control/
├── main.py                     ← giriş noktası
├── README.md
├── config/
│   ├── ecat_config.json        ← seçilen adapter (ilk bağlantıda oluşur)
│   └── positions.json          ← pozisyon kütüphanesi
├── core/
│   ├── LHandProLib.dll         ← donanım SDK (değişmez)
│   ├── ethercat_master.py      ← pysoem wrapper (değişmez)
│   ├── lhandprolib_loader.py   ← ctypes yükleyici (değişmez)
│   ├── lhandprolib_wrapper.py  ← PyLHandProLib (değişmez)
│   └── hand_controller.py      ← YENİ: merkezi kontrol sınıfı
├── services/
│   ├── position_manager.py     ← YENİ: pozisyon CRUD
│   └── adapter_resolver.py     ← YENİ: Npcap friendly name + config
└── cli/
    └── menu.py                 ← YENİ: interaktif menü
```

## Kurulum

Windows üzerinde çalışır (EtherCAT + Npcap gereksinimi).

```bash
pip install pysoem
```

Ayrıca [Npcap](https://npcap.com/) kurulu olmalı (**"WinPcap API-compatible Mode"** ile).

## Çalıştırma

```bash
cd hand_control
python main.py
```

Opsiyonel: farklı pozisyon dosyası kullanmak için:

```bash
python main.py C:\yol\benim_pozisyonlarim.json
```

## Menü akışı

1. **[1]** Adaptörleri tara → EtherCAT uyumlu ağ kartlarını okunabilir isimlerle listeler (Npcap friendly name)
2. **[2]** Bağlan → seçilen adaptörden master'ı başlatır, SAFEOP → OP geçişi yapar, **seçimi `config/ecat_config.json`'a kaydeder**
3. **[3]** Motorları Enable et → 6 motoru position mode'a alır
4. **[4]** Home yap → home sekansını çalıştırır
5. **[5]-[9]** Pozisyon işlemleri
6. **[10]** Canlı durum izle (açı/pozisyon/akım/alarm)
7. **[11]** Alarm temizle / **[12]** auto-reset

## EtherCAT adaptör seçimi

EtherCAT TCP/IP protokolü değildir, dolayısıyla IP/port bilgisine gerek yoktur.
Sadece hangi ağ kartının (veya USB-to-Ethernet dönüştürücünün) kullanılacağı
belirtilir.

İlk çalıştırmada [1] ile adaptörleri tarayın; Npcap yüklüyse isimler okunabilir
halde görünür (örn. "Realtek USB GbE Family Controller"). Seçtiğiniz adaptör
otomatik olarak `config/ecat_config.json`'a kaydedilir, sonraki açılışlarda
`← son kullanılan` işaretiyle görünür ve Enter'a basarak hızlıca bağlanabilirsiniz.

USB dönüştürücüyü farklı porta takarsanız bile, uygulama önce raw GUID, sonra
display name üzerinden eşleşme arayarak adaptörü tekrar bulmaya çalışır.

## Kod API olarak kullanımı

CLI dışında, `HandController`'ı doğrudan import edip kendi uygulamanıza
(GUI, web servisi, test scripti vb.) entegre edebilirsiniz:

```python
from core.hand_controller import HandController
from services.position_manager import PositionManager

# 1) Bağlan
hand = HandController(dof=6, auto_reset=True)
adapters = hand.scan_interfaces()
hand.connect(adapter_index=0)

# 2) Motorları hazırla
hand.enable_all()
hand.home_all()

# 3) Pozisyon kütüphanesinden hareket
pm = PositionManager("config/positions.json")
rock = pm.get("ROCK")
hand.move_to_position(rock.pos, velocity=rock.vel, max_current=rock.cur)
hand.wait_reached(timeout_s=3.0)

# 4) Doğrudan encoder değeri
hand.move_to_position([0, 0, 5000, 5000, 0, 0])

# 5) Durum okuma
for s in hand.get_status():
    print(s)   # Motor#1 pos=5000 cur=120 alarm=0 en=1 reached=1

# 6) Hata kontrolü
if hand.has_alarms():
    print(hand.get_active_alarms())
    hand.clear_alarms()

# 7) Güvenli kapanış
hand.disconnect()
```

## rogolob22.py ile farklar

| Konu | rogolob22.py | Yeni uygulama |
|---|---|---|
| Toplam satır | ~4700 | ~1200 (kontrol mantığı için ~500) |
| Global state | `g_ec_master`, `_tpdo_thread` vb. modül-global | `HandController` örnek alanları |
| Arayüz | Tkinter GUI + TCP server + kamera | Sade CLI (GUI sonra eklenecek) |
| Pozisyon formatı | 2 ayrı dosya (basit + genişletilmiş) | Tek dosya, otomatik format algılama |
| Alarm yönetimi | Manuel | Arka plan monitor + opsiyonel auto-reset |
| Reconnect | Manuel UI butonu | `on_disconnected` callback ile sinyal |
| Kapatma | Thread'ler leak'leyebiliyor | Context manager + `disconnect()` temiz |

## Sonraki adımlar

- GUI (tkinter veya PyQt) eklenmek istendiğinde `HandController` ve
  `PositionManager` doğrudan kullanılır; tek yapılacak UI bağlama.
- Otomatik reconnect stratejisi: `on_disconnected` callback'inden
  `hand.scan_interfaces()` + `hand.connect(idx)` zinciri tetiklenir.
- Daha gelişmiş pozisyon kütüphanesi: sekanslar (pozisyon dizileri),
  interpolasyon, hızlı/yavaş modlar.
