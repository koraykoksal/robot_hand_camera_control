# -*- coding: utf-8 -*-
"""
EtherCAT Worker Process — Sadeleştirilmiş versiyon
===================================================

Bu dosya AYRI BİR PROCESS'TE çalışır.

Tasarım prensibi: **Tekerleği yeniden icat etme.**
    - HandController zaten tüm setup mantığını biliyor
    - Worker sadece HandController'ı kullanır, command dispatcher olarak davranır
    - Ana process ile queue üzerinden konuşur

Ana process ile IPC:
    cmd_queue    (main -> worker)   komutlar
    resp_queue   (worker -> main)   cevaplar
    status_queue (worker -> main)   asenkron durum akışı
"""

from __future__ import annotations

import os
import sys
import time
import traceback
import multiprocessing as mp
from typing import List, Optional

# sys.path setup - worker process kendi interpreter'ında çalışır
THIS_DIR = os.path.dirname(os.path.abspath(__file__))
PARENT_DIR = os.path.dirname(THIS_DIR)
for path in [THIS_DIR, PARENT_DIR,
             os.path.join(PARENT_DIR, "services"),
             os.path.join(PARENT_DIR, "cli"),
             os.path.join(PARENT_DIR, "config")]:
    if path not in sys.path:
        sys.path.insert(0, path)

from ipc_protocol import (
    Command, Response, StatusUpdate,
    CmdType, RespStatus, UpdateType,
    MotorSnapshot,
)


# ---------------------------------------------------------------------------
# Worker entry point
# ---------------------------------------------------------------------------
def worker_main(
    cmd_queue: mp.Queue,
    resp_queue: mp.Queue,
    status_queue: mp.Queue,
    dof: int = 6,
):
    """Worker process entry point."""

    # KRITIK: stdout'u status_queue'ya yönlendir
    # Bu sayede worker içindeki herhangi bir print() mesajı
    # ana process'in terminal'ine tek bir kez yansır (duplikasyon önleme).
    # ethercat_master.py 60+ print() kullanıyor, hepsini _log'a çevirmek yerine
    # tek noktadan yakalayıp ana process'e forward ediyoruz.
    class _StdoutToQueue:
        """sys.stdout yerine geçen file-like object."""
        def __init__(self, queue):
            self._queue = queue
            self._buffer = ""

        def write(self, text: str):
            if not text:
                return
            # Satır satır buffer'la ve gönder
            self._buffer += text
            while "\n" in self._buffer:
                line, self._buffer = self._buffer.split("\n", 1)
                if line:  # boş satırları atla
                    try:
                        self._queue.put(
                            StatusUpdate(
                                update_type=UpdateType.LOG,
                                data={"message": line}
                            ),
                            timeout=0.5,
                        )
                    except Exception:
                        pass

        def flush(self):
            if self._buffer:
                try:
                    self._queue.put(
                        StatusUpdate(
                            update_type=UpdateType.LOG,
                            data={"message": self._buffer}
                        ),
                        timeout=0.5,
                    )
                except Exception:
                    pass
                self._buffer = ""

    # stdout'u değiştir - worker process boyunca aktif
    sys.stdout = _StdoutToQueue(status_queue)

    try:
        # Worker-specific imports
        from hand_controller import HandController
        from ethercat_master import _apply_windows_optimizations
    except Exception as e:
        _push_status(status_queue, UpdateType.WORKER_ERROR, {
            "error": f"Import failed: {e}",
            "traceback": traceback.format_exc(),
        })
        return

    # Windows optimizasyonları (timer 1ms + priority HIGH)
    _apply_windows_optimizations()

    # HandController - tüm setup işlerini bu yapıyor zaten
    # Callback'ler ile status update'leri ana process'e yayıyoruz
    state = {"shutdown": False}

    def on_log(msg: str):
        _push_log(status_queue, msg)

    def on_alarm(motor_id: int, code: int):
        _push_status(status_queue, UpdateType.ALARM_RAISED, {
            "motor_id": motor_id,
            "code": code,
        })

    def on_disconnected(reason: str):
        _push_status(status_queue, UpdateType.CONNECTION_LOST, {"reason": reason})

    def on_reconnected():
        _push_status(status_queue, UpdateType.CONNECTION_RESTORED, {})

    hand = HandController(
        dof=dof,
        auto_reset=True,
        monitor_interval_s=10.0,
        prep_before_move=True,
        auto_reconnect=True,
        on_log=on_log,
        on_alarm=on_alarm,
        on_disconnected=on_disconnected,
        on_reconnected=on_reconnected,
    )

    _push_log(status_queue, "🟢 EtherCAT worker process başladı")

    # Command dispatcher
    dispatcher = CommandDispatcher(hand, resp_queue, status_queue, dof, state)

    try:
        while not state["shutdown"]:
            try:
                cmd: Command = cmd_queue.get(timeout=1.0)
                dispatcher.dispatch(cmd)
            except Exception as e:
                # Queue timeout normal - skip
                if "Empty" in type(e).__name__ or "timeout" in str(e).lower():
                    continue
                _push_status(status_queue, UpdateType.WORKER_ERROR, {
                    "error": str(e),
                    "traceback": traceback.format_exc(),
                })
                time.sleep(0.3)
    finally:
        _push_log(status_queue, "🔻 Worker cleanup...")
        try:
            if hand.is_connected:
                hand.disconnect()
        except Exception:
            pass
        _push_log(status_queue, "👋 Worker kapandı")


# ---------------------------------------------------------------------------
# Command Dispatcher — HandController metodlarına delegasyon
# ---------------------------------------------------------------------------
class CommandDispatcher:
    def __init__(self, hand, resp_queue, status_queue, dof, state):
        self.hand = hand
        self.resp_queue = resp_queue
        self.status_queue = status_queue
        self.dof = dof
        self.state = state

    def dispatch(self, cmd: Command) -> None:
        handlers = {
            CmdType.SCAN_ADAPTERS: self._scan,
            CmdType.CONNECT: self._connect,
            CmdType.DISCONNECT: self._disconnect,
            CmdType.SHUTDOWN: self._shutdown,
            CmdType.ENABLE_ALL: self._enable_all,
            CmdType.DISABLE_ALL: self._disable_all,
            CmdType.MOVE_TO_POSITION: self._move,
            CmdType.HOME_ALL: self._home,
            CmdType.STOP_ALL: self._stop,
            CmdType.CLEAR_ALARMS: self._clear_alarms,
            CmdType.GET_STATUS: self._get_status,
            CmdType.GET_CONNECTION_STATE: self._get_connection_state,
        }
        handler = handlers.get(cmd.cmd_type)
        if not handler:
            self._respond(cmd, RespStatus.UNKNOWN_COMMAND, f"Unknown: {cmd.cmd_type}")
            return
        try:
            handler(cmd)
        except Exception as e:
            self._respond(cmd, RespStatus.ERROR, f"Exception: {e}", {
                "traceback": traceback.format_exc(),
            })

    # ---- handlers ----------------------------------------------------------
    def _scan(self, cmd: Command):
        names = self.hand.scan_interfaces()
        self._respond(cmd, RespStatus.OK, f"{len(names)} adapter", {"adapters": names})

    def _connect(self, cmd: Command):
        idx = cmd.payload.get("adapter_index")
        if idx is None:
            self._respond(cmd, RespStatus.ERROR, "adapter_index required")
            return
        ok = self.hand.connect(idx)
        if ok:
            self._respond(cmd, RespStatus.OK, "Connected", {
                "adapter": self.hand._selected_adapter or "",
                "dof": self.hand.dof,
            })
        else:
            self._respond(cmd, RespStatus.ERROR, "Connect failed")

    def _disconnect(self, cmd: Command):
        self.hand.disconnect()
        self._respond(cmd, RespStatus.OK, "Disconnected")

    def _shutdown(self, cmd: Command):
        self._respond(cmd, RespStatus.OK, "Shutting down")
        self.state["shutdown"] = True

    def _enable_all(self, cmd: Command):
        v = cmd.payload.get("velocity", 12000)
        c = cmd.payload.get("max_current", 700)
        results = self.hand.enable_all(velocity=v, max_current=c)
        ok_count = sum(1 for r in results if r[1])
        if ok_count == self.dof:
            self._respond(cmd, RespStatus.OK, f"All {self.dof} motors enabled")
        else:
            self._respond(cmd, RespStatus.ERROR, f"{ok_count}/{self.dof} enabled")

    def _disable_all(self, cmd: Command):
        # HandController'da disable_all yok, tek tek yap
        if not self.hand.is_connected or not self.hand._lhp:
            self._respond(cmd, RespStatus.NOT_CONNECTED, "Not connected")
            return
        for mid in range(1, self.dof + 1):
            try:
                self.hand._lhp.set_enable(mid, False)
            except Exception:
                pass
        self._respond(cmd, RespStatus.OK, "All motors disabled")

    def _move(self, cmd: Command):
        position = cmd.payload.get("position", [])
        velocity = cmd.payload.get("velocity", 12000)
        max_current = cmd.payload.get("max_current", 700)
        wait = cmd.payload.get("wait", True)
        timeout_s = cmd.payload.get("timeout_s", 5.0)
        pos_tolerance = cmd.payload.get("pos_tolerance", 500)

        ok = self.hand.move_to_position(
            position,
            velocity=velocity,
            max_current=max_current,
        )
        if not ok:
            self._respond(cmd, RespStatus.ERROR, "Move command failed")
            return

        if not wait:
            self._respond(cmd, RespStatus.OK, "Move sent (no wait)")
            return

        reached, reason = self.hand.wait_reached(
            timeout_s=timeout_s,
            target_pos=position,
            pos_tolerance=pos_tolerance,
        )
        if reached:
            if reason == "grip_detected":
                self._respond(cmd, RespStatus.OK, "Position reached (grip detected)")
            else:
                self._respond(cmd, RespStatus.OK, "Position reached")
        elif reason == "timeout":
            self._respond(cmd, RespStatus.TIMEOUT, "Not reached in time")
        elif reason == "alarm":
            self._respond(cmd, RespStatus.CONFLICT, "Motor alarm")
        elif reason == "alarm_locked":
            self._respond(cmd, RespStatus.NOT_CONNECTED, "Motor locked")
        else:
            self._respond(cmd, RespStatus.ERROR, f"Wait failed: {reason}")

    def _home(self, cmd: Command):
        fc = cmd.payload.get("force_current", 400)
        self.hand.home_all(force_current=fc)
        self._respond(cmd, RespStatus.OK, "Home command sent")

    def _stop(self, cmd: Command):
        self.hand.stop_all()
        self._respond(cmd, RespStatus.OK, "Stopped")

    def _clear_alarms(self, cmd: Command):
        motor_id = cmd.payload.get("motor_id", 0)
        self.hand.clear_alarms(motor_id=motor_id)
        self._respond(cmd, RespStatus.OK, "Alarms cleared")

    def _get_status(self, cmd: Command):
        status_list = self.hand.get_status()
        motors = []
        for s in status_list:
            motors.append({
                "motor_id": s.motor_id,
                "position": s.position,
                "angle": s.angle,
                "current": s.current,
                "alarm": s.alarm,
                "enabled": s.enabled,
                "reached": s.reached,
            })
        self._respond(cmd, RespStatus.OK, "Status", {
            "motors": motors,
            "connected": self.hand.is_connected,
            "enabled": self.hand.is_enabled,
        })

    def _get_connection_state(self, cmd: Command):
        self._respond(cmd, RespStatus.OK, "Connection state", {
            "connected": self.hand.is_connected,
            "enabled": self.hand.is_enabled,
            "adapter": self.hand.selected_adapter or "",
        })

    # ---- helpers -----------------------------------------------------------
    def _respond(self, cmd: Command, status: RespStatus,
                 message: str = "", data: Optional[dict] = None):
        resp = Response(
            request_id=cmd.request_id,
            status=status,
            message=message,
            data=data or {},
        )
        try:
            self.resp_queue.put(resp, timeout=1.0)
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------
def _push_status(queue, update_type: UpdateType, data: dict):
    try:
        queue.put(StatusUpdate(update_type=update_type, data=data), timeout=0.5)
    except Exception:
        pass


def _push_log(queue, message: str):
    try:
        queue.put(
            StatusUpdate(update_type=UpdateType.LOG, data={"message": message}),
            timeout=0.5,
        )
    except Exception:
        pass
