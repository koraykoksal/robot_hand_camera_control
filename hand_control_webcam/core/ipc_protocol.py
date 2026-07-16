# -*- coding: utf-8 -*-
"""
IPC Protokolü — Multiprocessing Command/Response mesaj yapıları
================================================================

EtherCAT worker process ile ana process arasındaki iletişim için
pickle-friendly dataclass'lar. multiprocessing.Queue ile gönderilir.

Tasarım prensipleri:
- Her komutun unique bir request_id'si var (eşleştirme için)
- Komutlar sync: ana process response bekler
- Status update'ler async: worker ne zaman isterse push eder
- Sadece built-in tipler + dataclass (pickle sorun çıkarmaz)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional, Tuple
import uuid
import time


# ---------------------------------------------------------------------------
# Command Types (ana process -> worker)
# ---------------------------------------------------------------------------
class CmdType(Enum):
    # Lifecycle
    SCAN_ADAPTERS = "scan_adapters"
    CONNECT = "connect"
    DISCONNECT = "disconnect"
    SHUTDOWN = "shutdown"

    # Motor control
    ENABLE_ALL = "enable_all"
    DISABLE_ALL = "disable_all"
    MOVE_TO_POSITION = "move_to_position"
    HOME_ALL = "home_all"
    STOP_ALL = "stop_all"
    CLEAR_ALARMS = "clear_alarms"

    # Queries
    GET_STATUS = "get_status"
    GET_CONNECTION_STATE = "get_connection_state"


class RespStatus(Enum):
    """HTTP-benzeri status code'lar."""
    OK = 200                    # İşlem başarılı
    TIMEOUT = 408               # Hareket tamamlanmadı
    CONFLICT = 409              # Motor alarm
    ERROR = 500                 # Genel hata
    NOT_CONNECTED = 503         # Bağlantı yok veya motor kilitli
    UNKNOWN_COMMAND = 400       # Worker anlamadı


# ---------------------------------------------------------------------------
# Command
# ---------------------------------------------------------------------------
@dataclass
class Command:
    """Ana process'ten worker'a gönderilen komut."""
    cmd_type: CmdType
    request_id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    payload: dict = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)


# ---------------------------------------------------------------------------
# Response
# ---------------------------------------------------------------------------
@dataclass
class Response:
    """Worker'dan ana process'e dönen cevap."""
    request_id: str
    status: RespStatus
    message: str = ""
    data: dict = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)


# ---------------------------------------------------------------------------
# StatusUpdate (async, worker -> ana)
# ---------------------------------------------------------------------------
class UpdateType(Enum):
    CONNECTION_OK = "connection_ok"
    CONNECTION_LOST = "connection_lost"
    CONNECTION_RESTORED = "connection_restored"
    ALARM_RAISED = "alarm_raised"
    ALARM_CLEARED = "alarm_cleared"
    MOTOR_LOCKED = "motor_locked"
    WORKER_ERROR = "worker_error"
    LOG = "log"


@dataclass
class StatusUpdate:
    """Worker'dan ana process'e asenkron olarak akan durum güncellemeleri."""
    update_type: UpdateType
    data: dict = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)


# ---------------------------------------------------------------------------
# Motor status snapshot (shared memory için)
# ---------------------------------------------------------------------------
@dataclass
class MotorSnapshot:
    """Tek bir motorun durumu."""
    motor_id: int = 0
    position: int = 0
    angle: float = 0.0
    current: int = 0
    alarm: int = 0
    enabled: bool = False
    reached: bool = False


@dataclass
class HandSnapshot:
    """Tüm el durumu - shared memory üzerinden okunabilir."""
    connected: bool = False
    enabled: bool = False
    adapter_name: str = ""
    dof: int = 6
    motors: List[MotorSnapshot] = field(default_factory=list)
    last_update_ts: float = 0.0
    connection_uptime_s: float = 0.0
    watchdog_count: int = 0           # son başlatmadan beri kaç kez watchdog
    reconnect_count: int = 0          # son başlatmadan beri kaç kez reconnect
