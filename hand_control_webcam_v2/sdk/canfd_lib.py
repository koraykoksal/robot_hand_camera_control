#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
CANFD通信库封装
提供扫描、连接、断开、发送以及接收回调功能

Windows: 使用 HCanbus.dll（ctypes直接调用）
Linux:   使用 python-can（socketcan）
"""

import sys
import threading
import time
import ctypes
import os
from typing import Optional, Callable

# 常量定义
STATUS_OK = 0

# DLC到数据长度的映射
dlc2len = [0, 1, 2, 3, 4, 5, 6, 7, 8, 12, 16, 20, 24, 32, 48, 64]

_IS_WINDOWS = sys.platform == "win32"


class CANFDException(Exception):
    """CANFD操作异常"""
    pass


# =============================================================================
# Windows 实现：通过 ctypes 调用 HCanbus.dll
# =============================================================================

if _IS_WINDOWS:
    import ctypes.wintypes

    # ------------------------------------------------------------------
    # HCanbus.dll 结构体定义
    # ------------------------------------------------------------------

    class DevInfo(ctypes.Structure):
        _fields_ = [
            ("HW_Type", ctypes.c_char * 32),
            ("HW_Ser",  ctypes.c_char * 32),
            ("HW_Ver",  ctypes.c_char * 32),
            ("FW_Ver",  ctypes.c_char * 32),
            ("MF_Date", ctypes.c_char * 32),
        ]

    class CanFDConfig(ctypes.Structure):
        _fields_ = [
            ("NomBaud",  ctypes.c_uint),
            ("DatBaud",  ctypes.c_uint),
            ("NomPre",   ctypes.c_ushort),
            ("NomTseg1", ctypes.c_ubyte),
            ("NomTseg2", ctypes.c_ubyte),
            ("NomSJW",   ctypes.c_ubyte),
            ("DatPre",   ctypes.c_ubyte),
            ("DatTseg1", ctypes.c_ubyte),
            ("DatTseg2", ctypes.c_ubyte),
            ("DatSJW",   ctypes.c_ubyte),
            ("Config",   ctypes.c_ubyte),
            ("Model",    ctypes.c_ubyte),
            ("Cantype",  ctypes.c_ubyte),
        ]

    class CanFDMsg(ctypes.Structure):
        _fields_ = [
            ("ID",         ctypes.c_uint),
            ("TimeStamp",  ctypes.c_uint),
            ("FrameType",  ctypes.c_ubyte),
            ("DLC",        ctypes.c_ubyte),
            ("ExternFlag", ctypes.c_ubyte),
            ("RemoteFlag", ctypes.c_ubyte),
            ("BusSatus",   ctypes.c_ubyte),
            ("ErrSatus",   ctypes.c_ubyte),
            ("TECounter",  ctypes.c_ubyte),
            ("RECounter",  ctypes.c_ubyte),
            ("Data",       ctypes.c_ubyte * 64),
        ]

    def _load_hcanbus_dll() -> ctypes.WinDLL:
        """加载 HCanbus.dll，优先从 lib/ 子目录查找"""
        search_paths = [
            os.path.join(os.path.dirname(__file__), "lib", "HCanbus.dll"),
            os.path.join(os.path.dirname(__file__), "HCanbus.dll"),
            "HCanbus.dll",
        ]
        for path in search_paths:
            if os.path.exists(path):
                return ctypes.WinDLL(os.path.abspath(path))
        raise CANFDException("找不到 HCanbus.dll，请将其放在 lib/ 目录下")

    def _len_to_dlc(length: int) -> int:
        if length <= 8:
            return length
        elif length <= 12:
            return 9
        elif length <= 16:
            return 10
        elif length <= 20:
            return 11
        elif length <= 24:
            return 12
        elif length <= 32:
            return 13
        elif length <= 48:
            return 14
        else:
            return 15

    class CANFD:
        """CANFD通信类 - Windows实现（HCanbus.dll）"""

        _RECV_BUF_SIZE = 500
        _RECV_TIMEOUT_MS = 50
        _RECV_SLEEP_MS = 0.005

        def __init__(self):
            self._dll = _load_hcanbus_dll()
            self._dev_index = -1
            self._is_connected = False
            self._receive_callback: Optional[Callable] = None
            self._receive_thread: Optional[threading.Thread] = None
            self._receive_running = False

        def scan(self) -> int:
            """扫描HCanbus设备，返回设备数量"""
            try:
                return int(self._dll.CAN_ScanDevice())
            except Exception as e:
                raise CANFDException(f"扫描设备异常: {e}")

        def connect(self, device_index: int = 0, channel_index: int = 0,
                    nom_baudrate: int = 1000000, dat_baudrate: int = 5000000,
                    nom_sampling: int = 0, dat_sampling: int = 0) -> bool:
            """连接HCanbus CANFD设备

            Args:
                device_index:  设备索引
                channel_index: 通道索引（HCanbus单通道设备忽略）
                nom_baudrate:  仲裁段波特率（Hz）
                dat_baudrate:  数据段波特率（Hz）
                nom_sampling:  仲裁段采样点模式 0=80%  1=75%
                dat_sampling:  数据段采样点模式 0=75%
            """
            if self._is_connected:
                self.disconnect()

            try:
                ret = self._dll.CAN_OpenDevice(ctypes.c_uint(device_index))
                if ret != 0:
                    raise CANFDException(f"CAN_OpenDevice 失败，返回值: {ret}")

                cfg = CanFDConfig()
                cfg.Model    = 0                              # 正常模式
                cfg.NomBaud  = nom_baudrate
                cfg.DatBaud  = dat_baudrate
                cfg.Config   = 0x01 | 0x02 | 0x04            # 终端电阻+唤醒+重传
                cfg.Cantype  = 1                              # ISO CANFD

                if nom_sampling == 0:
                    cfg.NomPre = 2; cfg.NomTseg1 = 31; cfg.NomTseg2 = 8;  cfg.NomSJW = 5
                else:
                    cfg.NomPre = 2; cfg.NomTseg1 = 29; cfg.NomTseg2 = 10; cfg.NomSJW = 6

                if dat_sampling == 0:
                    cfg.DatPre = 1; cfg.DatTseg1 = 11; cfg.DatTseg2 = 4;  cfg.DatSJW = 2

                ret = self._dll.CANFD_Init(ctypes.c_uint(device_index), ctypes.byref(cfg))
                if ret != 0:
                    self._dll.CAN_CloseDevice(ctypes.c_uint(device_index))
                    raise CANFDException(f"CANFD_Init 失败，返回值: {ret}")

                self._dev_index = device_index
                self._is_connected = True

                # 启动接收线程
                self._receive_running = True
                self._receive_thread = threading.Thread(
                    target=self._receive_loop, daemon=True)
                self._receive_thread.start()

                return True

            except CANFDException:
                raise
            except Exception as e:
                raise CANFDException(f"连接设备异常: {e}")

        def disconnect(self) -> bool:
            """断开HCanbus设备连接"""
            if not self._is_connected:
                return True

            self._receive_running = False
            if self._receive_thread and self._receive_thread.is_alive():
                self._receive_thread.join(timeout=1.0)

            try:
                self._dll.CAN_CloseDevice(ctypes.c_uint(self._dev_index))
            except Exception:
                pass

            self._is_connected = False
            self._dev_index = -1
            self._receive_callback = None
            return True

        def send(self, id: int, data: bytes, frame_type: int = 0x04,
                 extern_flag: int = 0, remote_flag: int = 0) -> bool:
            """发送CANFD数据（固定64字节帧）

            Args:
                id:           消息ID
                data:         要发送的数据（最多64字节）
                frame_type:   帧类型（默认0x04 CANFD帧）
                extern_flag:  扩展帧标志（0标准帧/1扩展帧）
                remote_flag:  远程帧标志（0数据帧/1远程帧）
            """
            if not self._is_connected:
                raise CANFDException("设备未连接")
            if len(data) > 64:
                raise CANFDException("数据长度不能超过64字节")

            msg = CanFDMsg()
            msg.ID         = id
            msg.FrameType  = frame_type
            msg.DLC        = _len_to_dlc(64)   # 固定64字节，与C++版本一致
            msg.ExternFlag = extern_flag
            msg.RemoteFlag = remote_flag

            # 填充数据
            ctypes.memset(msg.Data, 0, 64)
            for i, b in enumerate(data[:64]):
                msg.Data[i] = b

            ret = self._dll.CANFD_Transmit(
                ctypes.c_uint(self._dev_index),
                ctypes.byref(msg),
                ctypes.c_uint(1),
                ctypes.c_int(100)
            )
            return ret == 1

        def set_receive_callback(self, callback: Optional[Callable[[dict], None]]) -> None:
            """设置接收回调函数

            Args:
                callback: 接收到消息时调用，参数为字典格式：
                          {"id", "timestamp", "frame_type", "dlc", "data_len",
                           "extern_flag", "remote_flag", "bus_status", "err_status",
                           "te_counter", "re_counter", "data"}
            """
            self._receive_callback = callback

        def _receive_loop(self) -> None:
            """接收线程主循环"""
            MsgArray = CanFDMsg * self._RECV_BUF_SIZE
            msgs = MsgArray()
            while self._receive_running:
                if not self._is_connected:
                    time.sleep(0.01)
                    continue

                count = self._dll.CANFD_Receive(
                    ctypes.c_uint(self._dev_index),
                    msgs,
                    ctypes.c_uint(self._RECV_BUF_SIZE),
                    ctypes.c_int(self._RECV_TIMEOUT_MS)
                )

                if count > 0 and self._receive_callback:
                    for i in range(count):
                        m = msgs[i]
                        data_len = dlc2len[m.DLC] if m.DLC < len(dlc2len) else 64
                        canfd_msg = {
                            "id":          m.ID,
                            "timestamp":   m.TimeStamp,
                            "frame_type":  m.FrameType,
                            "dlc":         m.DLC,
                            "data_len":    data_len,
                            "extern_flag": m.ExternFlag,
                            "remote_flag": m.RemoteFlag,
                            "bus_status":  m.BusSatus,
                            "err_status":  m.ErrSatus,
                            "te_counter":  m.TECounter,
                            "re_counter":  m.RECounter,
                            "data":        bytes(m.Data[:data_len]),
                        }
                        try:
                            self._receive_callback(canfd_msg)
                        except Exception as e:
                            print(f"CANFD接收回调异常: {e}")

                time.sleep(self._RECV_SLEEP_MS)

        @property
        def is_connected(self) -> bool:
            return self._is_connected

        def __del__(self):
            try:
                if self._is_connected:
                    self.disconnect()
            except Exception:
                pass


# =============================================================================
# Linux 实现：使用 python-can（socketcan）
# =============================================================================

else:
    import subprocess
    import can

    class CANFD:
        """CANFD通信类 - Linux实现（python-can socketcan）"""

        def __init__(self):
            self._is_connected = False
            self._device_index = 0
            self._interface = ""
            self._nom_baudrate = 1000000
            self._dat_baudrate = 5000000
            self._bus = None
            self._receive_thread: Optional[threading.Thread] = None
            self._receive_stop_event = threading.Event()
            self._receive_callback: Optional[Callable] = None

        def scan(self) -> int:
            """扫描socketcan接口，返回 can* 接口数量"""
            try:
                can_interfaces = []
                if os.path.exists("/sys/class/net"):
                    for ifname in os.listdir("/sys/class/net"):
                        if ifname.startswith("can"):
                            can_interfaces.append(ifname)
                return len(can_interfaces)
            except Exception as e:
                raise CANFDException(f"扫描设备异常: {e}")

        def _setup_can_interface(self, ifname: str, nom_baudrate: int,
                                 dat_baudrate: int) -> bool:
            try:
                subprocess.run(["modprobe", "-r", "gs_usb"], capture_output=True)
                subprocess.run(["modprobe",  "gs_usb"],      capture_output=True)
                subprocess.run(
                    ["bash", "-c",
                     "echo 'a8fa 8598' | sudo tee /sys/bus/usb/drivers/gs_usb/new_id"],
                    capture_output=True)
                subprocess.run(["ip", "link", "set", ifname, "down"], capture_output=True)
                subprocess.run(
                    ["ip", "link", "set", ifname, "type", "can",
                     "bitrate", str(nom_baudrate),
                     "dbitrate", str(dat_baudrate),
                     "fd", "on", "loopback", "off", "listen-only", "off"],
                    capture_output=True)
                subprocess.run(["ip", "link", "set", ifname, "up"], capture_output=True)
                return True
            except Exception as e:
                print(f"设置CAN接口失败: {e}")
                return False

        def connect(self, device_index: int = 0, channel_index: int = 0,
                    nom_baudrate: int = 1000000, dat_baudrate: int = 5000000,
                    nom_sampling: int = 0, dat_sampling: int = 0) -> bool:
            """连接socketcan CANFD设备"""
            try:
                self._device_index = device_index
                self._nom_baudrate = nom_baudrate
                self._dat_baudrate = dat_baudrate

                can_interfaces = []
                if os.path.exists("/sys/class/net"):
                    for ifname in os.listdir("/sys/class/net"):
                        if ifname.startswith("can"):
                            can_interfaces.append(ifname)

                if device_index < 0 or device_index >= len(can_interfaces):
                    raise CANFDException(f"设备索引无效: {device_index}")

                self._interface = can_interfaces[device_index]

                if not self._setup_can_interface(self._interface, nom_baudrate, dat_baudrate):
                    raise CANFDException("设置CAN接口失败")

                self._bus = can.Bus(
                    interface='socketcan',
                    channel=self._interface,
                    bitrate=nom_baudrate,
                    fd=True,
                    can_filters=[]
                )

                self._is_connected = True
                return True

            except CANFDException:
                raise
            except Exception as e:
                if self._bus:
                    try:
                        self._bus.shutdown()
                    except Exception:
                        pass
                    self._bus = None
                raise CANFDException(f"连接设备异常: {e}")

        def disconnect(self) -> bool:
            """断开socketcan设备连接"""
            if not self._is_connected:
                return True

            if self._receive_thread and self._receive_thread.is_alive():
                self._receive_stop_event.set()
                self._receive_thread.join(timeout=1.0)

            if self._bus:
                try:
                    self._bus.shutdown()
                except Exception:
                    pass
                self._bus = None

            if self._interface:
                try:
                    subprocess.run(
                        ["ip", "link", "set", self._interface, "down"],
                        capture_output=True)
                except Exception:
                    pass

            self._is_connected = False
            self._interface = ""
            self._receive_callback = None
            return True

        def send(self, id: int, data: bytes, frame_type: int = 0x04,
                 extern_flag: int = 0, remote_flag: int = 0) -> bool:
            """发送CANFD数据"""
            if not self._is_connected:
                raise CANFDException("设备未连接")
            if len(data) > 64:
                raise CANFDException("数据长度不能超过64字节")

            msg = can.Message(
                arbitration_id=id,
                data=data,
                is_extended_id=bool(extern_flag),
                is_remote_frame=bool(remote_flag),
                is_fd=True,
                bitrate_switch=True
            )
            try:
                self._bus.send(msg)
                return True
            except Exception as e:
                raise CANFDException(f"发送数据失败: {e}")

        def set_receive_callback(self, callback: Optional[Callable[[dict], None]]) -> None:
            """设置接收回调函数"""
            self._receive_callback = callback
            if callback and not (self._receive_thread and self._receive_thread.is_alive()):
                if not self._is_connected:
                    raise CANFDException("设备未连接")
                self._receive_stop_event.clear()
                self._receive_thread = threading.Thread(
                    target=self._receive_loop, daemon=True)
                self._receive_thread.start()

        def _receive_loop(self) -> None:
            try:
                while not self._receive_stop_event.is_set():
                    try:
                        msg = self._bus.recv(timeout=0.1)
                        if msg and self._receive_callback:
                            data_len = len(msg.data)
                            dlc = next(
                                (i for i, v in enumerate(dlc2len) if v == data_len), 15)
                            canfd_msg = {
                                "id":          msg.arbitration_id,
                                "timestamp":   int(msg.timestamp * 1000),
                                "frame_type":  0x04,
                                "dlc":         dlc,
                                "data_len":    data_len,
                                "extern_flag": 1 if msg.is_extended_id else 0,
                                "remote_flag": 1 if msg.is_remote_frame else 0,
                                "bus_status":  0,
                                "err_status":  0,
                                "te_counter":  0,
                                "re_counter":  0,
                                "data":        bytes(msg.data),
                            }
                            try:
                                self._receive_callback(canfd_msg)
                            except Exception as e:
                                print(f"CANFD接收回调异常: {e}")
                    except Exception:
                        time.sleep(0.01)
                    time.sleep(0.001)
            except Exception as e:
                print(f"CANFD接收线程异常: {e}")
                if self._is_connected:
                    try:
                        self.disconnect()
                    except Exception:
                        pass

        @property
        def is_connected(self) -> bool:
            return self._is_connected

        def __del__(self):
            try:
                if self._is_connected:
                    self.disconnect()
            except Exception:
                pass


# =============================================================================
# 示例用法
# =============================================================================

if __name__ == "__main__":
    def receive_callback(msg):
        print(f"接收到CANFD消息:")
        print(f"  ID: 0x{msg['id']:X}")
        print(f"  数据长度: {msg['data_len']}")
        print(f"  数据: {[hex(b) for b in msg['data']]}")
        print(f"  时间戳: {msg['timestamp']}")
        print(f"  帧类型: {msg['frame_type']}")

    try:
        canfd = CANFD()

        device_count = canfd.scan()
        print(f"扫描到 {device_count} 个CANFD设备")

        if device_count == 0:
            print("未找到CANFD设备")
            exit()

        print("正在连接CANFD设备...")
        canfd.connect(nom_baudrate=1000000, dat_baudrate=5000000)
        print("CANFD设备连接成功")

        canfd.set_receive_callback(receive_callback)

        print("发送测试数据...")
        test_data = bytes([0x01, 0x06, 0x00, 0x01, 0x00, 0x01,
                           0x00, 0x01, 0x00, 0x01, 0x00, 0x01, 0x00, 0x01])
        canfd.send(0x500 + 1, test_data)
        print("测试数据发送成功")

        print("按Ctrl+C退出程序")
        while True:
            time.sleep(1)

    except KeyboardInterrupt:
        print("\n用户中断程序")
    except CANFDException as e:
        print(f"CANFD错误: {e}")
    except Exception as e:
        print(f"其他错误: {e}")
    finally:
        if 'canfd' in locals() and canfd.is_connected:
            print("正在断开CANFD设备连接...")
            canfd.disconnect()
            print("CANFD设备已断开连接")
