"""
EtherCAT master wrapper (pysoem).

Fixes:
- IO loop resilient retry/backoff
- Detects OP drop and tries auto-recover to OP (with SAFEOP-ERR ACK flow)
- If recover fails repeatedly -> stops IO loop and sets needs_reconnect=True
- Thread-safe via lock

Notes:
- "Sync manager watchdog" usually means cyclic PDO exchange timing jitter/too slow.
  On Windows we use timeBeginPeriod(1) to get 1ms timer resolution.
- Process & IO thread priority elevated to minimize Windows scheduler jitter.
"""

from __future__ import annotations

import sys
import threading
import time
from typing import List, Optional, Tuple

import pysoem


# ---------------------------------------------------------------------------
# Windows timer hassasiyeti + Process priority
# ---------------------------------------------------------------------------
# 1) timeBeginPeriod(1): Windows timer resolution'u 15.6ms'den 1ms'ye düşürür.
#    time.sleep() ve thread scheduling jitter'ını azaltır.
# 2) HIGH_PRIORITY_CLASS: Process önceliğini yükseltir. Diğer uygulamaların
#    CPU kullanımı bu process'i bloke edemez.
#    NOT: REALTIME_PRIORITY_CLASS KULLANMIYORUZ - çünkü Windows'un kendi sistem
#    thread'lerini de bloke eder ve sistemi dondurur. HIGH yeterli olur.
# ---------------------------------------------------------------------------

# Windows sabit değerleri
HIGH_PRIORITY_CLASS = 0x00000080
THREAD_PRIORITY_TIME_CRITICAL = 15
THREAD_PRIORITY_HIGHEST = 2

_win_priority_applied = False


def _apply_windows_optimizations() -> None:
    """
    Windows'a özgü performans optimizasyonlarını uygular.
    Tek sefer çalışır, idempotent.
    """
    global _win_priority_applied
    if _win_priority_applied or sys.platform != "win32":
        return

    try:
        import ctypes
        import atexit

        # 1) Timer resolution -> 1ms
        try:
            _winmm = ctypes.WinDLL("winmm")
            _winmm.timeBeginPeriod(1)
            atexit.register(lambda: _winmm.timeEndPeriod(1))
            print("ℹ️ Windows timer resolution: 1ms")
        except Exception as e:
            print(f"⚠️ Timer resolution ayarlanamadı: {e}")

        # 2) Process priority -> HIGH
        try:
            kernel32 = ctypes.WinDLL("kernel32")
            hProcess = kernel32.GetCurrentProcess()
            ok = kernel32.SetPriorityClass(hProcess, HIGH_PRIORITY_CLASS)
            if ok:
                print("ℹ️ Process priority: HIGH")
            else:
                print("⚠️ Process priority değiştirilemedi (admin yetkisi gerekebilir)")
        except Exception as e:
            print(f"⚠️ Process priority ayarlanamadı: {e}")

        _win_priority_applied = True

    except Exception as e:
        print(f"⚠️ Windows optimizasyonları başarısız: {e}")


def _boost_current_thread_priority() -> bool:
    """
    Mevcut thread'in önceliğini TIME_CRITICAL seviyesine yükseltir.
    Sadece Windows'ta etkili. IO thread bunu kendi başında çağırmalı.

    Returns:
        True: başarılı, False: değişiklik yapılamadı
    """
    if sys.platform != "win32":
        return False

    try:
        import ctypes
        kernel32 = ctypes.WinDLL("kernel32")
        hThread = kernel32.GetCurrentThread()
        ok = kernel32.SetThreadPriority(hThread, THREAD_PRIORITY_TIME_CRITICAL)
        return bool(ok)
    except Exception:
        return False


# Modül yüklenince uygula
_apply_windows_optimizations()


class EthercatMaster:
    def __init__(self):
        self.master: Optional[pysoem.Master] = pysoem.Master()
        self.slaves = []
        self.input_size = 0
        self.output_size = 0
        self.ifname: Optional[str] = None

        # IO loop
        self.running = False
        self.thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()

        # health/debug
        self.last_tx_ts: float = 0.0
        self.last_rx_ts: float = 0.0
        self.io_fail_count: int = 0

        # important flags
        self.needs_reconnect: bool = False

        # Timings - settings.py'den oku
        try:
            from settings import (
                PDO_CYCLE_S, PDO_RX_TIMEOUT_US, VERBOSE_LOGGING,
            )
            self.cycle_s: float = PDO_CYCLE_S
            self.rx_timeout_us: int = PDO_RX_TIMEOUT_US
            self.verbose_logging: bool = VERBOSE_LOGGING
        except ImportError:
            # Settings bulunamadıysa varsayılan değerler
            self.cycle_s: float = 0.004          # 4ms
            self.rx_timeout_us: int = 4000       # 4ms receive timeout
            self.verbose_logging: bool = False

        # recover control
        self._recover_fail: int = 0
        self._last_state_check: float = 0.0
        self._last_recover_ts: float = 0.0

    # -------------------------
    # Helpers
    # -------------------------
    def scanNetworkInterfaces(self) -> List[str]:
        adapters = pysoem.find_adapters()
        if not adapters:
            print("⚠️ No adapters detected by pysoem (Npcap/permissions?)")
            return []
        print("🔍 Available adapters:")
        for i, a in enumerate(adapters):
            print(f"  [{i}] {a.desc}")
        return [a.name for a in adapters]

    def _state_name(self, st: int) -> str:
        if st == pysoem.INIT_STATE:
            return "INIT"
        if st == pysoem.PREOP_STATE:
            return "PREOP"
        if st == pysoem.SAFEOP_STATE:
            return "SAFEOP"
        if st == pysoem.OP_STATE:
            return "OP"
        # pysoem bazen error bitleri ile gelir; ham değeri yazdır
        return f"UNKNOWN({st})"

    def _print_slave_states(self) -> None:
        if not self.master:
            return
        try:
            self.master.read_state()
        except Exception as e:
            print(f"⚠️ read_state failed: {e}")
            return

        for i, s in enumerate(self.slaves):
            try:
                al_txt = pysoem.al_status_code_to_string(s.al_status)
            except Exception:
                al_txt = "(unknown)"
            print(
                f"  Slave {i} ({s.name}): State={self._state_name(s.state)}, "
                f"AL={hex(s.al_status)} {al_txt}"
            )

    def is_io_alive(self) -> bool:
        return bool(self.running and self.thread and self.thread.is_alive())

    def get_master_state(self) -> Optional[int]:
        if not self.master:
            return None
        try:
            with self._lock:
                self.master.read_state()
                return self.master.state
        except Exception:
            return None

    def read_states(self) -> Optional[Tuple[int, List[Tuple[int, int]]]]:
        if not self.master:
            return None
        try:
            with self._lock:
                self.master.read_state()
                ms = self.master.state
                ss = [(s.state, s.al_status) for s in self.slaves]
            return ms, ss
        except Exception:
            return None

    # -------------------------
    # Lifecycle
    # -------------------------
    def init(self, channel_index: int, ifaces: List[str]) -> bool:
        """
        EtherCAT master'ı başlat. Otomatik retry ile bozuk slave state'leri kurtarır.
        """
        if not ifaces:
            print("❌ No interfaces provided.")
            return False
        if channel_index < 0 or channel_index >= len(ifaces):
            print(f"❌ Invalid channel_index={channel_index}.")
            return False

        # Retry sayısı ve delay'i settings'ten oku
        try:
            from settings import CONNECT_MAX_ATTEMPTS, CONNECT_RETRY_DELAY_S
        except ImportError:
            CONNECT_MAX_ATTEMPTS = 3
            CONNECT_RETRY_DELAY_S = 0.8

        for attempt in range(1, CONNECT_MAX_ATTEMPTS + 1):
            if attempt > 1:
                print(f"\n🔄 Bağlantı denemesi #{attempt}/{CONNECT_MAX_ATTEMPTS}...")
                # Bozuk state'i temizle: master'ı kapat, yeni instance aç
                self._hard_cleanup()
                time.sleep(CONNECT_RETRY_DELAY_S)  # Slave'in kendine gelmesi için

            ok = self._init_once(channel_index, ifaces)
            if ok:
                if attempt > 1:
                    print(f"✅ Bağlantı #{attempt}. denemede kuruldu")
                return True

            print(f"❌ Deneme #{attempt} başarısız")

        print(f"\n❌ {CONNECT_MAX_ATTEMPTS} denemenin tamamı başarısız")
        print(f"{'=' * 60}")
        print("Manuel müdahale önerisi:")
        print("  1. Robot elin gücünü kesip tekrar verin")
        print("  2. 15 saniye bekleyin")
        print("  3. Yeniden bağlanmayı deneyin")
        print(f"{'=' * 60}")
        return False

    def _hard_cleanup(self) -> None:
        """
        Bozuk bağlantı sonrası agresif temizlik:
        - Mevcut master'ı kapat
        - Yeni master instance'ı hazırla
        Bu slave'in bir sonraki open() çağrısında kendini
        yeniden tanıtmasına izin verir.
        """
        if self.master:
            try:
                with self._lock:
                    try:
                        for s in self.slaves:
                            try:
                                s.state = pysoem.INIT_STATE
                            except Exception:
                                pass
                        self.master.state = pysoem.INIT_STATE
                        self.master.write_state()
                    except Exception:
                        pass
                time.sleep(0.1)
                try:
                    self.master.close()
                except Exception:
                    pass
            except Exception:
                pass

        self.master = None
        self.slaves = []

    def _init_once(self, channel_index: int, ifaces: List[str]) -> bool:
        """Tek bir bağlantı denemesi. init() tarafından retry ile çağrılır."""
        # reset flags
        self.needs_reconnect = False
        self._recover_fail = 0
        self.io_fail_count = 0

        # ensure fully stopped
        try:
            self.stop()
        except Exception:
            pass

        self.master = pysoem.Master()

        try:
            raw_ifname = ifaces[channel_index]

            # pysoem'in yeni sürümleri master.open() için str bekliyor.
            # find_adapters() bazen bytes döndürür; güvenlik için normalize ediyoruz.
            if isinstance(raw_ifname, bytes):
                self.ifname = raw_ifname.decode("utf-8", errors="ignore")
            else:
                self.ifname = str(raw_ifname)

            print(f"🔌 Opening EtherCAT master on: {self.ifname}")
            self.master.open(self.ifname)

            if self.master.config_init() <= 0:
                print("❌ No EtherCAT slaves found.")
                return False

            self.slaves = self.master.slaves
            print(f"✅ Found {len(self.slaves)} slaves")
            for i, s in enumerate(self.slaves):
                print(f"  Slave {i}: {s.name} (Vendor {hex(s.man)}, Product {hex(s.id)})")

            # ÖNEMLİ: Slave'in geçerli kimliğini doğrula
            # Vendor=0 or Product=0 -> slave bozuk state, retry gerekli
            if any(s.man == 0 or s.id == 0 for s in self.slaves):
                print("⚠️ Slave kimliği geçersiz (Vendor=0 veya Product=0)")
                print("   → Slave bozuk state'te, tam reset gerekli")
                return False

            # Temiz başlangıç: önceki başarısız denemelerden kalan bozuk state'leri
            # temizlemek için önce INIT'e düş, ardından mailbox kurulumunu tetikle.
            # Bu "Invalid mailbox configuration" (AL=0x16) hatasının önüne geçer.
            try:
                with self._lock:
                    for s in self.slaves:
                        s.state = pysoem.INIT_STATE
                    self.master.state = pysoem.INIT_STATE
                    self.master.write_state()
                time.sleep(0.1)

                # Şimdi PREOP'a çıkar - mailbox init burada olur
                with self._lock:
                    for s in self.slaves:
                        s.state = pysoem.PREOP_STATE
                    self.master.state = pysoem.PREOP_STATE
                    self.master.write_state()
                time.sleep(0.2)
                # PREOP'a ulaştığını doğrula (sabırlı)
                self.master.state_check(pysoem.PREOP_STATE, 50_000)
                print("✅ Slaves reset to PREOP")
            except Exception as e:
                print(f"⚠️ INIT/PREOP reset skipped: {e}")

            self.master.config_map()
            print("✅ PDO map done")

            try:
                self.master.config_dc()
                print("✅ DC config done")
            except Exception as e:
                print(f"⚠️ DC config skipped/failed: {e}")

            # SAFEOP geçişi - yavaş slave'ler için sabırlı ol
            print("⏳ Waiting SAFEOP...")
            safeop_reached = False
            for attempt in range(3):  # 3 kez 50ms timeout ile dene
                # Dışarıdan PDO akışını besleyip slave'i canlı tut
                try:
                    for _ in range(5):
                        with self._lock:
                            self.master.send_processdata()
                            self.master.receive_processdata(self.rx_timeout_us)
                        time.sleep(0.01)
                except Exception:
                    pass

                # state_check çağır, döndüğünde state'i yeniden oku
                rc = self.master.state_check(pysoem.SAFEOP_STATE, 50_000)

                # state_check'in dönüş değerine güvenmeyip gerçek state'i oku
                with self._lock:
                    self.master.read_state()
                    master_state = self.master.state
                    slave_states = [s.state for s in self.slaves]

                all_safeop_or_op = all(
                    s == pysoem.SAFEOP_STATE or s == pysoem.OP_STATE
                    for s in slave_states
                )

                if rc == pysoem.SAFEOP_STATE or all_safeop_or_op:
                    safeop_reached = True
                    print(f"✅ SAFEOP reached (attempt {attempt+1}, rc={rc})")
                    break

                print(f"   ↳ attempt {attempt+1}: rc={rc}, master={self._state_name(master_state)}, "
                      f"slaves={[self._state_name(s) for s in slave_states]}")

            if not safeop_reached:
                self._print_slave_states()
                print("❌ Failed to reach SAFEOP")
                return False

            # one cycle
            with self._lock:
                self.master.send_processdata()
                self.last_tx_ts = time.time()
                self.master.receive_processdata(self.rx_timeout_us)
                self.last_rx_ts = time.time()

            # request OP
            if not self._force_to_op():
                self._print_slave_states()
                print("❌ Failed to reach OP")
                return False

            print("✅ Master in OP state")

            self.input_size = sum(len(s.input) for s in self.slaves)
            self.output_size = sum(len(s.output) for s in self.slaves)
            print(f"📊 Total input bytes : {self.input_size}")
            print(f"📊 Total output bytes: {self.output_size}")

            # init outputs
            for s in self.slaves:
                s.output = bytes(len(s.output))

            return True

        except Exception as e:
            print(f"❌ init() failed: {e}")
            try:
                self.stop()
            except Exception:
                pass
            return False

    def start(self) -> bool:
        return True

    def run(self) -> None:
        if self.running:
            return
        if not self.master:
            raise RuntimeError("EtherCAT master is not initialized")

        self.running = True
        self.thread = threading.Thread(target=self._process_io, daemon=True)
        self.thread.start()

    def stop(self) -> None:
        self.running = False

        if self.thread and self.thread.is_alive():
            self.thread.join(timeout=2.0)
        self.thread = None

        if self.master:
            try:
                # 1) Önce slave'leri INIT'e zorla - bu çok kritik
                # Yoksa slave bozuk state'te kalır ve sonraki bağlantı başarısız olur
                with self._lock:
                    try:
                        for s in self.slaves:
                            try:
                                s.state = pysoem.INIT_STATE
                            except Exception:
                                pass
                        self.master.state = pysoem.INIT_STATE
                        self.master.write_state()
                    except Exception:
                        pass
                time.sleep(0.1)

                # 2) INIT state'e ulaştığını doğrula (en fazla 500ms bekle)
                try:
                    self.master.state_check(pysoem.INIT_STATE, 500_000)
                except Exception:
                    pass

            finally:
                try:
                    self.master.close()
                except Exception:
                    pass

    # -------------------------
    # OP force / recover
    # -------------------------
    def _force_to_op(self) -> bool:
        """Try to move master to OP."""
        if not self.master:
            return False

        try:
            with self._lock:
                self.master.state = pysoem.OP_STATE
                self.master.write_state()

            for _ in range(40):
                with self._lock:
                    self.master.send_processdata()
                    self.last_tx_ts = time.time()
                    self.master.receive_processdata(self.rx_timeout_us)
                    self.last_rx_ts = time.time()

                if self.master.state_check(pysoem.OP_STATE, 5_000) == pysoem.OP_STATE:
                    return True
                time.sleep(0.05)

            return self.master.state_check(pysoem.OP_STATE, 5_000) == pysoem.OP_STATE

        except Exception as e:
            print(f"⚠️ _force_to_op exception: {e}")
            return False

    def _ack_safeop_error(self) -> None:
        """
        Try to ACK SAFEOP+ERROR for slaves (classic SOEM recovery step).
        Some slaves require SAFEOP+ACK before they accept OP again.
        """
        if not self.master:
            return
        try:
            with self._lock:
                # read current states first
                self.master.read_state()
                for s in self.slaves:
                    # pysoem exposes constants; STATE_ACK may not exist on all builds
                    # We'll try common bit OR if available.
                    try:
                        ack = pysoem.STATE_ACK  # type: ignore[attr-defined]
                        s.state = pysoem.SAFEOP_STATE | ack
                    except Exception:
                        # fallback: just request SAFEOP
                        s.state = pysoem.SAFEOP_STATE
                self.master.write_state()
        except Exception as e:
            print(f"⚠️ ACK SAFEOP-ERR failed: {e}")

    def _recover_op(self) -> bool:
        """
        If master dropped from OP, try to recover:
        - First verify: is state really bad or was it a measurement glitch?
        - If truly bad, try the full recovery sequence:
          INIT -> PREOP -> SAFEOP -> OP (for SM watchdog errors like AL=0x1b)
        """
        if not self.master:
            return False

        now = time.time()
        if now - self._last_recover_ts < 1.0:
            return False
        self._last_recover_ts = now

        # Önce gerçekten kötü mü diye tekrar oku - belki bir ölçüm glitch'iydi
        # Birkaç PDO döngüsü çalıştırıp tekrar bak
        try:
            for _ in range(5):
                with self._lock:
                    self.master.send_processdata()
                    self.last_tx_ts = time.time()
                    self.master.receive_processdata(self.rx_timeout_us)
                    self.last_rx_ts = time.time()
                time.sleep(self.cycle_s)

            with self._lock:
                self.master.read_state()
                ms = self.master.state
                slave_state = self.slaves[0].state if self.slaves else 0

            # Yeniden ölçtük, hâlâ OP mu?
            if ms == pysoem.OP_STATE or slave_state == pysoem.OP_STATE:
                # False alarm, bağlantı hâlâ iyi
                return True

        except Exception:
            pass

        # AL status code'u oku - SM watchdog (0x1b) ise INIT'ten başlamak lazım
        sm_watchdog = False
        try:
            with self._lock:
                for s in self.slaves:
                    if s.al_status == 0x1b:
                        sm_watchdog = True
                        break
        except Exception:
            pass

        # Gerçekten OP dışı, şimdi kurtarma deneyelim
        print("🟠 Recover: trying to get back to OP...")

        # SM watchdog hatasında INIT'ten başla (tam zincir)
        if sm_watchdog:
            print("   ↳ SM watchdog detected, doing full INIT->OP cycle")
            try:
                # 1) INIT'e düş
                with self._lock:
                    for s in self.slaves:
                        s.state = pysoem.INIT_STATE
                    self.master.state = pysoem.INIT_STATE
                    self.master.write_state()
                time.sleep(0.2)

                # 2) PREOP'a çıkar (mailbox yeniden kurulur)
                with self._lock:
                    for s in self.slaves:
                        s.state = pysoem.PREOP_STATE
                    self.master.state = pysoem.PREOP_STATE
                    self.master.write_state()
                time.sleep(0.3)
                self.master.state_check(pysoem.PREOP_STATE, 50_000)

                # 3) PDO map yeniden
                try:
                    self.master.config_map()
                except Exception:
                    pass

                # 4) SAFEOP'a geç + birkaç PDO döngüsü
                with self._lock:
                    for s in self.slaves:
                        s.state = pysoem.SAFEOP_STATE
                    self.master.state = pysoem.SAFEOP_STATE
                    self.master.write_state()
                time.sleep(0.2)

                for _ in range(15):
                    with self._lock:
                        self.master.send_processdata()
                        self.last_tx_ts = time.time()
                        self.master.receive_processdata(self.rx_timeout_us)
                        self.last_rx_ts = time.time()
                    time.sleep(self.cycle_s)

            except Exception as e:
                print(f"⚠️ Recover INIT sequence failed: {e}")
        else:
            # 0) ACK SAFEOP-ERR (klasik yol)
            self._ack_safeop_error()
            time.sleep(0.1)

            # 1) request SAFEOP explicitly
            try:
                with self._lock:
                    self.master.state = pysoem.SAFEOP_STATE
                    self.master.write_state()
                time.sleep(0.2)
            except Exception as e:
                print(f"⚠️ Recover: SAFEOP request failed: {e}")

            # a few cycles in SAFEOP
            try:
                for _ in range(10):
                    with self._lock:
                        self.master.send_processdata()
                        self.last_tx_ts = time.time()
                        self.master.receive_processdata(self.rx_timeout_us)
                        self.last_rx_ts = time.time()
                    time.sleep(self.cycle_s)
            except Exception:
                pass

        # Finalize: try OP
        if self._force_to_op():
            print("✅ Recover: OP restored")
            return True

        print("❌ Recover: failed to restore OP")
        self._print_slave_states()
        return False

    # -------------------------
    # IO loop
    # -------------------------
    def _process_io(self) -> None:
        if not self.master:
            self.running = False
            return

        # KRITIK: IO thread önceliğini TIME_CRITICAL seviyesine yükselt
        # Bu thread'in Windows scheduler tarafından kesintiye uğraması minimum olur.
        # EtherCAT SM watchdog (~100ms) ile yarışıyoruz.
        boosted = _boost_current_thread_priority()
        if boosted:
            print("ℹ️ IO thread priority: TIME_CRITICAL")

        fail = 0
        self._last_state_check = 0.0
        consecutive_bad_state = 0   # Arka arkaya kaç kez OP dışında gördük
        BAD_STATE_THRESHOLD = 5     # Recover tetiklemek için kaç tur gerekli (5s)

        while self.running:
            try:
                with self._lock:
                    self.master.send_processdata()
                    self.last_tx_ts = time.time()
                    self.master.receive_processdata(self.rx_timeout_us)
                    self.last_rx_ts = time.time()

                fail = 0
                self.io_fail_count = 0

                # state check (1.0s)
                now = time.time()
                if now - self._last_state_check > 1.0:
                    self._last_state_check = now
                    try:
                        with self._lock:
                            self.master.read_state()
                            ms = self.master.state
                            # Ayrıca slave state'ini de oku - master state geçici 0 dönebilir
                            slave_state = (
                                self.slaves[0].state if self.slaves else 0
                            )

                        # OP kontrolü: hem master hem slave OP olmalı
                        # ms == 0 genelde "henüz okuma tam oturmamış" demek, tolere et
                        is_ok = (
                            ms == pysoem.OP_STATE
                            or (ms == 0 and slave_state == pysoem.OP_STATE)
                        )

                        if not is_ok:
                            consecutive_bad_state += 1
                            # Sadece verbose mod açıkken bu uyarı basılır
                            # Normal durumda bu mesaj ölçüm artefaktı - gerçek
                            # sorun oluşursa BAD_STATE_THRESHOLD'da recover tetiklenir
                            if self.verbose_logging and consecutive_bad_state >= 2 and (
                                consecutive_bad_state == 2
                                or consecutive_bad_state % 5 == 0
                            ):
                                print(
                                    f"⚠️ Master state not OP: "
                                    f"master={self._state_name(ms)} "
                                    f"slave={self._state_name(slave_state)} "
                                    f"(consecutive={consecutive_bad_state})"
                                )
                            # Sadece gerçekten sürekli kötüyse recover tetikle
                            if consecutive_bad_state >= BAD_STATE_THRESHOLD:
                                print(f"⚠️ State {BAD_STATE_THRESHOLD}s boyunca OP değil, recover deneniyor")
                                # Recover tetiklerken lock serbest bırakıldı,
                                # recover bittiğinde counter'ı sıfırla
                                consecutive_bad_state = 0
                                ok = self._recover_op()
                                if not ok:
                                    self._recover_fail += 1
                                    if self._recover_fail >= 3:
                                        print("❌ Too many recover failures -> needs_reconnect=True")
                                        self.needs_reconnect = True
                                        self.running = False
                                        break
                                else:
                                    self._recover_fail = 0
                                    consecutive_bad_state = 0
                        else:
                            # OK'siz geçirdiğimiz süreyi sıfırla
                            consecutive_bad_state = 0

                    except Exception as e:
                        print(f"⚠️ State check failed: {e}")

                time.sleep(self.cycle_s)

            except Exception as e:
                fail += 1
                self.io_fail_count = fail
                print(f"❌ EtherCAT IO exception ({fail}): {e}")
                time.sleep(0.1)

                if fail >= 30:
                    print("❌ IO loop stopped (too many consecutive errors)")
                    self.needs_reconnect = True
                    self.running = False
                    break

    # -------------------------
    # Process data access
    # -------------------------
    def setOutputs(self, data: bytes, size: int) -> bool:
        if not self.master or not self.slaves:
            return False

        if len(data) != self.output_size:
            print(f"❌ Output length mismatch: expected {self.output_size}, got {len(data)}")
            return False

        with self._lock:
            offset = 0
            for s in self.slaves:
                n = len(s.output)
                s.output = data[offset: offset + n]
                offset += n
        return True

    def getInputs(self, size: int) -> Optional[bytes]:
        if not self.master or not self.slaves:
            return None

        if size != self.input_size:
            print(f"❌ Input length mismatch: expected {self.input_size}, got {size}")
            return None

        with self._lock:
            buf = bytearray()
            for s in self.slaves:
                buf.extend(s.input[: len(s.input)])
        return bytes(buf)

    def getInputSize(self) -> int:
        return self.input_size

    def getOutputSize(self) -> int:
        return self.output_size
