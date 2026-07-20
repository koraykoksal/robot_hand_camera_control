"""
LHandPro 控制器封装类 - 支持ECAT、CANFD和RS485三模式
提供连接、断开、运动控制等功能
"""

import time
import threading
from typing import Optional, List, Tuple
from lhandprolib_wrapper import PyLHandProLib, LHandProLibError, LCM_POSITION, LCN_ECAT, LCN_CANFD, LCN_RS485, LAC_DOF_6, LAC_DOF_6_S
from canfd_lib import CANFD
from config import CURRENT_HAND_TYPE, CANFD_NODE_ID, RS485_PORT_NAME, ENABLE_HOME_CHECK, ENABLE_TORQUE_CONTROL


class LHandProController:
    """LHandPro 控制器封装类 - 支持ECAT、CANFD和RS485三模式"""

    def __init__(self, communication_mode: str):
        """初始化控制器

        Args:
            communication_mode: 通信模式，可选值为 "CANFD"、"ECAT" 或 "RS485"
        """
        self.lhp: Optional[PyLHandProLib] = None

        # CANFD 相关属性
        self.canfd = None

        # ECAT 相关属性
        self.ec_master = None
        self.stop_flag = None
        self.monitor_thread = None

        # RS485 相关属性
        self.serial_port = None

        # 通用属性
        self.is_connected = False
        self.dof_total = 0
        self.dof_active = 0
        self.communication_mode = communication_mode.upper()

        # 验证通信模式
        if self.communication_mode not in ["CANFD", "ECAT", "RS485"]:
            raise ValueError(f"不支持的通信模式: {self.communication_mode}，请使用 'CANFD'、'ECAT' 或 'RS485'")

        # 根据通信模式动态导入所需库
        self._import_communication_libs()
    
    def _import_communication_libs(self):
        """根据通信模式导入所需的库"""
        global CANFD, EthercatMaster, SerialPort

        if self.communication_mode == "CANFD":
            from canfd_lib import CANFD
        elif self.communication_mode == "ECAT":
            from ethercat_master import EthercatMaster
        elif self.communication_mode == "RS485":
            from serial_port import SerialPort

    def _canfd_send_callback(self, msg_id: int, data: bytes) -> bool:
        """CANFD 发送回调函数"""
        if self.canfd and self.is_connected:
            try:
                # 使用传入的msg_id作为CANFD消息ID
                return self.canfd.send(msg_id, data)
            except Exception as e:
                print(f"CANFD发送失败: {e}")
        return False

    def _canfd_receive_callback(self, msg):
        """CANFD 接收回调函数"""
        if self.lhp and self.is_connected:
            if msg["id"] != 0x480+1:
                return
            # 处理接收到的CANFD消息
            self.lhp.set_canfd_data_decode(msg["id"], msg["data"])
    
    def _rs485_send_callback(self, data: bytes) -> bool:
        """RS485 发送回调函数"""
        if self.serial_port and self.is_connected:
            try:
                return self.serial_port.write(data) > 0
            except Exception as e:
                print(f"RS485发送失败: {e}")
        return False

    def _rs485_receive_callback(self, data: bytes):
        """RS485 接收回调函数"""
        if self.lhp and self.is_connected:
            self.lhp.set_rs485_data_decode(data)

    def _ec_send_callback(self, data: bytes) -> bool:
        """EtherCAT 发送回调函数"""
        if self.ec_master:
            return self.ec_master.setOutputs(data, len(data))
        print("EC master not initialized!")
        return False

    def _monitor_thread_func(self):
        """监控线程函数：刷新解析 TPDO 数据"""
        while not self.stop_flag.is_set() and self.is_connected:
            if self.ec_master:
                input_size = self.ec_master.getInputSize()
                inputs = self.ec_master.getInputs(input_size)
                if inputs is not None and self.lhp:
                    self.lhp.set_tpdo_data_decode(inputs)
            time.sleep(0.01)  # 10ms

    def connect(
        self,
        enable_motors: bool = True,
        home_motors: bool = True,
        home_wait_time: float = 5.0,
        device_index: Optional[int] = None,
        auto_select: bool = True,
        # CANFD 特定参数
        canfd_nom_baudrate: int = 1000000,
        canfd_dat_baudrate: int = 5000000,
        # RS485 特定参数
        rs485_port_name: str = None,
        rs485_baud_rate: int = 500000,
        rs485_node_id: int = 1,
    ) -> bool:
        """
        连接并初始化 LHandPro 设备

        Args:
            enable_motors: 是否自动使能电机
            home_motors: 是否自动回零
            home_wait_time: 回零等待时间（秒）

            # CANFD 特定参数
            canfd_nom_baudrate: CANFD标称波特率
            canfd_dat_baudrate: CANFD数据波特率
            device_index: 设备索引，如果为None则自动选择或让用户选择
                          （注意：通道固定为0）
            auto_select: 是否自动选择设备（当有多个设备时）

            # RS485 特定参数
            rs485_port_name: 串口名称，如 'COM1' 或 '/dev/ttyUSB0'，None则自动选择
            rs485_baud_rate: 波特率，None则使用config中的RS485_BAUD_RATE
            rs485_node_id: 节点ID，None则使用config中的RS485_NODE_ID

        Returns:
            bool: 连接是否成功
        """
        try:
            # 创建 PyLHandProLib 实例
            self.lhp = PyLHandProLib()

            retn = False

            if self.communication_mode == "CANFD":
                retn = self._connect_canfd(
                    enable_motors=enable_motors,
                    home_motors=home_motors,
                    home_wait_time=home_wait_time,
                    canfd_nom_baudrate=canfd_nom_baudrate,
                    canfd_dat_baudrate=canfd_dat_baudrate,
                    device_index=device_index,
                    auto_select=auto_select
                )
            elif self.communication_mode == "ECAT":
                retn = self._connect_ecat(
                    enable_motors=enable_motors,
                    home_motors=home_motors,
                    home_wait_time=home_wait_time,
                    channel_index=device_index,
                    auto_select=auto_select
                )
            elif self.communication_mode == "RS485":
                retn = self._connect_rs485(
                    enable_motors=enable_motors,
                    home_motors=home_motors,
                    home_wait_time=home_wait_time,
                    port_name=rs485_port_name,
                    baud_rate=rs485_baud_rate,
                    node_id=rs485_node_id,
                    device_index=device_index,
                )

            self.lhp.set_hand_type(CURRENT_HAND_TYPE)

            if ENABLE_HOME_CHECK:
                self.lhp.set_move_no_home(0)
            else:
                self.lhp.set_move_no_home(1)    

            # 设置扭矩控制模式
            if ENABLE_TORQUE_CONTROL:
                self.lhp.set_torque_control_mode(0, 1)
            else:
                self.lhp.set_torque_control_mode(0, 0)

            return retn
        
        except (LHandProLibError, Exception) as e:
            print(f"操作失败: {e}")
            if self.lhp:
                self.lhp.close()
                self.lhp = None
            self._cleanup_communication_resources()
            return False
    
    def _connect_canfd(self, enable_motors, home_motors, home_wait_time, canfd_nom_baudrate, canfd_dat_baudrate, device_index, auto_select):
        """使用CANFD模式连接设备"""
        # 初始化CANFD
        self.canfd = CANFD()
        
        print("正在使用CANFD通讯:")
        
        # 扫描CANFD设备
        device_count = self.canfd.scan()
        print(f"找到CANFD设备数量：{device_count}")
        
        if device_count == 0:
            print("未找到CANFD设备")
            self.lhp.close()
            self.lhp = None
            self.canfd = None
            return False
        
        # 处理设备索引
        if device_index is None:
            if device_count == 1:
                # 只有一个设备时自动选择
                print(f"✅ 检测到单个设备，自动选择设备索引: 0")
                device_index = 0
            elif auto_select:
                # 自动选择第一个设备
                print(f"✅ 自动选择设备索引: 0")
                device_index = 0
            else:
                # 多个设备时让用户选择
                print(f"请选择对应设备 [0 - {device_count - 1}]")
                while True:
                    try:
                        user_input = input(">>> ")
                        if user_input.strip() == "":
                            print(f"使用默认选择: 0")
                            device_index = 0
                            break

                        device_index = int(user_input)
                        if 0 <= device_index < device_count:
                            print(f"已选择设备索引: {device_index}")
                            break
                        else:
                            print(f"请输入 [0 - {device_count - 1}]")
                    except ValueError:
                        print("请输入数字")
        else:
            if not (0 <= device_index < device_count):
                print(f"无效的设备索引: {device_index}")
                self.lhp.close()
                self.lhp = None
                self.canfd = None
                return False
            print(f"使用指定设备索引: {device_index}")
        
        # 连接CANFD设备
        print(f"正在连接CANFD设备，标称波特率: {canfd_nom_baudrate}bps，数据波特率: {canfd_dat_baudrate}bps")
        if not self.canfd.connect(device_index=device_index, channel_index=0, nom_baudrate=canfd_nom_baudrate, dat_baudrate=canfd_dat_baudrate):
            print("CANFD设备连接失败")
            self.lhp.close()
            self.lhp = None
            self.canfd = None
            return False
        
        print("CANFD设备连接成功")
        self.is_connected = True
        
        # 设置CANFD接收回调
        self.canfd.set_receive_callback(self._canfd_receive_callback)
        
        # 设置发送回调
        self.lhp.set_send_canfd_callback(self._canfd_send_callback)      

        # 初始化LHandProLib为CANFD模式
        self.lhp.initial_ex(LCN_CANFD, CANFD_NODE_ID)
        
        # 执行通用初始化步骤
        return self._common_initialization(enable_motors, home_motors, home_wait_time)
    
    def _connect_ecat(self, enable_motors, home_motors, home_wait_time, channel_index, auto_select):
        """使用ECAT模式连接设备"""
        # 创建 EtherCAT 主站
        self.ec_master = EthercatMaster()

        print("正在使用EtherCAT通讯(100M):\n")

        # 扫描网口
        names = self.ec_master.scanNetworkInterfaces()
        print(f"找到网口数量：{len(names)}\n")

        if len(names) == 0:
            print("未找到网口")
            self.lhp.close()
            self.lhp = None
            return False

        # 选择网口
        if channel_index is None:
            if len(names) == 1:
                # 只有一个网口时自动选择
                print(f"✅ 检测到单个网口，自动选择: {names[0]}")
                channel_index = 0
            elif auto_select:
                # 自动选择第一个网口
                print(f"✅ 自动选择网口: {names[0]}")
                channel_index = 0
            else:
                # 多个网口时让用户选择
                print(f"请选择对应网口 [0 - {len(names) - 1}]")
                print("可用网口列表:")
                for i, name in enumerate(names):
                    print(f"  [{i}] {name}")

                while True:
                    try:
                        user_input = input(">>> ")
                        if user_input.strip() == "":
                            print(f"使用默认选择: 0 ({names[0]})")
                            channel_index = 0
                            break

                        channel_index = int(user_input)
                        if 0 <= channel_index < len(names):
                            print(f"已选择: [{channel_index}] {names[channel_index]}")
                            break
                        else:
                            print(f"请输入 [0 - {len(names) - 1}]")
                    except ValueError:
                        print("请输入数字")
        else:
            if not (0 <= channel_index < len(names)):
                print(f"无效的网口索引: {channel_index}")
                self.lhp.close()
                self.lhp = None
                return False
            print(f"使用指定网口: [{channel_index}] {names[channel_index]}")

        # 初始化 EtherCAT
        if not self.ec_master.init(channel_index, names):
            print("初始化失败")
            self.lhp.close()
            self.lhp = None
            return False

        print("连接成功")
        self.is_connected = True

        if not self.ec_master.start():
            print("启动设备失败")
            self.lhp.close()
            self.lhp = None
            return False

        # 启动后台 IO
        self.ec_master.run()

        # 设置回调
        self.lhp.set_send_rpdo_callback(self._ec_send_callback)

        # 启动监控线程
        self.stop_flag = threading.Event()
        self.monitor_thread = threading.Thread(target=self._monitor_thread_func, daemon=True)
        self.monitor_thread.start()

        # 初始化 LHandProLib
        try:
            self.lhp.initial(LCN_ECAT)
        except LHandProLibError as e:
            print(f"LHandProLib 初始化失败: {e}")
            self.disconnect()
            return False
            
        # 执行通用初始化步骤
        return self._common_initialization(enable_motors, home_motors, home_wait_time)
    
    def _connect_rs485(self, enable_motors, home_motors, home_wait_time, port_name, baud_rate, node_id, device_index=None):
        """使用RS485模式连接设备"""
        from serial_port import SerialPort
        self.serial_port = SerialPort()

        print("正在使用RS485通讯:")

        # 选择串口
        if port_name is None:
            available_ports = self.serial_port.scan_available_ports()
            if not available_ports:
                print("未找到可用串口")
                self.lhp.close()
                self.lhp = None
                self.serial_port = None
                return False

            print("\n可用串口列表:")
            for i, port in enumerate(available_ports):
                print(f"  [{i}] {port}")

            if device_index is not None:
                # 多进程场景：用 device_index 直接映射到对应串口
                if device_index < len(available_ports):
                    port_name = available_ports[device_index]
                    print(f"根据设备索引 {device_index} 自动选择串口: {port_name}")
                else:
                    print(f"设备索引 {device_index} 超出可用串口数量 {len(available_ports)}")
                    self.lhp.close()
                    self.lhp = None
                    self.serial_port = None
                    return False
            elif len(available_ports) == 1:
                port_name = available_ports[0]
                print(f"自动选择唯一可用串口: {port_name}")
            else:
                while True:
                    try:
                        user_input = input(f"\n请选择串口编号 [0-{len(available_ports) - 1}]: ")
                        port_index = int(user_input)
                        if 0 <= port_index < len(available_ports):
                            port_name = available_ports[port_index]
                            break
                        print("请输入有效的编号")
                    except ValueError:
                        print("请输入数字")

        # 打开串口
        print(f"正在打开串口 {port_name}，波特率: {baud_rate}bps")
        if not self.serial_port.open(port_name, baud_rate):
            print(f"打开串口失败: {port_name}")
            self.lhp.close()
            self.lhp = None
            self.serial_port = None
            return False

        print("串口打开成功")
        self.is_connected = True

        # 设置发送回调
        self.lhp.set_send_rs485_callback(self._rs485_send_callback)

        # 设置接收回调
        self.serial_port.set_read_callback(self._rs485_receive_callback)

        # 初始化LHandProLib为RS485模式
        print(f"正在初始化 (RS485, node_id={node_id}) ...")
        try:
            self.lhp.initial_ex(LCN_RS485, node_id)
        except Exception as e:
            print(f"初始化失败: {e}")
            self.disconnect()
            return False
        print("初始化成功")

        # 执行通用初始化步骤
        return self._common_initialization(enable_motors, home_motors, home_wait_time)

    def _common_initialization(self, enable_motors, home_motors, home_wait_time):
        """通用初始化步骤"""
        # 获取自由度
        self.dof_total, self.dof_active = self.lhp.get_dof()
        print(f"自由度: 总共 {self.dof_total}, 主动 {self.dof_active}")

        # 使能电机
        if enable_motors:
            self.lhp.set_enable(0, True)
            print("等待使能完成")
            time.sleep(1.0)

        # 回零
        if home_motors:
            print("正在回零")
            self.lhp.home_motors(0)
            time.sleep(home_wait_time)

        return True
    
    def _cleanup_communication_resources(self):
        """清理通信资源"""
        if self.communication_mode == "CANFD" and self.canfd:
            self.canfd.disconnect()
            self.canfd = None
        elif self.communication_mode == "ECAT":
            if self.stop_flag:
                self.stop_flag.set()
            if self.monitor_thread and self.monitor_thread.is_alive():
                self.monitor_thread.join(timeout=2.0)
            if self.ec_master:
                self.ec_master.stop()
                time.sleep(0.1)
                self.ec_master = None
        elif self.communication_mode == "RS485" and self.serial_port:
            self.serial_port.close()
            self.serial_port = None
    
    def disconnect(self):
        """断开连接并清理资源"""
        if not self.is_connected:
            return

        print("正在断开连接...")

        # 关闭库
        if self.lhp:
            self.lhp.close()
            self.lhp = None

        # 清理通信资源
        self._cleanup_communication_resources()

        self.is_connected = False
        print("已断开连接")

    def move_to_positions(
        self,
        positions: List[int],
        velocity: int = 20000,
        max_current: int = 1000,
        wait_time: float = 1.0
    ) -> bool:
        """
        移动到指定位置

        Args:
            positions: 位置列表，长度为 dof_active
            velocity: 位置速度
            max_current: 最大电流
            wait_time: 运动后等待时间（秒）

        Returns:
            bool: 是否成功发送运动指令
        """
        if not self.is_connected or not self.lhp:
            print("设备未连接")
            return False

        if len(positions) != self.dof_active:
            print(f"位置数量不匹配: 期望 {self.dof_active}, 得到 {len(positions)}")
            return False

        try:
            for j in range(self.dof_active):
                motor_id = j + 1
                self.lhp.set_target_position(motor_id, positions[j])
                self.lhp.set_position_velocity(motor_id, velocity)
                self.lhp.set_max_current(motor_id, max_current)

            self.lhp.move_motors(0)
            print(f"✅ 运动指令发送成功: positions={positions}")
            if wait_time > 0:
                time.sleep(wait_time)
            return True
        except Exception as e:
            print(f"运动控制失败: {e}")
            return False

    def move_to_angles(
        self,
        angles: List[float],
        angular_velocity: float = 200.0,
        max_current: int = 1000,
        wait_time: float = 1.0
    ) -> bool:
        """
        移动到指定角度

        Args:
            angles: 角度列表，长度为 dof_active
            angular_velocity: 角速度
            max_current: 最大电流
            wait_time: 运动后等待时间（秒）

        Returns:
            bool: 是否成功发送运动指令
        """
        if not self.is_connected or not self.lhp:
            print("设备未连接")
            return False

        if len(angles) != self.dof_active:
            print(f"角度数量不匹配: 期望 {self.dof_active}, 得到 {len(angles)}")
            return False

        try:
            for j in range(self.dof_active):
                motor_id = j + 1
                self.lhp.set_target_angle(motor_id, angles[j])
                self.lhp.set_angular_velocity(motor_id, angular_velocity)
                self.lhp.set_max_current(motor_id, max_current)

            self.lhp.move_motors(0)
            print(f"✅ 运动指令发送成功: angles={angles}")
            if wait_time > 0:
                time.sleep(wait_time)
            return True
        except Exception as e:
            print(f"运动控制失败: {e}")
            return False

    def move_sequence(
        self,
        positions_list: List[List[int]],
        velocity: int = 20000,
        max_current: int = 1000,
        wait_time: float = 1.0
    ) -> bool:
        """
        执行一系列位置运动

        Args:
            positions_list: 位置序列列表，每个元素是一个位置列表
            velocity: 位置速度
            max_current: 最大电流
            wait_time: 每个运动后等待时间（秒）

        Returns:
            bool: 是否成功执行所有运动
        """
        if not self.is_connected or not self.lhp:
            print("设备未连接")
            return False

        try:
            for i, pos_list in enumerate(positions_list):
                success = self.move_to_positions(pos_list, velocity, max_current, wait_time)
                if not success:
                    print(f"第 {i} 个位置运动失败")
                    return False
                print(f"line: {i} positions: {pos_list} ✅")
            return True
        except Exception as e:
            print(f"运动序列执行失败: {e}")
            return False

    def enable_motors(self, enable: bool = True):
        """
        使能/禁用电机

        Args:
            enable: True为使能，False为禁用
        """
        if not self.is_connected or not self.lhp:
            print("设备未连接")
            return

        try:
            self.lhp.set_enable(0, enable)
            print(f"电机{'使能' if enable else '禁用'}成功")
        except Exception as e:
            print(f"设置电机使能状态失败: {e}")

    def home(self, wait_time: float = 5.0):
        """
        回零操作

        Args:
            wait_time: 回零等待时间（秒）
        """
        if not self.is_connected or not self.lhp:
            print("设备未连接")
            return

        try:
            print("正在回零")
            self.lhp.home_motors(0)
            time.sleep(wait_time)
            print("回零完成")
        except Exception as e:
            print(f"回零失败: {e}")

    def stop_motors(self):
        """
        停止所有电机运动
        """
        if not self.is_connected or not self.lhp:
            print("设备未连接")
            return

        try:
            self.lhp.stop_motors(0)
            print("✅ 所有电机已停止")
        except Exception as e:
            print(f"停止电机失败: {e}")

    def move_to_zero(
        self,
        velocity: int = 20000,
        max_current: int = 1000,
        wait_time: float = 1.0
    ) -> bool:
        """
        移动到零位置（所有位置为0）

        Args:
            velocity: 位置速度
            max_current: 最大电流
            wait_time: 运动后等待时间（秒）

        Returns:
            bool: 是否成功发送运动指令
        """
        zero_positions = [0] * self.dof_active
        return self.move_to_positions(zero_positions, velocity, max_current, wait_time)

    def get_dof(self) -> Tuple[int, int]:
        """
        获取自由度信息

        Returns:
            Tuple[int, int]: (总自由度, 主动自由度)
        """
        return self.dof_total, self.dof_active
    
    def clear_alarm(self) -> None:
        """
        清除所有电机报警
        """
        if not self.is_connected or not self.lhp:
            print("设备未连接")
            return
        
        try:
            self.lhp.set_clear_alarm(0)
            print("✅ 已清除所有电机报警")
        except Exception as e:
            print(f"清除报警失败: {e}")
    
    def get_alarm(self) -> bool:
        """
        获取所有电机的报警状态
        
        Returns:
            bool: 如果有任何一个电机报警，返回True；否则返回False
        """
        if not self.is_connected or not self.lhp:
            print("设备未连接")
            return False
        
        try:
            for motor_id in range(1, self.dof_active + 1):
                alarm = self.lhp.get_now_alarm(motor_id)
                if alarm == 1:
                    print(f"⚠️ 电机 {motor_id} 报警")
                    return True
            return False
        except Exception as e:
            print(f"获取报警状态失败: {e}")
            return False

    def __enter__(self):
        """上下文管理器入口"""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """上下文管理器出口，自动断开连接"""
        self.disconnect()
        return False