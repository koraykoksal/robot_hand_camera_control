# -*- coding: utf-8 -*-
"""
FastAPI REST Server
====================

Robotik El kontrol sistemi için HTTP endpoint'leri.

Çalıştırma:
    python run_api.py

Swagger UI:
    http://localhost:8000/docs

API bilgisi:
    http://localhost:8000/
"""

from __future__ import annotations

import os
import sys
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, HTTPException, Depends, Header
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

# sys.path setup
THIS_DIR = os.path.dirname(os.path.abspath(__file__))
PARENT_DIR = os.path.dirname(THIS_DIR)
for sub in ("core", "services", "cli", "config", "api"):
    p = os.path.join(PARENT_DIR, sub)
    if p not in sys.path:
        sys.path.insert(0, p)
if PARENT_DIR not in sys.path:
    sys.path.insert(0, PARENT_DIR)

from worker_proxy import EcatWorkerProxy
from ipc_protocol import UpdateType
from position_manager import PositionManager
from adapter_resolver import enrich_adapters, load_adapter_config, find_saved_adapter_index

from models import (
    ConnectRequest, MoveRequest, EnableRequest, ByeRequest,
    ClearAlarmsRequest, HomeRequest,
    StatusResponse, HealthResponse, AdapterInfo, AdaptersResponse,
    MotorStatus, SystemStatusResponse, PoseInfo, PositionsResponse,
)

import api_settings


# ============================================================================
# GLOBAL STATE (worker proxy ve position manager)
# ============================================================================

class AppState:
    """Server yaşam döngüsü boyunca paylaşılan durum."""
    proxy: Optional[EcatWorkerProxy] = None
    position_manager: Optional[PositionManager] = None
    adapter_config_path: Optional[str] = None


state = AppState()


# ============================================================================
# OTOMATİK BAĞLANTI (settings.py'deki adapter index'i ile)
# ============================================================================

def _auto_connect_from_settings():
    """
    settings.py'deki ECAT_ADAPTER_INDEX ile robota otomatik bağlanır + enable eder.

    Amaç: 'python run_api.py' sonrası manuel /connect + /enable gerektirmemek.
    Index ayarsız/geçersizse mevcut adapter listesini konsola yazar ki
    kullanıcı doğru index'i görüp settings.py'ye yazabilsin.
    """
    if not (state.proxy and state.proxy.is_alive()):
        return

    # 1) settings.py'den index oku
    try:
        import settings  # config/ zaten sys.path'te
        adapter_index = int(getattr(settings, "ECAT_ADAPTER_INDEX", -1))
    except Exception as e:
        print(f"ℹ️ settings.ECAT_ADAPTER_INDEX okunamadı ({e}); manuel /connect kullanın.")
        return

    # 2) Adapter listesini al
    try:
        raw = state.proxy.scan_adapters()
    except Exception as e:
        print(f"⚠️ Adapter taraması başarısız: {e}")
        return

    # 3) Index geçerliyse bağlan + enable
    if 0 <= adapter_index < len(raw):
        try:
            disp = enrich_adapters(raw)[adapter_index].display_name
        except Exception:
            disp = raw[adapter_index]
        print(f"🔌 Otomatik bağlanılıyor: [{adapter_index}] {disp}")
        ok, msg = state.proxy.connect(adapter_index)
        if ok:
            ok2, msg2 = state.proxy.enable_all()
            if ok2:
                print("✅ Otomatik bağlandı + motorlar enable")
            else:
                print(f"⚠️ Bağlandı ama enable başarısız: {msg2}")
        else:
            print(f"⚠️ Otomatik bağlantı başarısız: {msg}")
    else:
        print(f"⚠️ settings.ECAT_ADAPTER_INDEX={adapter_index} geçersiz. Mevcut adapter'lar:")
        for a in enrich_adapters(raw):
            print(f"    [{a.index}] {a.display_name}")
        print("    Doğru index'i config/settings.py -> ECAT_ADAPTER_INDEX'e yazıp yeniden başlatın.")


# ============================================================================
# LIFESPAN (startup / shutdown)
# ============================================================================

@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    FastAPI yaşam döngüsü.
    Startup: worker başlat, pozisyonları yükle.
    Shutdown: worker'ı düzgün kapat.
    """
    # Startup
    print("🚀 API Server başlatılıyor...")

    # Pozisyon manager
    positions_path = os.path.join(PARENT_DIR, "config", "positions.json")
    state.position_manager = PositionManager(positions_path, autosave=True)
    print(f"📂 Pozisyonlar yüklendi: {state.position_manager.count()} adet")

    # Adapter config
    state.adapter_config_path = os.path.join(PARENT_DIR, "config", "ecat_config.json")

    # Worker proxy
    if api_settings.AUTO_START_WORKER:
        print("⚙️ Worker process başlatılıyor...")
        state.proxy = EcatWorkerProxy(
            dof=6,
            default_timeout_s=api_settings.DEFAULT_COMMAND_TIMEOUT_S,
        )
        if not state.proxy.start():
            print("❌ Worker başlatılamadı!")
        else:
            print("✅ Worker process hazır")
            # settings.py'deki index ile otomatik bağlan + enable
            _auto_connect_from_settings()

    print(f"\n{'=' * 60}")
    print(f"  ✅ API hazır: http://{api_settings.API_HOST}:{api_settings.API_PORT}")
    print(f"  📚 Swagger UI: http://localhost:{api_settings.API_PORT}/docs")
    print(f"{'=' * 60}\n")

    yield  # <-- server çalışıyor

    # Shutdown
    print("\n🔻 API Server kapatılıyor...")
    if state.proxy:
        try:
            # Önce bağlantıyı kes
            if state.proxy.is_alive():
                state.proxy.disconnect()
            # Sonra worker'ı durdur
            state.proxy.stop(timeout_s=3.0)
        except Exception as e:
            print(f"⚠️ Shutdown hata: {e}")
    print("👋 Kapandı\n")


# ============================================================================
# FASTAPI APP
# ============================================================================

app = FastAPI(
    title=api_settings.API_TITLE,
    version=api_settings.API_VERSION,
    description=api_settings.API_DESCRIPTION,
    lifespan=lifespan,
)

# CORS middleware (web tarayıcılardan erişim için)
app.add_middleware(
    CORSMiddleware,
    allow_origins=api_settings.CORS_ALLOW_ORIGINS,
    allow_methods=api_settings.CORS_ALLOW_METHODS,
    allow_headers=api_settings.CORS_ALLOW_HEADERS,
)


# ============================================================================
# GÜVENLİK (opsiyonel API key)
# ============================================================================

async def verify_api_key(authorization: Optional[str] = Header(None)):
    """API key kontrolü (API_KEY_REQUIRED=True ise)."""
    if not api_settings.API_KEY_REQUIRED:
        return True
    if not authorization:
        raise HTTPException(status_code=401, detail="Authorization header required")
    expected = f"Bearer {api_settings.API_KEY}"
    if authorization != expected:
        raise HTTPException(status_code=401, detail="Invalid API key")
    return True


# ============================================================================
# YARDIMCI: Worker proxy'i alır, hazır değilse 503 fırlatır
# ============================================================================

def get_proxy() -> EcatWorkerProxy:
    """Worker proxy'i al. Hazır değilse HTTPException."""
    if state.proxy is None or not state.proxy.is_alive():
        raise HTTPException(
            status_code=503,
            detail="Worker process not running",
        )
    return state.proxy


# ============================================================================
# ENDPOINT'LER
# ============================================================================

@app.get("/", tags=["Info"])
async def root():
    """API hakkında kısa bilgi."""
    return {
        "api": api_settings.API_TITLE,
        "version": api_settings.API_VERSION,
        "docs": "/docs",
        "health": "/health",
    }


@app.get("/health", response_model=HealthResponse, tags=["Info"])
async def health():
    """Detaylı sağlık kontrolü."""
    worker_alive = state.proxy is not None and state.proxy.is_alive()

    connected = False
    enabled = False
    adapter = None

    if worker_alive:
        try:
            conn_state = state.proxy.get_connection_state()
            if conn_state:
                connected = conn_state.get("connected", False)
                enabled = conn_state.get("enabled", False)
                adapter = conn_state.get("adapter", None)
        except Exception:
            pass

    return HealthResponse(
        api_running=True,
        worker_alive=worker_alive,
        connected=connected,
        enabled=enabled,
        adapter=adapter,
        version=api_settings.API_VERSION,
    )


# ------------ BAĞLANTI YÖNETİMİ ------------

@app.get("/adapters", response_model=AdaptersResponse, tags=["Connection"],
         dependencies=[Depends(verify_api_key)])
async def list_adapters():
    """Kullanılabilir network adapter'lerini listele."""
    proxy = get_proxy()
    adapters_raw = proxy.scan_adapters()
    adapters = [
        AdapterInfo(index=i, name=name)
        for i, name in enumerate(adapters_raw)
    ]
    return AdaptersResponse(adapters=adapters, count=len(adapters))


@app.post("/connect", response_model=StatusResponse, tags=["Connection"],
          dependencies=[Depends(verify_api_key)])
async def connect(req: ConnectRequest):
    """Belirtilen adapter üzerinden robot ele bağlan."""
    proxy = get_proxy()
    ok, msg = proxy.connect(req.adapter_index)
    if ok:
        return StatusResponse(status=200, message=msg, data={"adapter_index": req.adapter_index})
    raise HTTPException(status_code=500, detail=msg)


@app.post("/disconnect", response_model=StatusResponse, tags=["Connection"],
          dependencies=[Depends(verify_api_key)])
async def disconnect():
    """Robot el bağlantısını kes."""
    proxy = get_proxy()
    ok, msg = proxy.disconnect()
    return StatusResponse(status=200 if ok else 500, message=msg)


# ------------ MOTOR KONTROL ------------

@app.post("/enable", response_model=StatusResponse, tags=["Motors"],
          dependencies=[Depends(verify_api_key)])
async def enable_motors(req: EnableRequest = EnableRequest()):
    """Tüm motorları enable et."""
    proxy = get_proxy()
    ok, msg = proxy.enable_all(velocity=req.velocity, max_current=req.max_current)
    return StatusResponse(
        status=200 if ok else 500,
        message=msg,
    )


@app.post("/disable", response_model=StatusResponse, tags=["Motors"],
          dependencies=[Depends(verify_api_key)])
async def disable_motors():
    """Tüm motorları disable et."""
    proxy = get_proxy()
    ok, msg = proxy.disable_all()
    return StatusResponse(status=200 if ok else 500, message=msg)


@app.post("/home", response_model=StatusResponse, tags=["Motors"],
          dependencies=[Depends(verify_api_key)])
async def home(req: HomeRequest = HomeRequest()):
    """Home sekansı - tüm motorlar sıfıra döner."""
    proxy = get_proxy()
    ok, msg = proxy.home_all(force_current=req.force_current)
    return StatusResponse(status=200 if ok else 500, message=msg)


@app.post("/stop", response_model=StatusResponse, tags=["Motors"],
          dependencies=[Depends(verify_api_key)])
async def stop_motors():
    """Tüm motorları anında durdur."""
    proxy = get_proxy()
    ok, msg = proxy.stop_all()
    return StatusResponse(status=200 if ok else 500, message=msg)


@app.post("/alarms/clear", response_model=StatusResponse, tags=["Motors"],
          dependencies=[Depends(verify_api_key)])
async def clear_alarms(req: ClearAlarmsRequest = ClearAlarmsRequest()):
    """Motor alarmlarını temizle. motor_id=0 ise tümü, >0 ise belirli motor."""
    proxy = get_proxy()
    ok, msg = proxy.clear_alarms(motor_id=req.motor_id)
    return StatusResponse(status=200 if ok else 500, message=msg)


# ------------ DURUM İZLEME ------------

@app.get("/status", response_model=SystemStatusResponse, tags=["Status"],
         dependencies=[Depends(verify_api_key)])
async def get_status():
    """Anlık motor durumlarını al (pozisyon, akım, alarm vb.)."""
    proxy = get_proxy()
    data = proxy.get_status()
    if not data:
        raise HTTPException(status_code=503, detail="Status alınamadı")

    motors = [
        MotorStatus(**m) for m in data.get("motors", [])
    ]
    return SystemStatusResponse(
        connected=data.get("connected", False),
        enabled=data.get("enabled", False),
        motors=motors,
    )


# ------------ POZİSYON YÖNETİMİ ------------

@app.get("/positions", response_model=PositionsResponse, tags=["Positions"],
         dependencies=[Depends(verify_api_key)])
async def list_positions():
    """Kayıtlı pozisyonları listele (BARDAK_AL, BARDAK_BIRAK vs.)."""
    if not state.position_manager:
        raise HTTPException(status_code=500, detail="Position manager not initialized")

    poses = []
    for name in state.position_manager.list_names():
        pose = state.position_manager.get(name)
        if pose:
            poses.append(PoseInfo(
                name=pose.name,
                pos=list(pose.pos),
                vel=list(pose.vel),
                cur=list(pose.cur),
                pos_tol=pose.pos_tol,
                description=pose.description,
            ))
    return PositionsResponse(positions=poses, count=len(poses))


@app.post("/move", response_model=StatusResponse, tags=["Movement"],
          dependencies=[Depends(verify_api_key)])
async def move_to_position(req: MoveRequest):
    """Manuel pozisyon hareketi - tüm parametreler body'de."""
    proxy = get_proxy()
    status_code, msg = proxy.move_to_position(
        position=req.position,
        velocity=req.velocity,
        max_current=req.max_current,
        wait=req.wait,
        timeout_s=req.timeout_s,
        pos_tolerance=req.pos_tolerance,
    )
    response = StatusResponse(
        status=status_code,
        message=msg,
        data={
            "position": req.position,
            "wait": req.wait,
        },
    )
    if status_code >= 400:
        return JSONResponse(status_code=status_code, content=response.model_dump())
    return response


@app.post("/move/{name}", response_model=StatusResponse, tags=["Movement"],
          dependencies=[Depends(verify_api_key)])
async def move_to_named_position(name: str):
    """
    Kayıtlı pozisyona git + otomatik GRIP kontrolü.

    **Smart 200 mantığı**: Hareket sonrası akım ve pozisyon analiz edilir.

    **Olası grip_status değerleri:**
    - `GRIP_OK`: Nesne kavrandı (motor zorlanıyor, sapma yüksek)
    - `NO_OBJECT`: Havada hareket (nesne yok veya algılanmadı)
    - `OVERGRIP`: Aşırı sıkma (tehlikeli seviyede akım)
    - `UNCERTAIN`: Belirsiz (ara durum)

    **HTTP durumları:**
    - `200 OK`: Başarılı hareket (grip_status içeriği önemli)
    - `409 Conflict`: Aşırı sıkma tespit edildi
    - `500 Error`: Ciddi donanım hatası
    - `503 Service Unavailable`: Bağlantı yok

    Örnek:
        POST /move/BARDAK_AL
        POST /move/BARDAK_BIRAK
        POST /move/ZERO
    """
    if not state.position_manager:
        raise HTTPException(status_code=500, detail="Position manager not initialized")

    pose = state.position_manager.get(name)
    if not pose:
        raise HTTPException(
            status_code=404,
            detail=f"Pozisyon bulunamadı: {name}",
        )

    proxy = get_proxy()

    # Pose'dan değerleri çıkar (list ise ilk elemanı al)
    vel = pose.vel[0] if isinstance(pose.vel, list) else pose.vel
    cur = pose.cur[0] if isinstance(pose.cur, list) else pose.cur

    # 1. Hareketi yap
    move_status, move_msg = proxy.move_to_position(
        position=list(pose.pos),
        velocity=vel,
        max_current=cur,
        wait=True,
        timeout_s=5.0,
        pos_tolerance=pose.pos_tol,
    )

    # 2. GRIP analizini yap (hareket sonucu ne olursa olsun)
    grip_status, grip_detail = proxy.check_grip(target_position=list(pose.pos))

    # 3. Akıllı sonuç yorumlama
    # Temel: Hareket sonucu + GRIP durumu birlikte değerlendir
    final_status, final_message = _interpret_move_result(
        move_status=move_status,
        move_msg=move_msg,
        grip_status=grip_status,
        pose_name=name,
    )

    response_data = {
        "position_name": name,
        "position": list(pose.pos),
        "grip_status": grip_status,
        "move_result": {
            "raw_status": move_status,
            "raw_message": move_msg,
        },
        "details": grip_detail,
    }

    response = StatusResponse(
        status=final_status,
        message=final_message,
        data=response_data,
    )

    # 4xx ve 5xx durumlarında JSONResponse ile HTTP status code'u değiştir
    if final_status >= 400:
        return JSONResponse(status_code=final_status, content=response.model_dump())
    return response


def _interpret_move_result(
    move_status: int,
    move_msg: str,
    grip_status: str,
    pose_name: str,
) -> tuple:
    """
    Hareket sonucu + GRIP durumuna göre akıllı cevap üretir.

    Returns:
        (status_code, message)
    """
    # 1. OVERGRIP her zaman tehlikeli - 409 döndür
    if grip_status == "OVERGRIP":
        return (409, f"Overgrip detected - potential damage risk")

    # 2. Bağlantı yoksa veya ciddi donanım hatası
    if move_status == 503:
        return (503, "Connection lost")
    if move_status == 500:
        return (500, f"Hardware error: {move_msg}")

    # 3. Normal başarı durumu
    if move_status == 200:
        if grip_status == "GRIP_OK":
            return (200, f"Position reached - object grasped ({pose_name})")
        elif grip_status == "NO_OBJECT":
            return (200, f"Position reached - no object ({pose_name})")
        else:
            return (200, f"Position reached ({pose_name})")

    # 4. 408 TIMEOUT ama GRIP_OK - aslında başarılı kavrama!
    if move_status == 408:
        if grip_status == "GRIP_OK":
            return (200, f"Object grasped successfully ({pose_name})")
        elif grip_status == "UNCERTAIN":
            return (200, f"Partial reach - position approximated ({pose_name})")
        else:
            # Gerçek timeout - motor hedefine ulaşamadı, kavrama da yok
            return (408, f"Could not reach position ({pose_name})")

    # 5. Alarm durumu
    if move_status == 409:
        return (409, f"Alarm state: {move_msg}")

    # 6. Bilinmeyen durum
    return (move_status, move_msg)


# ------------ ÖZEL HAREKETLER ------------

@app.get("/grip/{pose_name}", response_model=StatusResponse, tags=["Grip"],
         dependencies=[Depends(verify_api_key)])
async def check_grip_after_pose(pose_name: str):
    """
    Bir pozisyona gittikten sonra GRIP kontrolü yapar.

    Akıllı kavrama tespiti:
      - GRIP_OK: Motor nesneyle karşılaştı (akım yüksek + sapma yüksek)
      - NO_OBJECT: Havada kavrama (motor rahat ulaştı)
      - OVERGRIP: Aşırı sıkma (akım limite yakın)
      - UNCERTAIN: Net sonuç alınamadı

    Örnek:
        GET /grip/BARDAK_AL → bardak tutuluyor mu kontrol et

    Not: Önce /move/BARDAK_AL çağırıp pozisyona gidilmeli.
    """
    if not state.position_manager:
        raise HTTPException(status_code=500, detail="Position manager not initialized")

    pose = state.position_manager.get(pose_name)
    if not pose:
        raise HTTPException(status_code=404, detail=f"Pozisyon bulunamadı: {pose_name}")

    proxy = get_proxy()
    grip_status, detail = proxy.check_grip(target_position=list(pose.pos))

    return StatusResponse(
        status=200,
        message=f"GRIP check: {grip_status}",
        data={
            "pose_name": pose_name,
            "grip_status": grip_status,
            "details": detail,
        },
    )


@app.post("/bye", response_model=StatusResponse, tags=["Movement"],
          dependencies=[Depends(verify_api_key)])
async def bye_bye(req: ByeRequest = ByeRequest()):
    """
    Bye bye hareketi - motor 3, 4, 5, 6 (işaret, orta, yüzük, serçe)
    yukarı-aşağı sallanır. Başparmak sabit kalır.

    Varsayılan: 3 kere sallama.

    Örnek:
        POST /bye
        POST /bye    body: {"repeat": 5}
    """
    proxy = get_proxy()
    status_code, msg, completed = proxy.bye_bye(
        repeat=req.repeat,
        velocity=req.velocity,
        max_current=req.max_current,
    )
    response = StatusResponse(
        status=status_code,
        message=msg,
        data={
            "repeat": req.repeat,
            "completed_steps": completed,
            "total_steps": req.repeat * 2,
        },
    )
    if status_code >= 400:
        return JSONResponse(status_code=status_code, content=response.model_dump())
    return response
