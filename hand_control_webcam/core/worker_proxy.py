# -*- coding: utf-8 -*-
"""
EcatWorkerProxy — Ana process tarafında worker'ı yönet
=======================================================

Bu sınıf ana process'te çalışır. Worker process'i başlatır, komutları
ona gönderir, cevapları bekler. CLI ve REST API bu proxy'yi kullanır.

Temel sorumluluklar:
    - Worker process'i başlat/durdur
    - Command/Response eşleştirmesi (request_id bazlı)
    - Status update dinleyici (callback pattern)
    - Timeout yönetimi (worker donarsa ana process kilitlenmemeli)
    - Zarif kapanış
"""

from __future__ import annotations

import os
import sys
import time
import threading
import multiprocessing as mp
from typing import Callable, Dict, List, Optional, Tuple

# Bu dosya core/ içinde - ipc_protocol ve ecat_worker aynı yerde
THIS_DIR = os.path.dirname(os.path.abspath(__file__))
if THIS_DIR not in sys.path:
    sys.path.insert(0, THIS_DIR)

from ipc_protocol import (
    Command, Response, StatusUpdate,
    CmdType, RespStatus, UpdateType,
)


class EcatWorkerProxy:
    """
    Ana process'ten worker process'ine erişim arayüzü.

    Kullanım:
        proxy = EcatWorkerProxy(dof=6)
        proxy.start()  # Worker process'i başlatır

        # Komut gönder
        resp = proxy.send_command(CmdType.SCAN_ADAPTERS)
        if resp.status == RespStatus.OK:
            adapters = resp.data["adapters"]

        # Status update dinle
        proxy.on_status_update = lambda upd: print(upd)

        # Kapanış
        proxy.stop()
    """

    def __init__(
        self,
        dof: int = 6,
        default_timeout_s: float = 10.0,
    ):
        self.dof = dof
        self.default_timeout_s = default_timeout_s

        # IPC queues
        # Not: Windows'ta mp.Queue pickle-based, Linux'ta pipe-based
        self._cmd_queue: Optional[mp.Queue] = None
        self._resp_queue: Optional[mp.Queue] = None
        self._status_queue: Optional[mp.Queue] = None

        # Worker process
        self._worker_process: Optional[mp.Process] = None
        self._running: bool = False

        # Response eşleştirme
        self._pending_responses: Dict[str, Response] = {}
        self._response_event = threading.Event()
        self._response_lock = threading.Lock()

        # Dispatch thread - response queue'yu dinler
        self._dispatch_thread: Optional[threading.Thread] = None

        # Status update listener
        self._status_thread: Optional[threading.Thread] = None
        self.on_status_update: Optional[Callable[[StatusUpdate], None]] = None
        self.on_log: Optional[Callable[[str], None]] = None

    # ==========================================================================
    # Lifecycle
    # ==========================================================================
    def start(self) -> bool:
        """Worker process'ini başlat."""
        if self._running:
            return True

        # Queue'ları oluştur
        self._cmd_queue = mp.Queue(maxsize=100)
        self._resp_queue = mp.Queue(maxsize=100)
        self._status_queue = mp.Queue(maxsize=1000)

        # Worker process'i başlat
        # Lazy import - sadece worker process'i başlatmak için gerekli
        from ecat_worker import worker_main

        self._worker_process = mp.Process(
            target=worker_main,
            args=(self._cmd_queue, self._resp_queue, self._status_queue, self.dof),
            name="ecat-worker",
            daemon=False,  # Daemon değil - düzgün kapanmasını istiyoruz
        )
        self._worker_process.start()
        self._running = True

        # Dispatch ve status dinleme thread'leri
        self._dispatch_thread = threading.Thread(
            target=self._dispatch_loop,
            name="proxy-dispatch",
            daemon=True,
        )
        self._dispatch_thread.start()

        self._status_thread = threading.Thread(
            target=self._status_loop,
            name="proxy-status",
            daemon=True,
        )
        self._status_thread.start()

        # Worker'ın başladığını doğrula (kısa bekleme)
        time.sleep(0.2)
        if not self._worker_process.is_alive():
            self._running = False
            return False

        return True

    def stop(self, timeout_s: float = 5.0) -> None:
        """Worker process'ini düzgün kapat."""
        if not self._running:
            return

        try:
            # Önce shutdown komutu gönder
            self.send_command(CmdType.SHUTDOWN, timeout_s=2.0)
        except Exception:
            pass

        self._running = False

        # Worker process'in bitmesini bekle
        if self._worker_process and self._worker_process.is_alive():
            self._worker_process.join(timeout=timeout_s)

            # Hâlâ yaşıyorsa terminate
            if self._worker_process.is_alive():
                self._worker_process.terminate()
                self._worker_process.join(timeout=1.0)

                # Zombi ise kill (Windows'ta böyle bir şey pek yok ama)
                if self._worker_process.is_alive():
                    try:
                        self._worker_process.kill()
                    except Exception:
                        pass

        self._worker_process = None

    def is_alive(self) -> bool:
        """Worker process hâlâ yaşıyor mu?"""
        return (
            self._running
            and self._worker_process is not None
            and self._worker_process.is_alive()
        )

    # ==========================================================================
    # Command sending
    # ==========================================================================
    def send_command(
        self,
        cmd_type: CmdType,
        payload: Optional[dict] = None,
        timeout_s: Optional[float] = None,
    ) -> Response:
        """
        Worker'a komut gönder ve cevap bekle (sync).

        Returns:
            Response - eğer timeout olursa status=ERROR olur
        """
        if not self.is_alive():
            return Response(
                request_id="",
                status=RespStatus.ERROR,
                message="Worker not running",
            )

        if timeout_s is None:
            timeout_s = self.default_timeout_s

        cmd = Command(cmd_type=cmd_type, payload=payload or {})

        # Komut gönder
        try:
            self._cmd_queue.put(cmd, timeout=2.0)
        except Exception as e:
            return Response(
                request_id=cmd.request_id,
                status=RespStatus.ERROR,
                message=f"Failed to send command: {e}",
            )

        # Cevap bekle
        return self._wait_response(cmd.request_id, timeout_s)

    def _wait_response(self, request_id: str, timeout_s: float) -> Response:
        """request_id için gelen cevabı bekler."""
        deadline = time.time() + timeout_s

        while time.time() < deadline:
            with self._response_lock:
                if request_id in self._pending_responses:
                    return self._pending_responses.pop(request_id)

            # Yeni cevap geldi mi?
            remaining = max(0.01, deadline - time.time())
            self._response_event.wait(timeout=min(0.1, remaining))
            self._response_event.clear()

        # Timeout
        return Response(
            request_id=request_id,
            status=RespStatus.TIMEOUT,
            message=f"No response in {timeout_s}s",
        )

    def _dispatch_loop(self) -> None:
        """Response queue'dan gelen cevapları pending dict'e yaz."""
        while self._running:
            try:
                resp: Response = self._resp_queue.get(timeout=0.5)
                with self._response_lock:
                    self._pending_responses[resp.request_id] = resp
                self._response_event.set()
            except Exception:
                # Queue timeout - normal, devam et
                continue

    def _status_loop(self) -> None:
        """Status queue'dan gelen update'leri dinle."""
        while self._running:
            try:
                update: StatusUpdate = self._status_queue.get(timeout=0.5)

                # LOG update'leri özel handle
                if update.update_type == UpdateType.LOG:
                    if self.on_log:
                        try:
                            self.on_log(update.data.get("message", ""))
                        except Exception:
                            pass
                    else:
                        # Default: print
                        print(update.data.get("message", ""))

                # Genel status update callback
                if self.on_status_update:
                    try:
                        self.on_status_update(update)
                    except Exception:
                        pass
            except Exception:
                continue

    # ==========================================================================
    # High-level API (convenience wrappers)
    # ==========================================================================
    def scan_adapters(self) -> List[str]:
        """Adapter isimlerini dön."""
        resp = self.send_command(CmdType.SCAN_ADAPTERS)
        if resp.status == RespStatus.OK:
            return resp.data.get("adapters", [])
        return []

    def connect(self, adapter_index: int) -> Tuple[bool, str]:
        """Bağlan. (ok, message) dön."""
        resp = self.send_command(
            CmdType.CONNECT,
            payload={"adapter_index": adapter_index},
            timeout_s=15.0,  # Connect uzun sürebilir
        )
        return (resp.status == RespStatus.OK, resp.message)

    def disconnect(self) -> Tuple[bool, str]:
        """Bağlantıyı kes."""
        resp = self.send_command(CmdType.DISCONNECT, timeout_s=5.0)
        return (resp.status == RespStatus.OK, resp.message)

    def enable_all(self, velocity: int = 12000, max_current: int = 700) -> Tuple[bool, str]:
        resp = self.send_command(
            CmdType.ENABLE_ALL,
            payload={"velocity": velocity, "max_current": max_current},
            timeout_s=5.0,
        )
        return (resp.status == RespStatus.OK, resp.message)

    def disable_all(self) -> Tuple[bool, str]:
        resp = self.send_command(CmdType.DISABLE_ALL, timeout_s=3.0)
        return (resp.status == RespStatus.OK, resp.message)

    def move_to_position(
        self,
        position: List[int],
        *,
        velocity: int = 12000,
        max_current: int = 700,
        wait: bool = True,
        timeout_s: float = 5.0,
        pos_tolerance: int = 500,
    ) -> Tuple[int, str]:
        """
        Pozisyona hareket et.

        Returns:
            (status_code, message)
            status_code: 200=OK, 408=timeout, 409=alarm, 503=locked, 500=error
        """
        resp = self.send_command(
            CmdType.MOVE_TO_POSITION,
            payload={
                "position": position,
                "velocity": velocity,
                "max_current": max_current,
                "wait": wait,
                "timeout_s": timeout_s,
                "pos_tolerance": pos_tolerance,
            },
            timeout_s=timeout_s + 3.0,  # wait için biraz ekstra
        )
        return (resp.status.value, resp.message)

    def home_all(self, force_current: int = 400) -> Tuple[bool, str]:
        resp = self.send_command(
            CmdType.HOME_ALL,
            payload={"force_current": force_current},
            timeout_s=3.0,
        )
        return (resp.status == RespStatus.OK, resp.message)

    def stop_all(self) -> Tuple[bool, str]:
        resp = self.send_command(CmdType.STOP_ALL, timeout_s=2.0)
        return (resp.status == RespStatus.OK, resp.message)

    def clear_alarms(self, motor_id: int = 0) -> Tuple[bool, str]:
        resp = self.send_command(
            CmdType.CLEAR_ALARMS,
            payload={"motor_id": motor_id},
            timeout_s=2.0,
        )
        return (resp.status == RespStatus.OK, resp.message)

    # ------------------------------------------------------------------
    # SEKANS HAREKETLERİ (bye bye gibi tekrarlı hareketler)
    # ------------------------------------------------------------------

    def execute_sequence(
        self,
        steps: list,
        repeat: int = 1,
        velocity: int = 12000,
        max_current: int = 500,
        pos_tolerance: int = 500,
        step_timeout_s: float = 3.0,
        settle_between_s: float = 0.1,
        retry_on_fail: int = 1,
    ) -> Tuple[int, str, int]:
        """
        Bir pozisyon dizisini (steps) belirlenen sayıda tekrar et.

        Args:
            steps: Pozisyon listesi, her biri 6 int'lik liste.
                   Örn: [[0,0,0,0,0,0], [0,0,3500,3500,3500,3500]]
            repeat: Tüm sekansı kaç kez tekrar edeceğiz
            velocity: Tüm adımlar için hız
            max_current: Tüm adımlar için maks akım
            pos_tolerance: Pozisyon toleransı
            step_timeout_s: Her adım için timeout
            settle_between_s: Adımlar arası bekleme
            retry_on_fail: Bir adım başarısız olursa kaç kez yeniden denenecek
                           (SM watchdog recover sonrası toparlanma için)

        Returns:
            (status_code, message, completed_steps)
        """
        if not steps:
            return (400, "Empty sequence", 0)

        total_steps = len(steps) * repeat
        completed = 0

        for cycle in range(repeat):
            for step_idx, pos in enumerate(steps):
                if len(pos) != self.dof:
                    return (400, f"Step {step_idx}: position must have {self.dof} values", completed)

                # Retry mantığı: watchdog recover sonrası hareket başarılı olabilir
                last_status = 500
                last_msg = ""
                for attempt in range(retry_on_fail + 1):
                    status_code, msg = self.move_to_position(
                        position=list(pos),
                        velocity=velocity,
                        max_current=max_current,
                        wait=True,
                        timeout_s=step_timeout_s,
                        pos_tolerance=pos_tolerance,
                    )
                    last_status = status_code
                    last_msg = msg

                    if status_code == 200:
                        break  # Başarılı, retry'a gerek yok

                    # Başarısızlık durumunda: eğer 408 (timeout) ise kısa bekle, tekrar dene
                    if status_code == 408 and attempt < retry_on_fail:
                        time.sleep(1.0)  # Recover için süre ver
                        continue

                    # 409 (alarm), 503 (bağlantı yok) gibi durumlarda retry anlamsız
                    break

                if last_status != 200:
                    return (
                        last_status,
                        f"Sequence failed at cycle {cycle + 1}, step {step_idx + 1}: {last_msg}",
                        completed,
                    )

                completed += 1

                if settle_between_s > 0:
                    time.sleep(settle_between_s)

        return (200, f"Sequence completed: {repeat} cycles × {len(steps)} steps = {total_steps}", completed)

    def bye_bye(
        self,
        repeat: int = 3,
        velocity: int = 12000,
        max_current: int = 500,
    ) -> Tuple[int, str, int]:
        """
        Bye bye hareketi: motor 3-6 yukarı-aşağı sallanır.

        - Motor 1-2 (başparmak) sabit kalır
        - Motor 3-6 (işaret, orta, yüzük, serçe) sallanır
        - 1 tekrar = aşağı-yukarı tam bir hareket

        Timeout ve settle süreleri SM watchdog recover'ına tolerans için
        yüksek tutulmuştur.
        """
        steps = [
            [0, 0, 0, 0, 0, 0],                  # BYE_UP: hepsi açık (parmaklar yukarı)
            [0, 0, 3500, 3500, 3500, 3500],       # BYE_DOWN: motor 3-6 aşağı
        ]
        return self.execute_sequence(
            steps=steps,
            repeat=repeat,
            velocity=velocity,
            max_current=max_current,
            pos_tolerance=500,
            step_timeout_s=10.0,          # 3→10: recover ihtimaline tolerans
            settle_between_s=0.3,         # 0.1→0.3: watchdog margin
        )

    # ------------------------------------------------------------------
    # GRIP TESPİTİ (Bardak/nesne kavrama kontrolü)
    # ------------------------------------------------------------------

    def check_grip(
        self,
        target_position: list,
        current_threshold: Optional[int] = None,
        deviation_threshold: Optional[int] = None,
        overgrip_current: Optional[int] = None,
    ) -> Tuple[str, dict]:
        """
        Robot elin bir nesneyi (bardak vb.) kavrayıp kavramadığını tespit et.

        Mantık:
          - Akım yüksek + Sapma yüksek → GRIP_OK (motor nesneyle karşılaştı, durdu)
          - Akım düşük + Sapma düşük → NO_OBJECT (havada serbestçe hareket etti)
          - Akım çok yüksek → OVERGRIP (aşırı sıkma, tehlikeli)
          - Diğer durumlar → UNCERTAIN

        Eşikler None ise settings.py'den okunur:
          - GRIP_CURRENT_THRESHOLD (varsayılan 80)
          - GRIP_DEVIATION_THRESHOLD (varsayılan 1500)
          - OVERGRIP_CURRENT (varsayılan 600)

        Args:
            target_position: Hedef pozisyon listesi (6 motor)
            current_threshold: GRIP_OK için min akım (None=settings'ten oku)
            deviation_threshold: GRIP_OK için min pozisyon sapması (None=settings'ten oku)
            overgrip_current: OVERGRIP için akım eşiği (None=settings'ten oku)

        Returns:
            (durum, detay_dict)
            durum: "GRIP_OK" | "NO_OBJECT" | "OVERGRIP" | "UNCERTAIN"
        """
        # Settings'ten eşikleri oku
        if current_threshold is None or deviation_threshold is None or overgrip_current is None:
            try:
                from settings import (
                    GRIP_CURRENT_THRESHOLD,
                    GRIP_DEVIATION_THRESHOLD,
                    OVERGRIP_CURRENT,
                    NO_OBJECT_DEVIATION,
                )
                if current_threshold is None:
                    current_threshold = GRIP_CURRENT_THRESHOLD
                if deviation_threshold is None:
                    deviation_threshold = GRIP_DEVIATION_THRESHOLD
                if overgrip_current is None:
                    overgrip_current = OVERGRIP_CURRENT
                no_object_dev = NO_OBJECT_DEVIATION
            except ImportError:
                # Fallback değerler
                if current_threshold is None:
                    current_threshold = 80
                if deviation_threshold is None:
                    deviation_threshold = 1500
                if overgrip_current is None:
                    overgrip_current = 600
                no_object_dev = 500
        else:
            no_object_dev = 500

        st = self.get_status()
        if not st or "motors" not in st:
            return ("UNKNOWN", {"error": "Status alınamadı"})

        motors = st["motors"]
        if len(motors) != len(target_position):
            return ("UNKNOWN", {"error": "Motor sayısı uyumsuz"})

        # Metrikler
        max_current = max(m.get("current", 0) for m in motors)
        avg_current = sum(m.get("current", 0) for m in motors) / len(motors)
        max_deviation = max(
            abs(m.get("position", 0) - target_position[i])
            for i, m in enumerate(motors)
        )

        # Motor detayları
        motor_details = []
        for i, m in enumerate(motors):
            motor_details.append({
                "motor_id": m.get("motor_id", i + 1),
                "target": target_position[i],
                "actual": m.get("position", 0),
                "deviation": abs(m.get("position", 0) - target_position[i]),
                "current": m.get("current", 0),
            })

        # GRIP mantığı - akıllı versiyon
        detail = {
            "max_current": max_current,
            "avg_current": round(avg_current, 1),
            "max_deviation": max_deviation,
            "thresholds": {
                "grip_current": current_threshold,
                "grip_deviation": deviation_threshold,
                "overgrip_current": overgrip_current,
            },
            "motors": motor_details,
        }

        # ÖNCE NO_OBJECT kontrolü: Akım düşük ve hedefe ulaştı → havada
        if max_current < current_threshold and max_deviation < no_object_dev:
            return ("NO_OBJECT", detail)

        # SONRA GRIP/OVERGRIP kontrolü: Akım yüksek (motor zorlandı)
        if max_current >= current_threshold:
            # Akım çok yüksek VE sapma çok yüksekse → OVERGRIP
            # (Motor limitine dayandı ama nesneye hala çarpıyor)
            if max_current > overgrip_current and max_deviation > (deviation_threshold * 1.5):
                return ("OVERGRIP", detail)
            # Normal GRIP: motor zorlandı ve sapma var (nesne kavrandı)
            elif max_deviation > deviation_threshold / 3:  # 500'den büyük sapma
                return ("GRIP_OK", detail)
            # Akım yüksek ama sapma yok → hedefe ulaştı, akım ilk harekette yüksek oldu
            else:
                return ("NO_OBJECT", detail)

        # UNCERTAIN: Ara durum
        return ("UNCERTAIN", detail)

    def get_status(self) -> Optional[dict]:
        """Motor snapshot'larını dict olarak dön."""
        resp = self.send_command(CmdType.GET_STATUS, timeout_s=2.0)
        if resp.status == RespStatus.OK:
            return resp.data
        return None

    def get_connection_state(self) -> Optional[dict]:
        """Bağlantı durumu: connected, uptime_s, watchdog_count, reconnect_count."""
        resp = self.send_command(CmdType.GET_CONNECTION_STATE, timeout_s=2.0)
        if resp.status == RespStatus.OK:
            return resp.data
        return None
