# -*- coding: utf-8 -*-
"""
REST API Ayarları
==================

API server'ın nasıl çalışacağını belirleyen ayarlar.
"""

# ============================================================================
# SERVER AYARLARI
# ============================================================================

# API dinleme adresi
# "127.0.0.1" = sadece aynı bilgisayardan erişilebilir (güvenli, geliştirme)
# "0.0.0.0"   = yerel ağdaki her cihazdan erişilebilir
API_HOST: str = "0.0.0.0"

# API dinleme port numarası
# Kullanılmayan bir port seçin. 8000 FastAPI için yaygın.
API_PORT: int = 8080

# Log seviyesi
# "debug"   = her şeyi göster
# "info"    = normal çalışma logları (önerilen)
# "warning" = sadece uyarı ve hatalar
API_LOG_LEVEL: str = "info"

# Uvicorn reload mode (geliştirme için, kodda değişiklik olunca otomatik yeniden başlatır)
# Production'da FALSE olmalı.
API_RELOAD: bool = False


# ============================================================================
# CORS (CROSS-ORIGIN RESOURCE SHARING)
# ============================================================================
# Web tarayıcısından erişim için gerekli.
# Başka bir origin'deki (örneğin bir web UI) istekler kabul edilsin mi?

# İzin verilen origin'ler
# ["*"] = tümü (güvensiz, ama geliştirme için pratik)
# ["http://localhost:3000"] = sadece belirli bir web app
CORS_ALLOW_ORIGINS: list = ["*"]

# İzin verilen HTTP metodları
CORS_ALLOW_METHODS: list = ["GET", "POST", "OPTIONS"]

# İzin verilen header'lar
CORS_ALLOW_HEADERS: list = ["*"]


# ============================================================================
# GÜVENLİK (İsteğe bağlı)
# ============================================================================

# API key zorunlu mu?
# True ise her isteğin Authorization header'ı olmalı.
# Değer: "Bearer <API_KEY>" veya header adını özelleştirin.
API_KEY_REQUIRED: bool = False

# API key (API_KEY_REQUIRED=True ise geçerli)
# Production'da güvenli bir değer kullanın, env variable'dan okuyun.
API_KEY: str = "degistirin-bu-key-production-degil"


# ============================================================================
# WORKER LIFECYCLE
# ============================================================================

# API server başlarken worker otomatik başlasın mı?
# True: uygulama başlar başlamaz worker process hazır olur
# False: manual olarak /connect çağrılana kadar worker uyur
AUTO_START_WORKER: bool = True

# Worker ile konuşurken komut timeout (saniye)
# Bu süreden uzun süren komutlar 408 döner.
DEFAULT_COMMAND_TIMEOUT_S: float = 15.0


# ============================================================================
# API BİLGİLERİ (Swagger docs için)
# ============================================================================

API_TITLE: str = "Robotik El REST API"
API_VERSION: str = "1.0.0"
API_DESCRIPTION: str = """
Robotik El EtherCAT Kontrol REST API'si.

Özellikler:
- **Multiprocessing mimari** — EtherCAT worker ayrı process'te, jitter bağışıklığı
- **Otomatik reconnect** — kablo koparsa sonsuz yeniden bağlanma
- **HTTP status code'ları** — 200, 408, 409, 503 gibi standart yanıtlar
- **Swagger UI** — /docs adresinde interaktif API dokümanı

Hızlı başlangıç:
1. POST /connect → bağlan
2. POST /enable → motorları hazırla
3. POST /move/BARDAK_AL → pozisyona git

Detaylı bilgi: /docs
"""
