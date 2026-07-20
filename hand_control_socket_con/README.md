# LHandPro — Robot El Socket (TCP) Uygulaması

Socket yayınını dinler, istemciden gelen **metin isteği** işler, robot ele uygular ve
**OK / NOK** cevabı döner. **İstemciden istek gelmediğinde uygulama hataya düşmez.**

## Kurulum
```bash
pip install -r requirements.txt
```
`sdk/` klasörünü doldur (vendor .py'leri + DLL'ler), Npcap kurulu olsun.

## Çalıştırma
```bash
python main.py            # sunucu (varsayilan 0.0.0.0:9090)
python client_test.py     # test istemcisi (etkilesimli)
python client_test.py PING ZERO BARDAK_AL     # tek seferlik komutlar
```
İlk testte `settings.MOCK_ROBOT = True` bırak — robot olmadan protokolü denersin.
Gerçek robot için `False` yap ve `ECAT_NIC_INDEX` ayarını kontrol et.

## Protokol (düz metin)
Satır sonu (`\n`) **opsiyoneldir** — PLC/Lua istemciler `\n` göndermeden de çalışır.

| İstek | Cevap |
|---|---|
| `PING` | `PONG` |
| `HEALTH` | `HEALTHOK` / `HEALTHNOK` |
| `STATUS` | JSON durum bilgisi |
| `POSES` | tanımlı poz adları (virgülle) |
| `CONNECT` | `CONNECTOK` / `CONNECTNOK` |
| `DISCONNECT` | `DISCONNECTOK` |
| `RECONNECT` | `RECONNECTOK` / `RECONNECTNOK` |
| `HOME` | `HOMEOK` / `HOMENOK` |
| `<POZ_ADI>` | `<POZ_ADI>OK` / `<POZ_ADI>NOK` |

Örnek: `BARDAK_AL` -> `BARDAK_ALOK`

### JSON komutlar (opsiyonel)
```json
{"cmd":"ping"}
{"cmd":"status"}
{"cmd":"poses"}
{"cmd":"pose","name":"ZERO"}
{"cmd":"move","pos":[0,0,0,0,0,0],"vel":15000,"cur":400}
```
Cevap: `{"status":"ok"|"nok","message":"..."}`

## Pozlar — poses.json
Hazır: `ZERO, ACIK, YUMRUK, BARDAK_AL, BARDAK_BIRAK, BYE_UP, BYE_DOWN, TEMIZLIK, ISARET`

Yeni poz eklemek için `poses.json`'a ekle (motor sırası: başparmak-bükme,
başparmak-rotasyon, işaret, orta, yüzük, serçe):
```json
"YENI_POZ": {
  "pos": [0, 0, 5000, 5000, 5000, 5000],
  "vel": [15000,15000,15000,15000,15000,15000],
  "cur": [400,400,400,400,400,400],
  "pos_tol": 300,
  "description": "aciklama"
}
```

## "Hataya düşmeme" nasıl sağlandı
| Durum | Davranış |
|---|---|
| İstemci hiç istek göndermiyor | `recv` zaman aşımı yakalanır, bağlantı açık kalır, döngü devam eder |
| Hiç istemci bağlanmıyor | `accept` zaman aşımlı; boşta bekler, dakikada bir durum yazar |
| İstemci aniden kopuyor (RST) | Yakalanır, sadece o istemci kapanır |
| Bilinmeyen/bozuk komut | `<KOMUT>NOK` döner, sunucu çalışmaya devam eder |
| Binary/çöp veri | Temizlenir, `NOK` döner (çöp cevaba yankılanmaz) |
| Robot bağlı değil / koptu | `NOK` döner; `AUTO_RECONNECT` açıksa yeniden bağlanmayı dener |
| Bir istemcide hata | Diğer istemciler ve sunucu etkilenmez (ayrı thread) |
| Ctrl+C | Robot ve socket temiz kapatılır |

Test edildi: boşta 6 sn bekleme, sert kopuş, 5 eşzamanlı istemci, bozuk JSON,
binary çöp, geçersiz poz — **0 çökme**.

## Ayarlar (settings.py, bölüm 6)
- `TCP_HOST` / `TCP_PORT` — dinleme adresi (varsayılan `0.0.0.0:9090`)
- `CLIENT_IDLE_TIMEOUT_S` (60) — istemci sessizken beklenecek süre; dolunca sadece devam eder
- `MAX_CLIENTS` (8) — eşzamanlı istemci sayısı
- `REPLY_OK` / `REPLY_NOK` — cevap ekleri (eski sistem `FAIL` bekliyorsa `REPLY_NOK="FAIL"` yap)
- `AUTO_CONNECT_ROBOT` — açılışta robota bağlan
- `AUTO_RECONNECT` — komut anında robot bağlı değilse yeniden bağlanmayı dene
- `MOVE_TIMEOUT_S` / `POS_TOLERANCE` — hareket bekleme ve tolerans

## Dosyalar
- `main.py` — sunucuyu başlatır
- `socket_server.py` — TCP sunucusu + komut işleme
- `poses.json` — poz tanımları
- `settings.py` — tüm ayarlar
- `robot_interface.py` — robot komut/sensör katmanı (v2 ile aynı)
- `robot_tools.py` — home / alarm / enable / kuvvet (kameradan ve socket'ten bağımsız)
- `list_nics.py` — EtherCAT ağ kartı listeleyici
- `client_test.py` — test istemcisi
