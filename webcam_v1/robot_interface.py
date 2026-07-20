# -*- coding: utf-8 -*-
"""
robot_interface.py — Kameradan gelen parmak kıvrımlarını LHandPro robot elin
motor pozisyonlarına çevirir, komut gönderir ve kuvvet sensörlerini okur.

MOCK_ROBOT=True iken gerçek donanım/SDK gerekmez; sahte robot ile vision
tarafını test edebilirsin (sahte kuvvet üretir).
"""
import os
import sys
import types
import time
from typing import Dict, List

import settings as config

# 6-DOF motor sırası (config.JOINT_RANGES anahtarları ile aynı sıra)
JOINT_ORDER = ["thumb_abduction", "thumb_flexion", "index", "middle", "ring", "pinky"]
FINGERS = ["thumb", "index", "middle", "ring", "pinky"]


def _map_to_position(value: float, joint: str) -> int:
    lo, hi = config.JOINT_RANGES[joint]
    return int(round(lo + value * (hi - lo)))


def _current_for(joint: str) -> int:
    """Parmak başına akım (kuvvet/tork) limiti; tanımsızsa global MAX_CURRENT."""
    return int(config.PER_JOINT_MAX_CURRENT.get(joint, config.MAX_CURRENT))


def compute_targets(curls: Dict[str, float], thumb_abduction: float) -> List[int]:
    """Kıvrımlardan JOINT_ORDER sırasına göre hedef pozisyonları hesaplar (göndermeden)."""
    vals = {
        "thumb_abduction": thumb_abduction,
        "thumb_flexion": curls.get("thumb", 0.0),
        "index": curls.get("index", 0.0),
        "middle": curls.get("middle", 0.0),
        "ring": curls.get("ring", 0.0),
        "pinky": curls.get("pinky", 0.0),
    }
    return [_map_to_position(vals[j], j) for j in JOINT_ORDER]


def _inject_vendor_config():
    """
    Vendor'ın lhandpro_controller.py'si `from config import ...` yapıyor,
    ama SDK'da config.py GELMİYOR. Burada settings.py'deki değerlerden
    çalışma anında bir 'config' modülü üretip sys.modules'e koyuyoruz.
    Böylece kullanıcı ikinci bir dosya oluşturmak zorunda kalmıyor.
    """
    vc = types.ModuleType("config")
    vc.CURRENT_HAND_TYPE = config.HAND_TYPE
    vc.CANFD_NODE_ID = config.CANFD_NODE_ID
    vc.RS485_PORT_NAME = config.RS485_PORT_NAME
    vc.RS485_BAUD_RATE = config.RS485_BAUD_RATE
    vc.RS485_NODE_ID = config.RS485_NODE_ID
    vc.ENABLE_HOME_CHECK = config.ENABLE_HOME_CHECK
    vc.ENABLE_TORQUE_CONTROL = config.ENABLE_TORQUE_CONTROL
    sys.modules["config"] = vc


class BaseHand:
    """Ortak arayüz."""
    def connect(self) -> bool: ...
    def disconnect(self): ...
    def send(self, curls: Dict[str, float], thumb_abduction: float): ...
    def read_forces(self) -> Dict[str, float]: ...
    def get_info(self) -> Dict[str, str]: ...
    def last_targets(self) -> List[int]: ...
    def prepare(self): ...
    def self_check(self) -> List[dict]: return []
    def home(self): ...


class MockHand(BaseHand):
    """Donanımsız test için sahte robot. Kıvrıma göre sahte kuvvet üretir."""
    def __init__(self):
        self.last_positions: List[int] = [0] * 6
        self._last_curls: Dict[str, float] = {f: 0.0 for f in FINGERS}
        self.motor_status: List[dict] = []

    def connect(self) -> bool:
        print("[MOCK] Sahte robot bağlandı (gerçek donanım YOK).")
        self.prepare()
        self.motor_status = self.self_check()
        return True

    def prepare(self):
        print("[MOCK][HAZIRLIK] alarm temizlendi -> enable -> home -> kuvvet ayarlandı.")

    def self_check(self):
        rows = []
        for i, j in enumerate(JOINT_ORDER):
            rows.append({"id": i + 1, "name": j, "enabled": True,
                         "alarm": 0, "angle": 0.0, "current": 0})
        return rows

    def home(self):
        print("[MOCK] home yapıldı (sahte).")

    def disconnect(self):
        print("[MOCK] Sahte robot kapatıldı.")

    def send(self, curls: Dict[str, float], thumb_abduction: float):
        vals = {
            "thumb_abduction": thumb_abduction,
            "thumb_flexion": curls["thumb"],
            "index": curls["index"],
            "middle": curls["middle"],
            "ring": curls["ring"],
            "pinky": curls["pinky"],
        }
        self.last_positions = [_map_to_position(vals[j], j) for j in JOINT_ORDER]
        self._last_curls = dict(curls)

    def read_forces(self) -> Dict[str, float]:
        # Parmak ne kadar kapalıysa o kadar "temas" varmış gibi sahte kuvvet.
        out = {}
        for f in FINGERS:
            c = self._last_curls.get(f, 0.0)
            out[f] = max(0.0, (c - 0.6)) * config.FORCE_DISPLAY_MAX * 1.5 if c > 0.6 else 0.0
        return out

    def get_info(self) -> Dict[str, str]:
        en = sum(1 for m in self.motor_status if m["enabled"])
        return {
            "mode": "MOCK (sahte robot)",
            "comm": config.COMM_MODE,
            "dof_total": "6",
            "dof_active": "6",
            "firmware": "N/A (mock)",
            "slaves": "N/A",
            "enabled": f"{en}/{len(self.motor_status) or 6}",
        }

    def last_targets(self) -> List[int]:
        return self.last_positions


class RealHand(BaseHand):
    """Gerçek LHandPro eli — vendor SDK'sını kullanır."""
    def __init__(self):
        self.controller = None
        self.lhp = None
        self.last_positions: List[int] = [0] * 6
        self._last_sent: Dict[int, int] = {}   # motor_id -> son gönderilen pozisyon
        self.dof_total = 0
        self.dof_active = 0
        self.firmware = "?"
        self.motor_status: List[dict] = []

    def connect(self) -> bool:
        sdk_dir = config.SDK_PYTHON_DIR
        if not os.path.isdir(sdk_dir):
            raise FileNotFoundError(
                f"SDK klasörü bulunamadı: {sdk_dir}\n"
                "Uygulama içinde 'sdk/' klasörü oluşturup EtherCAT_python dosyalarını "
                "(lhandprolib_loader.py, lhandprolib_wrapper.py, lhandpro_controller.py, "
                "ethercat_master.py) ve gerekli DLL'leri oraya koy."
            )
        if sdk_dir not in sys.path:
            sys.path.insert(0, sdk_dir)

        # SDK'da eksik olan 'config' modülünü settings.py'den üret
        _inject_vendor_config()

        # Vendor modülleri (çıkardığın SDK içinden)
        from lhandpro_controller import LHandProController  # noqa: E402

        self.controller = LHandProController(communication_mode=config.COMM_MODE)

        # Moda göre bağlanma parametreleri
        common = dict(
            enable_motors=config.ENABLE_MOTORS,
            home_motors=config.HOME_MOTORS,
            home_wait_time=config.HOME_WAIT_TIME,
        )
        if config.COMM_MODE == "ECAT":
            # EtherCAT: device_index = ağ kartı (NIC) indeksi. None => otomatik.
            ok = self.controller.connect(
                device_index=config.ECAT_NIC_INDEX,
                auto_select=True,
                **common,
            )
        elif config.COMM_MODE == "RS485":
            ok = self.controller.connect(
                rs485_port_name=config.RS485_PORT_NAME,
                rs485_baud_rate=config.RS485_BAUD_RATE,
                rs485_node_id=config.RS485_NODE_ID,
                **common,
            )
        else:  # CANFD
            ok = self.controller.connect(**common)

        if not ok:
            return False
        self.lhp = self.controller.lhp

        dof_total, dof_active = self.controller.get_dof()
        self.dof_total, self.dof_active = dof_total, dof_active
        print(f"[REAL] Bağlandı. DOF total={dof_total}, active={dof_active}")
        if dof_active < 6:
            print(f"[UYARI] Aktif DOF {dof_active}; kod 6-DOF varsayıyor. "
                  "16-DOF el için JOINT_ORDER/haritayı güncellemen gerekir.")

        # Firmware sürümü — Python katmanında açık değil; ham ctypes ile çağırıyoruz.
        self.firmware = self._read_firmware_version()

        if config.USE_FORCE_SENSORS:
            try:
                self.lhp.set_sensor_enable(True)
                print("[REAL] Kuvvet sensörleri etkin.")
            except Exception as e:
                print(f"[UYARI] Sensör etkinleştirilemedi: {e}")

        # Hazırlık + self-test (home + enable + kuvvet + durum raporu)
        if config.PREPARE_ON_CONNECT:
            self.prepare()
        self.motor_status = self.self_check()
        all_ok = all(m["enabled"] and m["alarm"] == 0 for m in self.motor_status)
        return True if not config.REQUIRE_ALL_ENABLED else all_ok

    def prepare(self):
        """Robot eli çalışmaya hazırla: alarm temizle -> enable -> home -> tork/kuvvet."""
        n = max(self.dof_active, 6)
        print("[HAZIRLIK] Alarm temizleniyor...")
        try:
            self.controller.clear_alarm()
        except Exception as e:
            print(f"  alarm temizleme atlandı: {e}")

        print("[HAZIRLIK] Motorlar enable ediliyor...")
        try:
            self.controller.enable_motors(True)
        except Exception as e:
            print(f"  enable hatası: {e}")

        print(f"[HAZIRLIK] Home (sıfırlama) yapılıyor... ({config.HOME_WAIT_TIME}s)")
        try:
            self.controller.home(config.HOME_WAIT_TIME)
        except Exception as e:
            print(f"  home hatası: {e}")

        # Kuvvet/tork limitini her motora uygula (rest'te de aktif olsun)
        print(f"[HAZIRLIK] Kuvvet (akım) limiti ayarlanıyor: {config.MAX_CURRENT}")
        for i in range(n):
            mid = i + 1
            try:
                self.lhp.set_max_current(mid, _current_for(JOINT_ORDER[i] if i < len(JOINT_ORDER) else "index"))
            except Exception:
                pass
        self._last_sent.clear()   # home sonrası motorlar 0'da; hareketle yeniden gönderilecek

    def self_check(self):
        """Her motorun enable/alarm/açı/akım durumunu oku ve döndür."""
        n = max(self.dof_active, 6)
        rows = []
        for i in range(n):
            mid = i + 1
            row = {"id": mid, "name": JOINT_ORDER[i] if i < len(JOINT_ORDER) else f"m{mid}",
                   "enabled": False, "alarm": -1, "angle": 0.0, "current": 0}
            try:
                row["enabled"] = bool(self.lhp.get_enable(mid))
            except Exception:
                pass
            try:
                row["alarm"] = int(self.lhp.get_now_alarm(mid))
            except Exception:
                pass
            try:
                row["angle"] = float(self.lhp.get_now_angle(mid))
            except Exception:
                pass
            try:
                row["current"] = int(self.lhp.get_now_current(mid))
            except Exception:
                pass
            rows.append(row)
        return rows

    def home(self):
        """Dışarıdan (tuşla) yeniden home çağrısı."""
        try:
            self.controller.home(config.HOME_WAIT_TIME)
            self._last_sent.clear()   # motorlar 0'a döndü; sonraki hareket yeniden gönderilsin
            self.last_positions = [0] * 6
            self.motor_status = self.self_check()
        except Exception as e:
            print(f"home hatası: {e}")

    def _read_firmware_version(self) -> str:
        """lhandprolib_get_firmware_version(handle, float*) — ham ctypes çağrısı."""
        try:
            from ctypes import c_int, c_void_p, c_float, POINTER, byref
            lib = self.lhp._lib
            fn = lib.lhandprolib_get_firmware_version
            fn.restype = c_int
            fn.argtypes = [c_void_p, POINTER(c_float)]
            ver = c_float()
            rc = fn(self.lhp._handle, byref(ver))
            v = ver.value
            if rc == 0 and 0.0 < v < 1000.0:   # makul aralık
                return f"{v:.2f}"
            return "N/A"
        except Exception:
            return "N/A"

    def _slave_count(self) -> str:
        try:
            if config.COMM_MODE == "ECAT" and self.controller.ec_master:
                return str(self.controller.ec_master.getSlaveCount())
        except Exception:
            pass
        return "N/A"

    def disconnect(self):
        if self.controller:
            try:
                self.controller.stop_motors()
            finally:
                self.controller.disconnect()

    def send(self, curls: Dict[str, float], thumb_abduction: float):
        if not self.lhp:
            return
        vals = {
            "thumb_abduction": thumb_abduction,
            "thumb_flexion": curls["thumb"],
            "index": curls["index"],
            "middle": curls["middle"],
            "ring": curls["ring"],
            "pinky": curls["pinky"],
        }
        # PARMAK BAŞINA gönderim: sadece hedefi anlamlı değişen (hareket eden)
        # parmağa komut/kuvvet gönder. Hareketsiz parmak güncellenmez.
        for j in JOINT_ORDER:
            mid = config.MOTOR_MAP[j]                 # kamera parmağı -> robot motor id
            pos = _map_to_position(vals[j], j)
            last = self._last_sent.get(mid)
            if last is not None and abs(pos - last) < config.POS_SEND_DEADBAND:
                continue                              # bu parmak hareketsiz -> gönderme
            self.lhp.set_target_position(mid, pos)
            self.lhp.set_position_velocity(mid, config.MOVE_VELOCITY)  # HIZ
            self.lhp.set_max_current(mid, _current_for(j))            # KUVVET/TORK
            self.lhp.move_motors(mid)                 # sadece bu motoru hareket ettir
            self._last_sent[mid] = pos

        # Log/görüntü için güncel hedefler (JOINT_ORDER sırasıyla)
        self.last_positions = [_map_to_position(vals[j], j) for j in JOINT_ORDER]

    def read_forces(self) -> Dict[str, float]:
        out = {f: 0.0 for f in FINGERS}
        if not (self.lhp and config.USE_FORCE_SENSORS):
            return out
        for f, sid in config.FINGERTIP_SENSOR_IDS.items():
            try:
                out[f] = float(self.lhp.get_finger_normal_force(sid))
            except Exception:
                out[f] = 0.0
        return out

    def get_info(self) -> Dict[str, str]:
        en = sum(1 for m in self.motor_status if m["enabled"])
        return {
            "mode": "REAL (gerçek robot)",
            "comm": config.COMM_MODE,
            "dof_total": str(self.dof_total),
            "dof_active": str(self.dof_active),
            "firmware": self.firmware,
            "slaves": self._slave_count(),
            "enabled": f"{en}/{len(self.motor_status) or self.dof_active}",
        }

    def last_targets(self) -> List[int]:
        return self.last_positions


def create_hand() -> BaseHand:
    return MockHand() if config.MOCK_ROBOT else RealHand()
