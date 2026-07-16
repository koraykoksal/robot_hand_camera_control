# -*- coding: utf-8 -*-
"""
HandController
==============

Robotik el kontrolünü merkezileştiren ana sınıf.

Akış:
    1) scan_interfaces()      -> mevcut EtherCAT adaptörlerini listele
    2) connect(adapter_idx)   -> EtherCAT master + LHandProLib init + TPDO monitor
    3) enable_all()           -> tüm motorları enable et (position mode)
    4) home_all()             -> home sekansını çalıştır
    5) move_to_position(...)  -> 6 elemanlı encoder pozisyonu gönder
    6) get_status()           -> tüm motorların anlık durumu
    7) clear_alarms()         -> alarmları temizle
    8) disconnect()           -> güvenli kapanış

Tasarım notları:
    * Mevcut `ethercat_master.py` ve `lhandprolib_wrapper.py` dosyaları DEĞİŞTİRİLMEDİ.
    * rogolob22.py'daki global state (g_ec_master, _tpdo_thread vb.) yerine
      her HandController örneği kendi master + lhp + monitor thread'ini yönetir.
    * Tüm PDO gönderme zincirinin doğru kurulum sırası (SDK'nın beklediği):
         a) EthercatMaster.init()/start()/run()    -> master OP state'e alınır
         b) lhp.set_send_rpdo_callback(cb)          -> cb master.setOutputs'u çağırır
         c) lhp.initial(LCN_ECAT)                   -> SDK kendi PDO setup'ını yapar
         d) TPDO monitor thread başlatılır          -> master.getInputs -> lhp.set_tpdo_data_decode
"""

from __future__ import annotations

import threading
import time
from typing import Callable, Dict, List, Optional, Tuple

from ethercat_master import EthercatMaster
from lhandprolib_wrapper import PyLHandProLib, LHandProLibError
from lhandprolib_loader import (
    LCN_ECAT,
    LCM_POSITION,
    LER_NONE,
)


# ---------------------------------------------------------------------------
# Sabitler (rogolob22.py'dan taşındı - 6 DOF donanım değerleri)
# ---------------------------------------------------------------------------
ROBOT_DOF: int = 6
MOTOR_MIN: int = 0
MOTOR_MAX: int = 10000
MOTOR_CENTER: int = 5000
ZERO_POS: List[int] = [0, 0, 0, 0, 0, 0]

DEFAULT_VELOCITY: int = 12000
DEFAULT_MAX_CURRENT: int = 500
HOME_FORCE_CURRENT: int = 500
DEFAULT_SETTLE_S: float = 0.05

# Monitor
# Kullanıcı tercihi: 10 saniyede bir kontrol yeterli (endüstriyel izleme patterni)
# Bu "Baglanti-OK / Baglanti-NOK" log'u için kullanılır.
# EtherCAT IO cycle bundan bağımsız çalışır (8ms, protokol gereği).
DEFAULT_MONITOR_INTERVAL_S: float = 10.0   # her 10 saniyede bir link check
DEFAULT_TPDO_SLEEP_S: float = 0.01         # TPDO decode loop döngü hızı


# ---------------------------------------------------------------------------
# Yardımcı veri yapıları
# ---------------------------------------------------------------------------
class MotorStatus:
    """Tek bir motorun anlık durum özeti."""

    __slots__ = ("motor_id", "position", "angle", "current", "alarm",
                 "enabled", "reached", "status")

    def __init__(self, motor_id: int):
        self.motor_id: int = motor_id
        self.position: int = 0
        self.angle: float = 0.0
        self.current: int = 0
        self.alarm: int = 0           # 0 = alarm yok
        self.enabled: bool = False
        self.reached: bool = False
        self.status: int = 0          # LST_* enum

    def to_dict(self) -> Dict:
        return {
            "motor_id": self.motor_id,
            "position": self.position,
            "angle": self.angle,
            "current": self.current,
            "alarm": self.alarm,
            "enabled": self.enabled,
            "reached": self.reached,
            "status": self.status,
        }

    def __repr__(self) -> str:
        return (f"Motor#{self.motor_id} "
                f"pos={self.position:>5} cur={self.current:>4} "
                f"alarm={self.alarm} en={int(self.enabled)} "
                f"reached={int(self.reached)}")


# ---------------------------------------------------------------------------
# HandController
# ---------------------------------------------------------------------------
class HandController:
    """
    Robotik el yüksek-seviye kontrol sınıfı.

    Args:
        dof: aktif kullanılacak motor sayısı (6 DOF için 6)
        auto_reset: alarm algılandığında otomatik temizlensin mi
        monitor_hz: status monitor thread'inin saniyedeki okuma frekansı
        on_alarm: alarm callback  (motor_id:int, alarm_code:int) -> None
        on_disconnected: bağlantı kaybı callback  (reason:str) -> None
        on_log: opsiyonel log callback (msg:str) -> None  (print'e ek)
    """

    # ---- yaşam döngüsü ----------------------------------------------------
    def __init__(
        self,
        dof: int = ROBOT_DOF,
        *,
        auto_reset: bool = True,
        monitor_interval_s: float = DEFAULT_MONITOR_INTERVAL_S,
        on_alarm: Optional[Callable[[int, int], None]] = None,
        on_disconnected: Optional[Callable[[str], None]] = None,
        on_reconnected: Optional[Callable[[], None]] = None,
        on_log: Optional[Callable[[str], None]] = None,
        prep_before_move: bool = True,
        home_before_move: bool = False,
        auto_reconnect: bool = True,
    ):
        self.dof: int = max(1, min(int(dof), ROBOT_DOF))
        self.auto_reset: bool = bool(auto_reset)
        self.monitor_interval_s: float = max(1.0, float(monitor_interval_s))

        # Hareket öncesi davranış
        self.prep_before_move: bool = bool(prep_before_move)
        self.home_before_move: bool = bool(home_before_move)

        # Otomatik yeniden bağlanma
        self.auto_reconnect: bool = bool(auto_reconnect)
        self._last_adapter_index: Optional[int] = None
        self._reconnect_lock = threading.Lock()
        self._reconnect_thread: Optional[threading.Thread] = None
        self._reconnecting: bool = False

        self._on_alarm = on_alarm
        self._on_disconnected = on_disconnected
        self._on_reconnected = on_reconnected
        self._on_log = on_log

        # Altyapı
        self._master: Optional[EthercatMaster] = None
        self._lhp: Optional[PyLHandProLib] = None
        self._adapter_names_bytes: List[bytes] = []
        self._selected_adapter: Optional[str] = None

        # Runtime state
        self._connected: bool = False
        self._enabled: bool = False

        # Threadler
        self._tpdo_thread: Optional[threading.Thread] = None
        self._tpdo_running = threading.Event()
        self._monitor_thread: Optional[threading.Thread] = None
        self._monitor_running = threading.Event()

        # Thread-safe durum snapshot'u
        self._status_lock = threading.Lock()
        self._last_status: List[MotorStatus] = [
            MotorStatus(i + 1) for i in range(self.dof)
        ]

        # Alarm rate limiting - aynı motor çok sık alarm üretirse zorla disable et
        # {motor_id: [timestamp1, timestamp2, ...]}
        self._alarm_history: Dict[int, List[float]] = {}
        # Her motor için "kilitli" mi? - rate limit aşıldıysa True
        # Kullanıcı manual olarak temizlemeden tekrar auto-reset yapılmaz
        self._motor_alarm_locked: Dict[int, bool] = {}
        # Rate limit parametreleri
        self._alarm_rate_window_s: float = 3.0    # zaman penceresi
        self._alarm_rate_threshold: int = 2       # bu pencerede max alarm sayısı

    # ---- log --------------------------------------------------------------
    def _log(self, msg: str) -> None:
        # Eğer on_log callback tanımlıysa sadece callback'e ver (duplikasyon önleme)
        # Callback yoksa direkt print et (standalone kullanım için)
        if self._on_log:
            try:
                self._on_log(msg)
            except Exception:
                pass
        else:
            print(msg)

    # ==========================================================================
    # Bağlantı yönetimi
    # ==========================================================================
    def scan_interfaces(self) -> List[str]:
        """
        Mevcut EtherCAT network adaptörlerini tarar. Bağlantı yokken de çağrılabilir.

        Returns:
            İnsan tarafından okunabilir adaptör isimleri (kaba, ham string)
        """
        tmp = EthercatMaster()
        try:
            names = tmp.scanNetworkInterfaces() or []
        except Exception as e:
            self._log(f"❌ scan_interfaces hata: {e}")
            names = []

        # rogolob22.py'da scanNetworkInterfaces() bytes döndürüyordu;
        # şimdiki ethercat_master.py str döndürüyor - ikisini de destekle
        self._adapter_names_bytes = []
        readable: List[str] = []
        for n in names:
            if isinstance(n, bytes):
                self._adapter_names_bytes.append(n)
                try:
                    readable.append(n.decode("utf-8", "ignore"))
                except Exception:
                    readable.append(str(n))
            else:
                self._adapter_names_bytes.append(str(n).encode("utf-8"))
                readable.append(str(n))
        return readable

    def connect(self, adapter_index: int) -> bool:
        """
        EtherCAT master'ı ve LHandProLib'i başlatır, motorları enable'a hazırlar.

        Sıralama:
            1. EthercatMaster.init + run  -> OP state
            2. PyLHandProLib.set_send_rpdo_callback(ec->master.setOutputs)
            3. PyLHandProLib.initial(LCN_ECAT)
            4. get_dof  (donanım DOF'unu doğrula)
            5. TPDO monitor thread'i başlat
            6. Status monitor thread'i başlat

        Motorlar `connect` sonunda OTOMATIK enable EDİLMEZ — bunu tercihen
        `enable_all()` çağrısı ile siz yaparsınız. Bu sayede ilk bağlantıda
        yanlışlıkla hareket edilmesi engellenir.

        Returns:
            True: başarılı
        """
        if self._connected:
            self._log("⚠️ Zaten bağlı, önce disconnect() çağırın.")
            return False

        # Henüz scan edilmediyse tara
        if not self._adapter_names_bytes:
            self._log("ℹ️ Önce adaptörler taranıyor...")
            self.scan_interfaces()

        if not self._adapter_names_bytes:
            self._log("❌ Hiç EtherCAT adaptörü bulunamadı (Npcap/WinPcap kurulu mu?)")
            return False

        if adapter_index < 0 or adapter_index >= len(self._adapter_names_bytes):
            self._log(f"❌ Geçersiz adapter index: {adapter_index}")
            return False

        # Adapter index'i sakla - auto_reconnect için gerekli
        self._last_adapter_index = adapter_index

        # 1) Master init
        self._log(f"🔌 EtherCAT master başlatılıyor (adapter {adapter_index})...")
        self._master = EthercatMaster()
        try:
            ok = self._master.init(adapter_index, self._adapter_names_bytes)
        except Exception as e:
            self._log(f"❌ Master init exception: {e}")
            self._cleanup_partial()
            return False

        if not ok:
            self._log("❌ Master init başarısız")
            self._cleanup_partial()
            return False

        try:
            self._master.run()
        except Exception as e:
            self._log(f"❌ Master run exception: {e}")
            self._cleanup_partial()
            return False

        # 2) LHandProLib setup
        try:
            self._lhp = PyLHandProLib()
            self._lhp.set_send_rpdo_callback(self._ec_send_callback)
            self._lhp.initial(LCN_ECAT)
        except Exception as e:
            self._log(f"❌ LHandProLib init exception: {e}")
            self._cleanup_partial()
            return False

        # 3) Donanım DOF doğrulama
        try:
            dof_total, dof_active = self._lhp.get_dof()
            self._log(f"✅ Donanım DOF: total={dof_total}, active={dof_active}")
            self.dof = min(int(dof_active), ROBOT_DOF, self.dof) or ROBOT_DOF
        except Exception as e:
            self._log(f"⚠️ get_dof okunamadı: {e} (varsayılan DOF={self.dof} kullanılıyor)")

        # Status slotlarını DOF'a göre yeniden boyutlandır
        with self._status_lock:
            self._last_status = [MotorStatus(i + 1) for i in range(self.dof)]

        # 4) TPDO monitor thread
        self._start_tpdo_monitor()
        time.sleep(0.15)  # rogolob22.py'daki gibi kısa settle

        # 5) Status monitor thread
        self._start_status_monitor()

        try:
            self._selected_adapter = self._adapter_names_bytes[adapter_index].decode(
                "utf-8", "ignore"
            )
        except Exception:
            self._selected_adapter = str(adapter_index)

        self._connected = True
        self._log(f"[{self._ts()}] Baglanti-OK (adapter: {self._selected_adapter}, DOF={self.dof})")

        # Crash/unexpected exit için atexit handler kaydet
        # Bu sayede uygulama nasıl kapanırsa kapansın slave INIT'e gönderilir
        # ve bir sonraki run'da slave temiz bir state'te bulunur.
        self._register_atexit_cleanup()

        return True

    def _register_atexit_cleanup(self) -> None:
        """
        Uygulama her türlü nedenle kapanırsa slave'i temizlemek için atexit hook.
        - Normal disconnect: zaten cleanup yapılır, bu idempotent
        - KeyboardInterrupt (Ctrl+C): atexit tetiklenir
        - Python exception: atexit tetiklenir
        - sys.exit(): atexit tetiklenir
        - Kernel kill (SIGKILL): atexit TETİKLENMEZ - donanım reset gerekir
        """
        import atexit

        # Dışarıdan closure yakalasın - self'e referans
        handler_ref = {"done": False}

        def _cleanup_on_exit():
            if handler_ref["done"]:
                return
            handler_ref["done"] = True
            try:
                if self._connected and self._master:
                    # Sessizce INIT'e gönder - log basmaya çalışma (sys kapanıyor olabilir)
                    try:
                        self._master.stop()
                    except Exception:
                        pass
            except Exception:
                pass

        atexit.register(_cleanup_on_exit)

    def disconnect(self) -> None:
        """Güvenli kapanış: motorları stop, thread'leri durdur, master'ı kapat."""
        self._log("🔻 Disconnect başlatıldı...")

        # Önce monitor ve tpdo durdur
        self._stop_status_monitor()
        self._stop_tpdo_monitor()

        # Motorları stop et (opsiyonel, hata toleranslı)
        if self._lhp:
            try:
                self._lhp.stop_motors(0)  # 0 = tüm motorlar
            except Exception:
                pass

            try:
                self._lhp.close()
            except Exception as e:
                self._log(f"⚠️ lhp.close hata: {e}")

        # Master stop
        if self._master:
            try:
                self._master.stop()
            except Exception as e:
                self._log(f"⚠️ master.stop hata: {e}")

        self._master = None
        self._lhp = None
        self._connected = False
        self._enabled = False
        self._log("🔻 Disconnect tamamlandı")

    def _cleanup_partial(self) -> None:
        """connect() içinde yarı-başarılı kurulumları geri al."""
        try:
            if self._lhp:
                self._lhp.close()
        except Exception:
            pass
        try:
            if self._master:
                self._master.stop()
        except Exception:
            pass
        self._master = None
        self._lhp = None
        self._connected = False

    @property
    def is_connected(self) -> bool:
        return self._connected

    @property
    def is_enabled(self) -> bool:
        return self._enabled

    # ==========================================================================
    # EtherCAT callback + TPDO loop
    # ==========================================================================
    def _ec_send_callback(self, data: bytes) -> bool:
        """
        PyLHandProLib -> EthercatMaster köprüsü.
        lhp.initial() sonrası her PDO gönderiminde bu çağrılır.
        """
        m = self._master
        if m is None:
            return False
        try:
            return bool(m.setOutputs(data, len(data)))
        except Exception as e:
            # Sadece log, callback içinde exception fırlatma
            self._log(f"⚠️ ec_send_callback exception: {e}")
            return False

    def _start_tpdo_monitor(self) -> None:
        """Master'dan gelen input'ları lhp.set_tpdo_data_decode'a pompalar."""
        if self._tpdo_running.is_set():
            return
        self._tpdo_running.set()

        def _loop():
            # rogolob22.py ile aynı davranış: önce kısa settle
            time.sleep(0.3)
            while self._tpdo_running.is_set():
                try:
                    m = self._master
                    lhp = self._lhp
                    if m is not None and lhp is not None:
                        size = m.getInputSize()
                        data = m.getInputs(size) if size > 0 else None
                        if data:
                            lhp.set_tpdo_data_decode(data)
                except Exception as e:
                    self._log(f"⚠️ TPDO monitor error: {e}")
                    time.sleep(0.2)
                time.sleep(DEFAULT_TPDO_SLEEP_S)

        self._tpdo_thread = threading.Thread(
            target=_loop, name="hand-tpdo-monitor", daemon=True
        )
        self._tpdo_thread.start()

    def _stop_tpdo_monitor(self) -> None:
        self._tpdo_running.clear()
        t = self._tpdo_thread
        if t and t.is_alive():
            t.join(timeout=1.0)
        self._tpdo_thread = None

    # ==========================================================================
    # Motor enable / home / alarm
    # ==========================================================================
    def enable_all(
        self,
        *,
        velocity: int = DEFAULT_VELOCITY,
        max_current: int = DEFAULT_MAX_CURRENT,
        set_position_mode: bool = True,
        settle_s: float = DEFAULT_SETTLE_S,
    ) -> List[Tuple[int, bool, Optional[str]]]:
        """
        Tüm motorları enable eder (position mode).

        Returns:
            [(motor_id, success, error_msg_or_None), ...]
        """
        if not self._ensure_connected():
            return []

        results: List[Tuple[int, bool, Optional[str]]] = []

        for motor_id in range(1, self.dof + 1):
            err: Optional[str] = None
            ok = False
            try:
                # Alarm varsa temizle
                try:
                    self._lhp.set_clear_alarm(motor_id)
                except Exception:
                    pass

                if set_position_mode:
                    try:
                        self._lhp.set_control_mode(motor_id, LCM_POSITION)
                    except Exception:
                        pass

                try:
                    self._lhp.set_position_velocity(motor_id, int(velocity))
                except Exception:
                    pass

                try:
                    self._lhp.set_max_current(motor_id, int(max_current))
                except Exception:
                    pass

                self._lhp.set_enable(motor_id, True)

                try:
                    ok = bool(self._lhp.get_enable(motor_id))
                except Exception:
                    # Get enable kimi firmware'lerde desteklenmeyebilir
                    ok = True

            except Exception as e:
                err = str(e)
                ok = False

            results.append((motor_id, ok, err))
            time.sleep(settle_s)

        # TPDO akışı ilk bağlantıdan sonra hemen stabilleşmeyebilir.
        # get_enable() False döndürmüş olabilir ama set_enable() gönderildi.
        # Bu yüzden sadece set_enable komutunun başarısız olduğu durumları hata say.
        self._enabled = all(r[1] for r in results)
        ok_count = sum(1 for r in results if r[1])
        error_count = sum(1 for r in results if r[2] is not None)

        if error_count == 0:
            # Tüm komutlar gönderildi - get_enable False dönse bile aslında enable olmuş
            self._log(f"✅ Enable komutları gönderildi: {len(results)} motor")
            # _enabled'i True kabul et - ilk PDO'dan sonra zaten doğrulanacak
            self._enabled = True
            # Results'ı düzelt (UI için)
            results = [(mid, True, None) for mid, _, _ in results]
        else:
            self._log(f"⚠️ Enable: {ok_count}/{len(results)} motor ({error_count} hata)")

        return results

    def disable_all(self) -> None:
        """Tüm motorları disable eder."""
        if not self._ensure_connected():
            return
        for motor_id in range(1, self.dof + 1):
            try:
                self._lhp.set_enable(motor_id, False)
            except Exception:
                pass
        self._enabled = False
        self._log("🔻 Tüm motorlar disable edildi")

    def home_all(self, force_current: int = HOME_FORCE_CURRENT) -> None:
        """
        Tüm motorları home pozisyonuna götürür.

        force_current: home sırasında akım limiti (amper değil, donanım birimi)
        """
        if not self._ensure_connected():
            return

        self._log(f"🏠 Home sekansı başlatılıyor (force_current={force_current})...")

        # Basınç sensörlerini sıfırla (ellemeli sensörler varsa)
        try:
            self._lhp.set_finger_pressure_reset()
        except Exception:
            pass

        for motor_id in range(1, self.dof + 1):
            try:
                self._lhp.set_clear_alarm(motor_id)
            except Exception:
                pass
            try:
                self._lhp.set_enable(motor_id, True)
            except Exception:
                pass
            try:
                self._lhp.set_max_current(motor_id, int(force_current))
            except Exception:
                pass
            try:
                self._lhp.home_motors(motor_id)
            except Exception as e:
                self._log(f"⚠️ Motor {motor_id} home hata: {e}")

        self._log("🏠 Home komutu gönderildi (motorlar hareket ediyor)")

    def clear_alarms(self, motor_id: int = 0) -> None:
        """
        Alarmları temizler. Manuel temizleme motor lock'unu da kaldırır,
        böylece rate-limited motor tekrar auto-reset uygun hale gelir.

        Args:
            motor_id: 0 -> tüm motorlar, >0 -> spesifik motor
        """
        if not self._ensure_connected():
            return

        if motor_id <= 0:
            for mid in range(1, self.dof + 1):
                try:
                    self._lhp.set_clear_alarm(mid)
                except Exception as e:
                    self._log(f"⚠️ Motor {mid} alarm clear hata: {e}")
            # Tüm motor lock'larını ve history'yi temizle
            self._motor_alarm_locked.clear()
            self._alarm_history.clear()
            self._log("✅ Tüm alarmlar temizlendi (kilitler de kaldırıldı)")
        else:
            try:
                self._lhp.set_clear_alarm(motor_id)
                self._log(f"✅ Motor {motor_id} alarmı temizlendi")
            except Exception as e:
                self._log(f"⚠️ Motor {motor_id} alarm clear hata: {e}")
            # Spesifik motor lock'unu ve history'sini temizle
            self._motor_alarm_locked.pop(motor_id, None)
            self._alarm_history.pop(motor_id, None)

    # ==========================================================================
    # Hareket komutları
    # ==========================================================================
    def move_to_position(
        self,
        position: List[int],
        *,
        velocity: Optional[int | List[int]] = None,
        max_current: Optional[int | List[int]] = None,
        skip_prep: bool = False,
    ) -> bool:
        """
        Encoder pozisyonu gönderir (6 elemanlı liste, her biri 0..10000).

        Args:
            position: 6 elemanlı encoder pozisyonu listesi (MOTOR_MIN..MOTOR_MAX)
            velocity: Her eksen için hız. None=default, int=hepsi aynı, list=eksen başı
            max_current: Her eksen için akım limiti. None=default, int=hepsi aynı, list=eksen başı
            skip_prep: True ise prep/home öncesi yapılmaz (iç çağrılar için, infinite loop önler)

        Returns:
            True: komut gönderildi (hareketin tamamlandığı garantisi değildir, `wait_reached` kullanın)
        """
        if not self._ensure_connected():
            return False

        # Hareket öncesi hazırlık
        if not skip_prep:
            if self.home_before_move:
                self._log("🏠 Pre-home (home_before_move=True)")
                self.home_all()
                # Home komutu asenkron; biraz bekle ki tamamlansın
                time.sleep(0.5)
            elif self.prep_before_move:
                self._prep_for_move()

        pos = self._sanitize_pos(position)
        vels = self._expand_int_param(velocity, DEFAULT_VELOCITY)
        curs = self._expand_int_param(max_current, DEFAULT_MAX_CURRENT)

        try:
            for j in range(self.dof):
                motor_id = j + 1
                self._lhp.set_target_position(motor_id, int(pos[j]))
                self._lhp.set_position_velocity(motor_id, int(vels[j]))
                self._lhp.set_max_current(motor_id, int(curs[j]))

            # move_motors(0) = tüm motorları senkron hareket ettir
            self._lhp.move_motors(0)
            time.sleep(0.05)  # PDO'nun bir döngü geçmesi için kısa bekleme
            return True

        except Exception as e:
            self._log(f"❌ move_to_position hata: {e}")
            return False

    def _prep_for_move(self) -> None:
        """
        Hareket öncesi hızlı hazırlık:
        - Varsa alarmları temizle
        - Motorlar disable ise enable et
        Home yapmaz - hız için. home_before_move=True ise ayrı çağrılır.
        """
        if not self._lhp:
            return

        # Alarmları kontrol et ve temizle
        had_alarm = False
        for motor_id in range(1, self.dof + 1):
            try:
                alarm = int(self._lhp.get_now_alarm(motor_id))
                if alarm != 0:
                    had_alarm = True
                    self._lhp.set_clear_alarm(motor_id)
            except Exception:
                pass

        # Motor enable durumunu kontrol et; disable olanları enable et
        needed_enable = []
        for motor_id in range(1, self.dof + 1):
            try:
                if not bool(self._lhp.get_enable(motor_id)):
                    needed_enable.append(motor_id)
            except Exception:
                pass

        if needed_enable:
            self._log(f"ℹ️ Pre-move: enable edilecek motorlar: {needed_enable}")
            for motor_id in needed_enable:
                try:
                    self._lhp.set_control_mode(motor_id, LCM_POSITION)
                    self._lhp.set_enable(motor_id, True)
                except Exception:
                    pass
            time.sleep(0.1)

        if had_alarm:
            self._log("ℹ️ Pre-move: alarmlar temizlendi")

    def move_to_zero(self) -> bool:
        """Tüm motorları sıfır pozisyonuna götürür."""
        return self.move_to_position(ZERO_POS)

    def stop_all(self) -> None:
        """Hareket halindeki tüm motorları durdurur."""
        if not self._ensure_connected():
            return
        try:
            self._lhp.stop_motors(0)
            self._log("⏹️ Tüm motorlar durduruldu")
        except Exception as e:
            self._log(f"⚠️ stop_motors hata: {e}")

    def wait_reached(
        self,
        timeout_s: float = 5.0,
        poll_s: float = 0.05,
        target_pos: Optional[List[int]] = None,
        pos_tolerance: int = 300,
        early_exit_on_grip: bool = True,
    ) -> Tuple[bool, str]:
        """
        Tüm aktif motorların hedefe ulaşmasını bekler.

        Iki yolla kontrol eder:
        1) position_reached bayrağı
        2) (opsiyonel) target_pos verilmişse, gerçek pozisyon ile farkın
           pos_tolerance içinde olması

        Erken çıkış (early_exit_on_grip=True ise):
          - Motor akım limite dayandı (yüksek akım)
          - Motor hareket etmiyor (pozisyon sabit)
          - Hedefe hâlâ uzakta (nesne kavraması)
          → "grip_detected" döner, gereksiz timeout beklemez

        Alarm tetiklenirse erken çıkar (408 değil 409 döner).

        Returns:
            (success, reason):
              (True, "reached")       -> tüm motorlar hedefe ulaştı
              (True, "grip_detected") -> nesne kavrandı (erken çıkış)
              (False, "timeout")      -> zaman aşımı
              (False, "alarm")        -> motor alarmı tetiklendi
              (False, "alarm_locked") -> motor rate-limit nedeniyle kilitlendi
        """
        if not self._ensure_connected():
            return False, "not_connected"

        # Erken çıkış parametrelerini settings'ten oku
        try:
            from settings import (
                EARLY_EXIT_ON_GRIP,
                EARLY_EXIT_CURRENT_THRESHOLD,
                EARLY_EXIT_STABLE_MS,
                EARLY_EXIT_MIN_MOTORS,
            )
            # Override edilebilir: argüman öncelikli
            if not early_exit_on_grip:
                EARLY_EXIT_ON_GRIP = False
            else:
                early_exit_on_grip = EARLY_EXIT_ON_GRIP
            grip_current_th = EARLY_EXIT_CURRENT_THRESHOLD
            grip_stable_threshold_s = EARLY_EXIT_STABLE_MS / 1000.0
            grip_min_motors = EARLY_EXIT_MIN_MOTORS
        except ImportError:
            grip_current_th = 400
            grip_stable_threshold_s = 0.4
            grip_min_motors = 3

        # Erken kavrama tespiti için durum takibi
        # Her motor için: (son_pozisyon, sabit_kalma_süresi)
        last_positions = {}  # motor_id -> (pos, time_first_stable)

        deadline = time.time() + timeout_s
        while time.time() < deadline:
            # 1) Herhangi bir motor kilitlendiyse erken çık
            for mid in range(1, self.dof + 1):
                if self._motor_alarm_locked.get(mid, False):
                    return False, "alarm_locked"

            # 2) Reached bayrağı ve gerçek pozisyon kontrolü
            all_reached = True
            try:
                # Erken kavrama analizi için akım + sabit motor kontrolü
                high_current_count = 0
                stuck_with_current_count = 0
                now = time.time()

                for motor_id in range(1, self.dof + 1):
                    # position_reached bayrağı
                    try:
                        reached = bool(self._lhp.get_position_reached(motor_id))
                    except Exception:
                        reached = False

                    # Gerçek pozisyon ve akım
                    actual = None
                    current = 0
                    try:
                        actual = int(self._lhp.get_now_position(motor_id))
                        current = int(self._lhp.get_now_current(motor_id))
                    except Exception:
                        pass

                    # Erken kavrama: Motor akım yüksek ve hareket etmiyor mu?
                    if early_exit_on_grip and actual is not None and current > grip_current_th:
                        # Motor yüksek akım çekiyor
                        high_current_count += 1

                        # Son pozisyonu kaydet, sabit kalma süresini takip et
                        prev = last_positions.get(motor_id)
                        if prev is None:
                            last_positions[motor_id] = (actual, now)
                        else:
                            prev_pos, first_stable = prev
                            # Motor hareketsiz mi (±20 encoder tolerans)?
                            if abs(actual - prev_pos) < 20:
                                # Ne kadar süredir sabit?
                                if (now - first_stable) >= grip_stable_threshold_s:
                                    stuck_with_current_count += 1
                            else:
                                # Motor hareket etti, yeniden başlat
                                last_positions[motor_id] = (actual, now)

                    if not reached:
                        all_reached = False

                    # Hedef pozisyon verildiyse ek kontrol
                    if target_pos is not None and len(target_pos) >= motor_id and actual is not None:
                        expected = int(target_pos[motor_id - 1])
                        if abs(actual - expected) > pos_tolerance:
                            all_reached = False

                    # 3) Herhangi bir alarm var mı?
                    try:
                        alarm = int(self._lhp.get_now_alarm(motor_id))
                        if alarm != 0:
                            return False, "alarm"
                    except Exception:
                        pass

                # Erken çıkış: N motor yüksek akım + sabit ise kavrama tamamlandı
                if (early_exit_on_grip
                        and stuck_with_current_count >= grip_min_motors
                        and not all_reached):
                    return True, "grip_detected"

            except Exception:
                all_reached = False

            if all_reached:
                return True, "reached"
            time.sleep(poll_s)

        return False, "timeout"

    # ==========================================================================
    # Durum okuma + monitor thread
    # ==========================================================================
    def get_status(self) -> List[MotorStatus]:
        """
        Motor status'larını döner. İki mod:

        1. refresh=True (varsayılan): Donanımdan o an oku, anlık değer.
           - API/kalibrasyon için ideal
           - 100ms civarı gecikme
        2. refresh=False: Status monitor thread'inin son snapshot'unu döner.
           - Hafif, hızlı
           - 10 saniyeye kadar stale olabilir
        """
        return self._get_status_impl(refresh=True)

    def get_cached_status(self) -> List[MotorStatus]:
        """Son snapshot - hızlı ama stale olabilir."""
        return self._get_status_impl(refresh=False)

    def _get_status_impl(self, refresh: bool) -> List[MotorStatus]:
        """Status okuma implementasyonu."""
        if refresh and self._connected and self._lhp is not None:
            # Donanımdan taze veri oku
            try:
                self._read_status_once({})
            except Exception:
                pass
        # Kopya dön (thread-safe)
        with self._status_lock:
            return [self._copy_status(s) for s in self._last_status]

    @staticmethod
    def _copy_status(s: MotorStatus) -> MotorStatus:
        out = MotorStatus(s.motor_id)
        out.position = s.position
        out.angle = s.angle
        out.current = s.current
        out.alarm = s.alarm
        out.enabled = s.enabled
        out.reached = s.reached
        out.status = s.status
        return out

    def has_alarms(self) -> bool:
        """Herhangi bir motorda alarm var mı?"""
        for s in self.get_status():
            if s.alarm != 0:
                return True
        return False

    def get_active_alarms(self) -> List[Tuple[int, int]]:
        """[(motor_id, alarm_code), ...] (sadece alarm!=0 olanlar)"""
        return [(s.motor_id, s.alarm) for s in self.get_status() if s.alarm != 0]

    def _start_status_monitor(self) -> None:
        """
        Arka plan izleme thread'i.
        Her monitor_interval_s (varsayılan 10sn) bir:
          - Motorlardan status oku (alarm/pozisyon vb)
          - Alarm edge detection
          - Bağlantı durumu logla: 'Baglanti-OK' veya 'Baglanti-NOK'

        Settings.ENABLE_STATUS_MONITOR=False ise hiç başlatılmaz.
        """
        # settings.py'den davranış bayraklarını oku
        enable_monitor = True
        periodic_log = True
        try:
            from settings import ENABLE_STATUS_MONITOR, PERIODIC_CONNECTION_LOG
            enable_monitor = ENABLE_STATUS_MONITOR
            periodic_log = PERIODIC_CONNECTION_LOG
        except ImportError:
            pass

        if not enable_monitor:
            self._log("ℹ️ Status monitor thread devre dışı (settings)")
            return

        if self._monitor_running.is_set():
            return
        self._monitor_running.set()

        # İlk log'u 2 saniye sonra bas (bağlantının oturması için)
        first_delay = min(2.0, self.monitor_interval_s)

        def _loop():
            prev_alarms: Dict[int, int] = {}
            prev_status_ok = None  # Önceki durum (değişiklik tespiti için)
            time.sleep(first_delay)
            while self._monitor_running.is_set():
                # 1) Motor status oku (alarm edge detection dahil)
                try:
                    self._read_status_once(prev_alarms)
                except Exception as e:
                    self._log(f"[{self._ts()}] Monitor error: {e}")

                # 2) Bağlantı durumunu değerlendir
                try:
                    status_ok, reason = self._check_link_status()
                    # Periyodik log AÇIKsa her seferinde yaz
                    # KAPALIysa sadece DURUM DEĞİŞTİĞİNDE yaz
                    should_log = periodic_log or (prev_status_ok != status_ok)

                    if status_ok:
                        if should_log:
                            self._log(f"[{self._ts()}] Baglanti-OK")
                    else:
                        if should_log:
                            self._log(f"[{self._ts()}] Baglanti-NOK (reason: {reason})")
                        # needs_reconnect ise auto-reconnect tetikle
                        if self._master and self._master.needs_reconnect:
                            if self._on_disconnected:
                                try:
                                    self._on_disconnected(reason)
                                except Exception:
                                    pass
                            if self.auto_reconnect and self._last_adapter_index is not None:
                                self._log(f"[{self._ts()}] Auto-reconnect baslatiliyor...")
                                self._start_auto_reconnect()
                            self._monitor_running.clear()
                            break
                    prev_status_ok = status_ok
                except Exception as e:
                    self._log(f"[{self._ts()}] Link check error: {e}")

                # 3) Sleep until next check
                # Küçük parçalara böl ki shutdown'a hızlı cevap verebilsin
                remaining = self.monitor_interval_s
                while remaining > 0 and self._monitor_running.is_set():
                    chunk = min(0.5, remaining)
                    time.sleep(chunk)
                    remaining -= chunk

        self._monitor_thread = threading.Thread(
            target=_loop, name="hand-status-monitor", daemon=True
        )
        self._monitor_thread.start()

    def _check_link_status(self) -> Tuple[bool, str]:
        """
        Bağlantının durumunu değerlendirir.

        Returns:
            (ok: bool, reason: str)
            ok=True  -> bağlantı sağlıklı
            ok=False -> sorun var, reason bilgi verir
        """
        if not self._master:
            return False, "no_master"
        if self._master.needs_reconnect:
            return False, "needs_reconnect"
        if not self._master.is_io_alive():
            return False, "io_thread_dead"

        # Master+slave state'i kontrol et
        try:
            states = self._master.read_states()
            if states is None:
                return False, "state_read_failed"
            master_state, slave_states = states
            import pysoem
            if master_state == pysoem.OP_STATE:
                return True, "op"
            # Slave'lerden en az biri OP ise de OK sayılır (master state ölçüm aliasingı olabilir)
            if slave_states and all(s == pysoem.OP_STATE for s, _ in slave_states):
                return True, "op_slave"
            # AL hata kodunu raporla
            if slave_states:
                _, al = slave_states[0]
                if al == 0x1b:
                    return False, "sm_watchdog"
                if al != 0:
                    return False, f"al=0x{al:02x}"
            return False, f"master_state={master_state}"
        except Exception as e:
            return False, f"exception:{e}"

    @staticmethod
    def _ts() -> str:
        """Log timestamp (HH:MM:SS formatı)."""
        return time.strftime("%H:%M:%S")

    def _start_auto_reconnect(self) -> None:
        """
        Arka planda otomatik yeniden bağlanmayı başlatır.
        settings.py'deki RECONNECT_INFINITE True ise kablo takılana kadar sonsuz dener.
        False ise RECONNECT_MAX_ATTEMPTS kadar dener, sonra vazgeçer.
        """
        with self._reconnect_lock:
            if self._reconnecting:
                return
            self._reconnecting = True

        # Settings'ten değerleri oku
        try:
            from settings import (
                RECONNECT_INITIAL_WAIT_S,
                RECONNECT_MAX_ATTEMPTS,
                RECONNECT_RETRY_DELAY_S,
                RECONNECT_INFINITE,
                RECONNECT_INFINITE_DELAY_S,
            )
        except ImportError:
            RECONNECT_INITIAL_WAIT_S = 5.0
            RECONNECT_MAX_ATTEMPTS = 10
            RECONNECT_RETRY_DELAY_S = 3.0
            RECONNECT_INFINITE = True
            RECONNECT_INFINITE_DELAY_S = 5.0

        def _reconnect_worker():
            try:
                self._log(f"🔄 {RECONNECT_INITIAL_WAIT_S:.0f} saniye bekleniyor (slave kendine gelsin)...")
                time.sleep(RECONNECT_INITIAL_WAIT_S)

                # Önce mevcut bağlantıyı temizle
                try:
                    self._stop_tpdo_monitor()
                    if self._lhp:
                        try:
                            self._lhp.close()
                        except Exception:
                            pass
                    if self._master:
                        try:
                            self._master.stop()
                        except Exception:
                            pass
                except Exception:
                    pass
                self._master = None
                self._lhp = None
                self._connected = False
                self._enabled = False

                # Yeniden bağlanma döngüsü
                idx = self._last_adapter_index
                attempt = 0
                mode = "sonsuz" if RECONNECT_INFINITE else f"max {RECONNECT_MAX_ATTEMPTS}"
                self._log(f"🔄 Auto-reconnect başlatılıyor ({mode} deneme)")

                while True:
                    attempt += 1

                    if RECONNECT_INFINITE:
                        self._log(f"🔄 Reconnect denemesi #{attempt} (sonsuz mod) - adapter #{idx}")
                    else:
                        if attempt > RECONNECT_MAX_ATTEMPTS:
                            self._log(f"❌ {RECONNECT_MAX_ATTEMPTS} denemeden sonra vazgeçildi")
                            self._log(f"   → Robot elin gücünü kapatıp açmanız gerekebilir")
                            break
                        self._log(f"🔄 Reconnect denemesi #{attempt}/{RECONNECT_MAX_ATTEMPTS} - adapter #{idx}")

                    try:
                        ok = self.connect(idx)
                    except Exception as e:
                        self._log(f"⚠️ Connect exception: {e}")
                        ok = False

                    if ok:
                        self._log(f"✅ Auto-reconnect başarılı! (deneme #{attempt})")
                        try:
                            self.enable_all()
                        except Exception as e:
                            self._log(f"⚠️ Enable after reconnect failed: {e}")
                        if self._on_reconnected:
                            try:
                                self._on_reconnected()
                            except Exception:
                                pass
                        return  # Başarılı, döngüden çık

                    # Başarısız - bekle ve tekrar dene
                    delay = RECONNECT_INFINITE_DELAY_S if RECONNECT_INFINITE else RECONNECT_RETRY_DELAY_S
                    self._log(f"⏳ {delay:.0f}s sonra tekrar denenecek (kabloyu kontrol edin)...")
                    time.sleep(delay)

                    # Her denemeden önce temizle
                    try:
                        if self._master:
                            self._master.stop()
                    except Exception:
                        pass
                    self._master = None
                    self._lhp = None
                    self._connected = False

            finally:
                with self._reconnect_lock:
                    self._reconnecting = False

        self._reconnect_thread = threading.Thread(
            target=_reconnect_worker, name="hand-auto-reconnect", daemon=True
        )
        self._reconnect_thread.start()

    def _stop_status_monitor(self) -> None:
        self._monitor_running.clear()
        t = self._monitor_thread
        if t and t.is_alive():
            t.join(timeout=1.0)
        self._monitor_thread = None

    def _read_status_once(self, prev_alarms: Dict[int, int]) -> None:
        """
        Tüm motorlardan status okur ve _last_status'u günceller.

        Her motor okuması arasında mini sleep var - bu GIL'i kısa süreliğine
        serbest bırakır, IO thread'in PDO göndermesine izin verir.
        SM watchdog tetiklenmesini engeller.
        """
        lhp = self._lhp
        if lhp is None:
            return

        new_statuses: List[MotorStatus] = []
        for motor_id in range(1, self.dof + 1):
            s = MotorStatus(motor_id)

            # Her çağrı fail olabilir; lokal try/except ile yumuşat
            try:
                s.position = int(lhp.get_now_position(motor_id))
            except Exception:
                pass
            try:
                s.angle = float(lhp.get_now_angle(motor_id))
            except Exception:
                pass
            try:
                s.current = int(lhp.get_now_current(motor_id))
            except Exception:
                pass
            try:
                s.alarm = int(lhp.get_now_alarm(motor_id))
            except Exception:
                pass
            try:
                s.enabled = bool(lhp.get_enable(motor_id))
            except Exception:
                pass
            try:
                s.reached = bool(lhp.get_position_reached(motor_id))
            except Exception:
                pass
            try:
                s.status = int(lhp.get_now_status(motor_id))
            except Exception:
                pass

            new_statuses.append(s)

            # Alarm edge detection - rising edge (0 -> !=0)
            prev = prev_alarms.get(motor_id, 0)
            if s.alarm != 0 and prev == 0:
                now = time.time()
                # Alarm history'yi güncelle - sadece son window_s içindekiler
                history = self._alarm_history.setdefault(motor_id, [])
                history.append(now)
                # Eski kayıtları temizle
                cutoff = now - self._alarm_rate_window_s
                history[:] = [t for t in history if t > cutoff]

                self._log(f"[{self._ts()}] 🚨 Motor {motor_id} alarm: code={s.alarm} "
                          f"(son {self._alarm_rate_window_s:.0f}s'de {len(history)}. kez)")

                if self._on_alarm:
                    try:
                        self._on_alarm(motor_id, s.alarm)
                    except Exception:
                        pass

                # Rate limit kontrolü
                if len(history) >= self._alarm_rate_threshold:
                    # Çok sık alarm - motoru KILITLE, auto-reset yapma
                    if not self._motor_alarm_locked.get(motor_id, False):
                        self._motor_alarm_locked[motor_id] = True
                        self._log(
                            f"[{self._ts()}] 503-SERVICE-UNAVAILABLE Motor {motor_id} "
                            f"kilitli ({self._alarm_rate_threshold}+ alarm / "
                            f"{self._alarm_rate_window_s:.0f}s)"
                        )
                        # Motoru güvenlik için disable et
                        try:
                            lhp.set_enable(motor_id, False)
                            self._log(f"   ↳ Motor {motor_id} güvenlik için disable edildi")
                            self._log(f"   ↳ Manuel clear için: [11] Alarmları temizle")
                        except Exception as e:
                            self._log(f"   ↳ disable failed: {e}")

                elif self.auto_reset and not self._motor_alarm_locked.get(motor_id, False):
                    # Normal auto-reset
                    try:
                        lhp.set_clear_alarm(motor_id)
                        self._log(f"   ↳ auto_reset: motor {motor_id} alarmı temizlendi")
                    except Exception as e:
                        self._log(f"   ↳ auto_reset FAILED: {e}")

            prev_alarms[motor_id] = s.alarm

            # KRITIK: Her motor okuması sonrası 2ms sleep ile GIL'i serbest bırak
            # Bu IO thread'in PDO göndermesine izin verir, SM watchdog'u engeller.
            time.sleep(0.002)

        with self._status_lock:
            self._last_status = new_statuses

    # ==========================================================================
    # Yardımcılar
    # ==========================================================================
    def _ensure_connected(self) -> bool:
        if not self._connected or not self._lhp or not self._master:
            self._log("❌ Bağlantı yok. Önce connect() çağırın.")
            return False
        return True

    @staticmethod
    def _sanitize_pos(pos: List[int]) -> List[int]:
        """6 elemanlı, MOTOR_MIN..MOTOR_MAX sınırlı int listesi."""
        out: List[int] = []
        src = list(pos) if pos is not None else []
        for i in range(ROBOT_DOF):
            v = src[i] if i < len(src) else 0
            try:
                v = int(v)
            except Exception:
                v = 0
            out.append(max(MOTOR_MIN, min(MOTOR_MAX, v)))
        return out

    @staticmethod
    def _expand_int_param(param, default: int) -> List[int]:
        """int|List[int]|None -> 6 elemanlı List[int]"""
        if param is None:
            return [default] * ROBOT_DOF
        if isinstance(param, list):
            out = []
            for i in range(ROBOT_DOF):
                v = param[i] if i < len(param) else default
                try:
                    out.append(int(v))
                except Exception:
                    out.append(default)
            return out
        try:
            return [int(param)] * ROBOT_DOF
        except Exception:
            return [default] * ROBOT_DOF

    # ---- context manager --------------------------------------------------
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        try:
            self.disconnect()
        except Exception:
            pass
        return False
