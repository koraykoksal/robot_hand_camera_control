"""
EtherCAT 主站封装库
"""

import threading
import time
import pysoem
from typing import List, Optional


class SlaveInfo:
    """从站信息"""
    def __init__(self, index: int, name: str, state: int = 0, al_status: int = 0):
        self.index = index
        self.name = name
        self.state = state
        self.al_status = al_status


class EthercatMaster:
    """EtherCAT 主站封装类"""

    def __init__(self):
        self.master = pysoem.Master()
        self.slaves = []
        self.input_size = 0
        self.output_size = 0
        self.running = False
        self.thread = None
        self.ifname = None
        # 从站信息列表
        self._slave_info_list: List[SlaveInfo] = []

    def scanNetworkInterfaces(self) -> List[str]:
        """扫描可用网口，过滤虚拟/无线/回环接口，返回物理网卡设备名列表"""
        adapters = pysoem.find_adapters()
        if not adapters:
            print("⚠️ 未检测到任何可用网口！请检查 Npcap 是否安装并重启。")
            return []

        # 过滤名单：这些名字中包含的关键词将被排除
        exclude_keywords = [
            'lo',        # Linux 回环接口
            'docker',    # Docker虚拟网卡
            'veth',      # 虚拟以太网设备
            'br-',       # 网桥接口
            'virbr',     # 虚拟网桥
            'vmnet',     # VMware虚拟网卡
            'tap',       # TAP虚拟设备
            'tun',       # TUN虚拟设备
            'wlan',      # 无线网卡
            'wlp',       # 无线网卡(新命名)
            'wlx',       # 无线网卡
            'wifi',      # WiFi接口
            'wwan',      # 无线广域网
            'bluetooth', # 蓝牙
            'vboxnet',   # VirtualBox虚拟网卡
            'wintun',    # Windows TUN设备
            'p2p',       # P2P连接
            'loopback',  # 回环(Windows)
            'teredo',    # Teredo隧道
            'isatap'     # ISATAP隧道
        ]

        print("🔍 检测到以下物理网卡：")
        filtered_adapters = []
        
        for adapter in adapters:
            # 确保name是字符串并转换为小写便于比较
            if isinstance(adapter.name, bytes):
                name = adapter.name.decode('utf-8', errors='ignore').lower()
            else:
                name = str(adapter.name).lower()
            
            # 检查是否需要排除
            should_exclude = any(keyword in name for keyword in exclude_keywords)
            
            if should_exclude:
                continue  # 跳过这个接口
            
            # 获取描述用于显示
            if isinstance(adapter.desc, bytes):
                desc = adapter.desc.decode('utf-8', errors='ignore')
            else:
                desc = str(adapter.desc)
            
            print(f"  【{len(filtered_adapters)}】{desc}")
            filtered_adapters.append(adapter)
        
        # 如果所有接口都被过滤掉了
        if not filtered_adapters:
            print("  ⚠️ 未找到可用的物理网卡")
            # 可选：显示被过滤的接口用于调试
            print("  被过滤的接口:")
            for adapter in adapters:
                if isinstance(adapter.name, bytes):
                    name = adapter.name.decode('utf-8', errors='ignore')
                else:
                    name = str(adapter.name)
                print(f"    - {adapter.desc} ({name})")
        
        return [adapter.name if isinstance(adapter.name, str) else 
                adapter.name.decode('utf-8', errors='ignore') 
                for adapter in filtered_adapters]

    def _print_slave_states(self):
        """打印从站状态和错误码"""
        self.master.read_state()
        for i, slave in enumerate(self.slaves):
            state_str = {
                pysoem.INIT_STATE: "INIT",
                pysoem.PREOP_STATE: "PREOP",
                pysoem.SAFEOP_STATE: "SAFEOP",
                pysoem.OP_STATE: "OP"
            }.get(slave.state, f"UNKNOWN({slave.state})")
            print(f"  Slave {i} ({slave.name}): State={state_str}, AL Status={hex(slave.al_status)} "
                  f"({pysoem.al_status_code_to_string(slave.al_status)})")

    def init(self, channel_index: int, ifaces: List[str]) -> bool:
        """初始化 EtherCAT 主站和从站（更贴近SOEM流程）"""
        try:
            self.ifname = ifaces[channel_index]
            print(f"🔌 正在初始化 EtherCAT 主站，使用网口: {self.ifname}")
            self.master.open(self.ifname)

            # 初始化从站配置
            if self.master.config_init() <= 0:
                print("❌ 未发现任何 EtherCAT 从站设备！")
                return False

            self.slaves = self.master.slaves
            print(f"✅ 发现 {len(self.slaves)} 个从站设备")
            self._slave_info_list = []
            for i, slave in enumerate(self.slaves):
                print(f"  Slave {i+1}: {slave.name} (Vendor: {hex(slave.man)}, Product: {hex(slave.id)})")
                self._slave_info_list.append(SlaveInfo(
                    index=i+1,
                    name=slave.name,
                    state=slave.state,
                    al_status=slave.al_status
                ))

            # 映射过程数据
            self.master.config_map()
            print("✅ PDO 缓冲区映射完成")

            # 配置分布式时钟（DC）
            self.master.config_dc()
            print("✅ 分布式时钟(DC)配置完成")

            # 等待进入 SAFEOP 状态
            print("⏳ 等待从站进入 SAFEOP 状态...")
            if self.master.state_check(pysoem.SAFEOP_STATE, 50000) != pysoem.SAFEOP_STATE:
                self._print_slave_states()
                print("❌ 未能进入 SAFEOP_STATE")
                return False

            # 先发一次 processdata
            self.master.send_processdata()
            self.master.receive_processdata(1000)

            # 进入 OP 状态
            print("🚀 正在切换到 OP 状态...")
            self.master.state = pysoem.OP_STATE
            self.master.write_state()

            # 多次 processdata 检查状态
            for _ in range(10):
                self.master.send_processdata()
                self.master.receive_processdata(1000)
                if self.master.state_check(pysoem.OP_STATE, 5000) == pysoem.OP_STATE:
                    break
                time.sleep(0.05)

            if self.master.state_check(pysoem.OP_STATE, 5000) != pysoem.OP_STATE:
                self._print_slave_states()
                print("❌ 未能进入 OP_STATE")
                return False

            print("✅ 成功进入 OP 状态")

            # 获取输入输出缓冲区大小
            self.input_size = sum(len(slave.input) for slave in self.slaves)
            self.output_size = sum(len(slave.output) for slave in self.slaves)

            print(f"📊 输入总长度: {self.input_size} 字节")
            print(f"📊 输出总长度: {self.output_size} 字节")

            # 初始化输出缓冲区
            for slave in self.slaves:
                slave.output = bytes(len(slave.output))

            return True

        except Exception as e:
            print(f"❌ 初始化失败: {e}")
            return False

    def start(self) -> bool:
        """已包含在 init 中，可留空"""
        return True

    def stop(self):
        """停止主站"""
        if self.master:
            self.master.state = pysoem.INIT_STATE
            self.master.write_state()
            time.sleep(0.1)
            self.master.close()

    def run(self):
        """启动后台 IO 线程"""
        if self.running:
            return
        self.running = True
        self.thread = threading.Thread(target=self._process_io, daemon=True)
        self.thread.start()

    def _process_io(self):
        """IO 处理循环"""
        while self.running:
            self.master.send_processdata()
            self.master.receive_processdata(1000)  # 1ms 超时
            time.sleep(0.001)  # 1ms

    def setOutputs(self, data: bytes, size: int) -> bool:
        """设置输出数据"""
        if len(data) != self.output_size:
            print(f"❌ 输出数据长度不匹配: 期望 {self.output_size}, 得到 {len(data)}")
            return False

        offset = 0
        for slave in self.slaves:
            slave_out_len = len(slave.output)
            if offset + slave_out_len > len(data):
                print(f"❌ 数据偏移越界: slave {slave.name}")
                return False
            slave.output = data[offset:offset + slave_out_len]
            offset += slave_out_len

        return True

    def getInputs(self, size: int) -> Optional[bytes]:
        """获取输入数据"""
        if size != self.input_size:
            print(f"❌ 输入大小不匹配: 期望 {self.input_size}, 得到 {size}")
            return None

        inputs = bytearray()
        for slave in self.slaves:
            slave_in_len = len(slave.input)
            inputs.extend(slave.input[:slave_in_len])
        return bytes(inputs)

    def getInputSize(self) -> int:
        return self.input_size

    def getOutputSize(self) -> int:
        return self.output_size

    # ========== 多从站接口 ==========

    def getSlaveCount(self) -> int:
        """获取从站数量"""
        return len(self.slaves)

    def getSlaveInfoList(self) -> List[SlaveInfo]:
        """获取从站信息列表"""
        return self._slave_info_list

    def getSlaveInputSize(self, slave_id: int) -> int:
        """获取指定从站的输入 PDO 大小（字节）"""
        if slave_id < 1 or slave_id > len(self.slaves):
            return 0
        return len(self.slaves[slave_id - 1].input)

    def getSlaveOutputSize(self, slave_id: int) -> int:
        """获取指定从站的输出 PDO 大小（字节）"""
        if slave_id < 1 or slave_id > len(self.slaves):
            return 0
        return len(self.slaves[slave_id - 1].output)

    def setSlaveOutputs(self, slave_id: int, data: bytes, size: int) -> bool:
        """设置指定从站的输出数据"""
        if slave_id < 1 or slave_id > len(self.slaves):
            print(f"❌ 无效的从站ID: {slave_id}")
            return False

        slave = self.slaves[slave_id - 1]
        slave_out_len = len(slave.output)

        if size != slave_out_len:
            print(f"❌ 输出数据长度不匹配: 期望 {slave_out_len}, 得到 {size}")
            return False

        slave.output = data
        return True

    def getSlaveInputs(self, slave_id: int, size: int) -> Optional[bytes]:
        """获取指定从站的输入数据"""
        if slave_id < 1 or slave_id > len(self.slaves):
            print(f"❌ 无效的从站ID: {slave_id}")
            return None

        slave = self.slaves[slave_id - 1]
        slave_in_len = len(slave.input)

        if size != slave_in_len:
            print(f"❌ 输入大小不匹配: 期望 {slave_in_len}, 得到 {size}")
            return None

        return bytes(slave.input[:slave_in_len])
