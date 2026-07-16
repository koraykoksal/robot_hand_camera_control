# -*- coding: utf-8 -*-
"""
REST API için Pydantic Modelleri
=================================

FastAPI request body ve response'ları için tip tanımlamaları.
Otomatik validasyon + Swagger dokümanı için kullanılır.
"""

from __future__ import annotations

from typing import List, Optional
from pydantic import BaseModel, Field


# ============================================================================
# REQUEST MODELS (istek body'leri)
# ============================================================================

class ConnectRequest(BaseModel):
    """Bağlantı için adapter index'i."""
    adapter_index: int = Field(
        ...,
        description="Network adapter index'i (GET /adapters'tan alın)",
        ge=0,
        examples=[1],
    )


class MoveRequest(BaseModel):
    """Manuel pozisyon hareketi için tüm parametreler."""
    position: List[int] = Field(
        ...,
        description="6 motor için hedef pozisyonlar (0-10000 arası)",
        min_length=6,
        max_length=6,
        examples=[[4500, 3500, 3500, 4000, 4000, 4000]],
    )
    velocity: Optional[int] = Field(
        default=12000,
        description="Motor hızı (1000-20000)",
        ge=1000,
        le=20000,
    )
    max_current: Optional[int] = Field(
        default=700,
        description="Maksimum akım limiti (100-1500)",
        ge=100,
        le=1500,
    )
    wait: Optional[bool] = Field(
        default=True,
        description="True: hareket bitince cevap döner (blocking). "
                    "False: komut gönderilir gönderilmez 200 döner.",
    )
    timeout_s: Optional[float] = Field(
        default=5.0,
        description="Hareket tamamlanma timeout'u (saniye)",
        gt=0.0,
    )
    pos_tolerance: Optional[int] = Field(
        default=500,
        description="Pozisyon toleransı (encoder sayımı)",
        ge=0,
    )


class EnableRequest(BaseModel):
    """Enable komutu için parametreler."""
    velocity: Optional[int] = Field(default=12000, ge=1000, le=20000)
    max_current: Optional[int] = Field(default=700, ge=100, le=1500)


class ByeRequest(BaseModel):
    """Bye bye hareketi için parametreler."""
    repeat: Optional[int] = Field(
        default=3,
        description="Kaç kere bye bye yapılacak (tam sallama sayısı)",
        ge=1,
        le=10,
    )
    velocity: Optional[int] = Field(
        default=12000,
        description="Parmak hareketi hızı",
        ge=1000,
        le=20000,
    )
    max_current: Optional[int] = Field(
        default=500,
        description="Parmak motor akım limiti",
        ge=100,
        le=1500,
    )


class ClearAlarmsRequest(BaseModel):
    """Alarm temizleme komutu."""
    motor_id: int = Field(
        default=0,
        description="0 = tüm motorlar, 1-6 = spesifik motor",
        ge=0,
        le=6,
    )


class HomeRequest(BaseModel):
    """Home sekansı."""
    force_current: Optional[int] = Field(
        default=400,
        description="Home sırasında kullanılacak akım limiti",
        ge=100,
        le=1500,
    )


# ============================================================================
# RESPONSE MODELS (yanıt formatları)
# ============================================================================

class StatusResponse(BaseModel):
    """Standart API yanıtı."""
    status: int = Field(..., description="HTTP-benzeri status code (200/408/409/500/503)")
    message: str = Field(..., description="Okunabilir mesaj")
    data: Optional[dict] = Field(default=None, description="Ek veri (opsiyonel)")


class HealthResponse(BaseModel):
    """Sağlık kontrolü yanıtı."""
    api_running: bool
    worker_alive: bool
    connected: bool
    enabled: bool
    adapter: Optional[str] = None
    version: str


class AdapterInfo(BaseModel):
    """Bir network adapter bilgisi."""
    index: int
    name: str


class AdaptersResponse(BaseModel):
    """Adapter listesi yanıtı."""
    adapters: List[AdapterInfo]
    count: int


class MotorStatus(BaseModel):
    """Tek bir motorun anlık durumu."""
    motor_id: int
    position: int
    angle: float
    current: int
    alarm: int
    enabled: bool
    reached: bool


class SystemStatusResponse(BaseModel):
    """Tüm motor durumları + sistem bilgisi."""
    connected: bool
    enabled: bool
    motors: List[MotorStatus]


class PoseInfo(BaseModel):
    """Kayıtlı pozisyon bilgisi."""
    name: str
    pos: List[int]
    vel: List[int]
    cur: List[int]
    pos_tol: int
    description: Optional[str] = None


class PositionsResponse(BaseModel):
    """Kayıtlı pozisyonlar listesi."""
    positions: List[PoseInfo]
    count: int
