"""
LHandProLib的Python面向对象封装
"""

from typing import Optional, List, Tuple, Callable
from ctypes import c_int, c_uint, c_float, c_bool, c_char, c_char_p, POINTER, byref

from lhandprolib_loader import (
    get_global_lhandpro_lib,
    LER_NONE, LER_PARAMETER, LER_KEY_FUNC_UNINIT, LER_GET_CONFIGURATION,
    LER_DATA_ANOMALY, LER_COMM_CONNECT, LER_COMM_SEND, LER_COMM_RECV,
    LER_COMM_DATA_FORMAT, LER_INVALID_PATH, LER_LOG_SAVE_FAIL, LER_NOT_HOME, LER_UNKNOWN,
    LAC_DOF_6, LAC_DOF_6_S, LAC_DOF_16,
    LCN_ECAT, LCN_CANFD, LCN_RS485,
    LCM_POSITION, LCM_VELOCITY, LCM_TORQUE, LCM_VEL_TOR, LCM_POS_TOR, LCM_HOME,
    LST_STOPPED, LST_RUNNING, LST_ALARM, LST_POS_LIMIT, LST_NEG_LIMIT,
    LST_BOTH_LIMIT, LST_EMG_STOP, LST_HOMING,
    LSS_FINGER_1_1, LSS_FINGER_1_2, LSS_FINGER_2_1, LSS_FINGER_2_2,
    LSS_FINGER_3_1, LSS_FINGER_3_2, LSS_FINGER_4_1, LSS_FINGER_4_2,
    LSS_FINGER_5_1, LSS_FINGER_5_2, LSS_HAND_PALM, LSS_MAX_COUNT,
    LDR_HAND_RIGHT, LDR_HAND_LEFT,
    LogAddCallbackWrapper, ECSendDataCallbackWrapper, CANFDSendDataCallbackWrapper,
    RS485SendDataCallbackWrapper
)


class LHandProLibError(Exception):
    """LHandProLib操作异常"""

    def __init__(self, error_code: int, message: str):
        self.error_code = error_code
        self.message = message
        super().__init__(f"Error {error_code}: {message}")


class PyLHandProLib:
    """LHandProLib的Python封装类"""

    def __init__(self, lib_path: Optional[str] = None):
        """
        初始化LHandPro实例

        Args:
            lib_path: 可选的库文件路径
        """
        self._lib_loader = get_global_lhandpro_lib(lib_path)
        self._lib = self._lib_loader.lib
        self._handle = self._lib.lhandprolib_create()

        if not self._handle:
            raise RuntimeError("无法创建LHandProLib实例")

        # 存储回调引用以避免垃圾回收
        self._callbacks = {}

    def __del__(self):
        """析构函数，确保资源释放"""
        if hasattr(self, '_handle') and self._handle:
            self._lib.lhandprolib_destroy(self._handle)

    def _check_error(self, result: int, operation: str) -> None:
        """检查错误码并抛出异常"""
        if result != LER_NONE:
            error_messages = {
                LER_PARAMETER: "参数错误",
                LER_KEY_FUNC_UNINIT: "关键函数未初始化",
                LER_GET_CONFIGURATION: "读取配置失败",
                LER_DATA_ANOMALY: "数据异常",
                LER_COMM_CONNECT: "通讯连接错误",
                LER_COMM_SEND: "通讯发送错误",
                LER_COMM_RECV: "通讯接收错误",
                LER_COMM_DATA_FORMAT: "通讯数据格式错误",
                LER_INVALID_PATH: "无效的文件路径",
                LER_LOG_SAVE_FAIL: "日志文件保存失败",
                LER_NOT_HOME: "没回零错误",
                LER_UNKNOWN: "未知错误",
            }
            message = error_messages.get(result, f"未知错误码: {result}")
            raise LHandProLibError(result, f"{operation}: {message}")

    # 初始化和关闭
    def initial(self, mode: int) -> None:
        """初始化库"""
        result = self._lib.lhandprolib_initial(self._handle, mode)
        self._check_error(result, "初始化")

    def initial_ex(self, mode: int, node_id: int) -> None:
        """初始化库（扩展版本，包含节点ID）"""
        result = self._lib.lhandprolib_initial_ex(self._handle, mode, node_id)
        self._check_error(result, "initial_ex")

    def close(self) -> None:
        """关闭库"""
        self._lib.lhandprolib_close(self._handle)

    # 回调设置
    def set_send_rpdo_callback(self, callback: Callable[[bytes], bool]) -> None:
        """设置发送RPDO回调"""

        def wrapper(data_ptr, length: int) -> bool:
            data = bytes(data_ptr[:length])
            return callback(data)

        wrapped_callback = ECSendDataCallbackWrapper(wrapper)
        self._callbacks['send_rpdo'] = wrapped_callback
        self._lib.lhandprolib_set_send_rpdo_callback(self._handle, wrapped_callback)

    def set_send_canfd_callback(self, callback: Callable[[int, bytes], bool]) -> None:
        """设置发送CANFD回调"""

        def wrapper(msg_id: int, data_ptr, length: int) -> bool:
            data = bytes(data_ptr[:length])
            return callback(msg_id, data)

        wrapped_callback = CANFDSendDataCallbackWrapper(wrapper)
        self._callbacks['send_canfd'] = wrapped_callback
        self._lib.lhandprolib_set_send_canfd_callback(self._handle, wrapped_callback)

    def set_send_rs485_callback(self, callback: Callable[[bytes], bool]) -> None:
        """设置发送RS485回调"""

        def wrapper(data_ptr, length: int) -> bool:
            data = bytes(data_ptr[:length])
            return callback(data)

        wrapped_callback = RS485SendDataCallbackWrapper(wrapper)
        self._callbacks['send_rs485'] = wrapped_callback
        self._lib.lhandprolib_set_send_rs485_callback(self._handle, wrapped_callback)

    def set_log_callback(self, callback: Callable[[str], None]) -> None:
        """设置日志回调"""

        def wrapper(message: c_char_p) -> None:
            callback(message.decode('utf-8'))

        wrapped_callback = LogAddCallbackWrapper(wrapper)
        self._callbacks['log'] = wrapped_callback
        self._lib.lhandprolib_set_log_callback(self._handle, wrapped_callback)

    # 数据接收处理
    def set_tpdo_data_decode(self, data: bytes) -> int:
        """设置TPDO数据解码"""
        data_array = (c_char * len(data))(*data)
        return self._lib.lhandprolib_set_tpdo_data_decode(self._handle, data_array, len(data))

    def set_canfd_data_decode(self, msg_id: int, data: bytes) -> int:
        """设置CANFD数据解码"""
        data_array = (c_char * len(data))(*data)
        return self._lib.lhandprolib_set_canfd_data_decode(self._handle, c_uint(msg_id), data_array, len(data))

    def set_rs485_data_decode(self, data: bytes) -> int:
        """设置RS485数据解码"""
        data_array = (c_char * len(data))(*data)
        return self._lib.lhandprolib_set_rs485_data_decode(self._handle, data_array, len(data))

    # RPDO数据处理
    def get_pre_send_rpdo_data(self) -> Tuple[bytes, int]:
        """获取预发送RPDO数据"""
        buffer_size = 1024
        data_buffer = (c_char * buffer_size)()
        io_size = c_int(buffer_size)

        result = self._lib.lhandprolib_get_pre_send_rpdo_data(
            self._handle, data_buffer, byref(io_size)
        )
        self._check_error(result, "获取RPDO数据")

        return bytes(data_buffer[:io_size.value]), io_size.value

    def get_pre_send_canfd_data(self) -> Tuple[bytes, int]:
        """获取预发送CANFD数据"""
        buffer_size = 1024
        data_buffer = (c_char * buffer_size)()
        io_size = c_int(buffer_size)

        result = self._lib.lhandprolib_get_pre_send_canfd_data(
            self._handle, data_buffer, byref(io_size)
        )
        self._check_error(result, "获取CANFD数据")

        return bytes(data_buffer[:io_size.value]), io_size.value

    def get_pre_send_rs485_data(self) -> Tuple[bytes, int]:
        """获取预发送RS485数据"""
        buffer_size = 1024
        data_buffer = (c_char * buffer_size)()
        io_size = c_int(buffer_size)

        result = self._lib.lhandprolib_get_pre_send_rs485_data(
            self._handle, data_buffer, byref(io_size)
        )
        self._check_error(result, "获取RS485数据")

        return bytes(data_buffer[:io_size.value]), io_size.value

    # 配置相关
    def set_hand_type(self, hand_type: int) -> None:
        """设置手类型"""
        result = self._lib.lhandprolib_set_hand_type(self._handle, hand_type)
        self._check_error(result, "设置手类型")

    def get_dof(self) -> Tuple[int, int]:
        """获取自由度信息"""
        total = c_int()
        active = c_int()
        result = self._lib.lhandprolib_get_dof(self._handle, byref(total), byref(active))
        self._check_error(result, "获取自由度信息")
        return total.value, active.value

    def set_hand_direction(self, direction: int) -> None:
        """设置手部方向"""
        result = self._lib.lhandprolib_set_hand_direction(self._handle, direction)
        self._check_error(result, "设置手部方向")

    def get_hand_direction(self) -> int:
        """获取手部方向"""
        direction = c_int()
        result = self._lib.lhandprolib_get_hand_direction(self._handle, byref(direction))
        self._check_error(result, "获取手部方向")
        return direction.value

    def get_hand_type(self) -> int:
        """获取手类型"""
        hand_type = c_int()
        result = self._lib.lhandprolib_get_hand_type(self._handle, byref(hand_type))
        self._check_error(result, "获取手类型")
        return hand_type.value

    def set_move_no_home(self, move_no_home: int) -> None:
        """设置是否不回零"""
        result = self._lib.lhandprolib_set_move_no_home(self._handle, move_no_home)
        self._check_error(result, "设置是否不回零")

    # 电机控制
    def set_control_mode(self, motor_id: int, mode: int) -> None:
        """设置控制模式"""
        result = self._lib.lhandprolib_set_control_mode(self._handle, motor_id, mode)
        self._check_error(result, "设置控制模式")

    def get_control_mode(self, motor_id: int) -> int:
        """获取控制模式"""
        mode = c_int()
        result = self._lib.lhandprolib_get_control_mode(self._handle, motor_id, byref(mode))
        self._check_error(result, "获取控制模式")
        return mode.value

    def set_torque_control_mode(self, motor_id: int, mode: int) -> None:
        """设置扭矩控制模式"""
        result = self._lib.lhandprolib_set_torque_control_mode(self._handle, motor_id, mode)
        self._check_error(result, "设置扭矩控制模式")

    def get_torque_control_mode(self, motor_id: int) -> int:
        """获取扭矩控制模式"""
        mode = c_int()
        result = self._lib.lhandprolib_get_torque_control_mode(self._handle, motor_id, byref(mode))
        self._check_error(result, "获取扭矩控制模式")
        return mode.value

    def set_enable(self, motor_id: int, enable: bool) -> None:
        """设置使能状态"""
        result = self._lib.lhandprolib_set_enable(self._handle, motor_id, int(enable))
        self._check_error(result, "设置使能状态")

    def get_enable(self, motor_id: int) -> bool:
        """获取使能状态"""
        enable = c_int()
        result = self._lib.lhandprolib_get_enable(self._handle, motor_id, byref(enable))
        self._check_error(result, "获取使能状态")
        return bool(enable.value)

    def get_position_reached(self, motor_id: int) -> bool:
        """获取位置到达状态"""
        reached = c_int()
        result = self._lib.lhandprolib_get_position_reached(self._handle, motor_id, byref(reached))
        self._check_error(result, "获取位置到达状态")
        return bool(reached.value)

    def get_torque_reached(self, motor_id: int) -> bool:
        """获取力矩到达状态"""
        reached = c_int()
        result = self._lib.lhandprolib_get_torque_reached(self._handle, motor_id, byref(reached))
        self._check_error(result, "获取力矩到达状态")
        return bool(reached.value)

    def set_clear_alarm(self, motor_id: int) -> None:
        """清除报警"""
        result = self._lib.lhandprolib_set_clear_alarm(self._handle, motor_id)
        self._check_error(result, "清除报警")

    def get_now_alarm(self, motor_id: int) -> int:
        """获取当前报警"""
        alarm = c_int()
        result = self._lib.lhandprolib_get_now_alarm(self._handle, motor_id, byref(alarm))
        self._check_error(result, "获取当前报警")
        return alarm.value

    def home_motors(self, motor_id: int) -> None:
        """回零电机"""
        result = self._lib.lhandprolib_home_motors(self._handle, motor_id)
        self._check_error(result, "电机回零")

    # 目标设置
    def set_target_angle(self, motor_id: int, angle: float) -> None:
        """设置目标角度"""
        result = self._lib.lhandprolib_set_target_angle(self._handle, motor_id, c_float(angle))
        self._check_error(result, "设置目标角度")

    def get_target_angle(self, motor_id: int) -> float:
        """获取目标角度"""
        angle = c_float()
        result = self._lib.lhandprolib_get_target_angle(self._handle, motor_id, byref(angle))
        self._check_error(result, "获取目标角度")
        return angle.value

    def set_target_position(self, motor_id: int, position: int) -> None:
        """设置目标位置"""
        result = self._lib.lhandprolib_set_target_position(self._handle, motor_id, position)
        self._check_error(result, "设置目标位置")

    def get_target_position(self, motor_id: int) -> int:
        """获取目标位置"""
        position = c_int()
        result = self._lib.lhandprolib_get_target_position(self._handle, motor_id, byref(position))
        self._check_error(result, "获取目标位置")
        return position.value

    def set_angular_velocity(self, motor_id: int, velocity: float) -> None:
        """设置角速度"""
        result = self._lib.lhandprolib_set_angular_velocity(self._handle, motor_id, c_float(velocity))
        self._check_error(result, "设置角速度")

    def get_angular_velocity(self, motor_id: int) -> float:
        """获取角速度"""
        velocity = c_float()
        result = self._lib.lhandprolib_get_angular_velocity(self._handle, motor_id, byref(velocity))
        self._check_error(result, "获取角速度")
        return velocity.value

    def set_position_velocity(self, motor_id: int, velocity: int) -> None:
        """设置位置速度"""
        result = self._lib.lhandprolib_set_position_velocity(self._handle, motor_id, velocity)
        self._check_error(result, "设置位置速度")

    def get_position_velocity(self, motor_id: int) -> int:
        """获取位置速度"""
        velocity = c_int()
        result = self._lib.lhandprolib_get_position_velocity(self._handle, motor_id, byref(velocity))
        self._check_error(result, "获取位置速度")
        return velocity.value

    def set_max_current(self, motor_id: int, current: int) -> None:
        """设置最大电流"""
        result = self._lib.lhandprolib_set_max_current(self._handle, motor_id, current)
        self._check_error(result, "设置最大电流")

    def get_max_current(self, motor_id: int) -> int:
        """获取最大电流"""
        current = c_int()
        result = self._lib.lhandprolib_get_max_current(self._handle, motor_id, byref(current))
        self._check_error(result, "获取最大电流")
        return current.value

    # 运动控制
    def move_motors(self, motor_id: int) -> None:
        """启动电机运动"""
        result = self._lib.lhandprolib_move_motors(self._handle, motor_id)
        self._check_error(result, "启动电机运动")

    def stop_motors(self, motor_id: int) -> None:
        """停止电机运动"""
        result = self._lib.lhandprolib_stop_motors(self._handle, motor_id)
        self._check_error(result, "停止电机运动")

    # 状态获取
    def get_now_status(self, motor_id: int) -> int:
        """获取当前状态"""
        status = c_int()
        result = self._lib.lhandprolib_get_now_status(self._handle, motor_id, byref(status))
        self._check_error(result, "获取当前状态")
        return status.value

    def get_now_angle(self, motor_id: int) -> float:
        """获取当前角度"""
        angle = c_float()
        result = self._lib.lhandprolib_get_now_angle(self._handle, motor_id, byref(angle))
        self._check_error(result, "获取当前角度")
        return angle.value

    def get_now_position(self, motor_id: int) -> int:
        """获取当前位置"""
        position = c_int()
        result = self._lib.lhandprolib_get_now_position(self._handle, motor_id, byref(position))
        self._check_error(result, "获取当前位置")
        return position.value

    def get_now_angular_velocity(self, motor_id: int) -> float:
        """获取当前角速度"""
        velocity = c_float()
        result = self._lib.lhandprolib_get_now_angular_velocity(self._handle, motor_id, byref(velocity))
        self._check_error(result, "获取当前角速度")
        return velocity.value

    def get_now_position_velocity(self, motor_id: int) -> int:
        """获取当前位置速度"""
        velocity = c_int()
        result = self._lib.lhandprolib_get_now_position_velocity(self._handle, motor_id, byref(velocity))
        self._check_error(result, "获取当前位置速度")
        return velocity.value

    def get_now_current(self, motor_id: int) -> int:
        """获取当前电流"""
        current = c_int()
        result = self._lib.lhandprolib_get_now_current(self._handle, motor_id, byref(current))
        self._check_error(result, "获取当前电流")
        return current.value

    # 触觉传感器
    def set_sensor_enable(self, enable: bool) -> None:
        """设置传感器启用状态"""
        result = self._lib.lhandprolib_set_sensor_enable(self._handle, int(enable))
        self._check_error(result, "设置传感器启用状态")

    def set_sensor_data_format(self, format: int) -> None:
        """设置传感器数据格式"""
        result = self._lib.lhandprolib_set_sensor_data_format(self._handle, format)
        self._check_error(result, "设置传感器数据格式")

    def set_sensor_order(self, order: List[int]) -> None:
        """设置传感器顺序"""
        if len(order) != 6:
            raise ValueError("传感器顺序数组长度必须为6")
        order_array = (c_int * 6)(*order)
        result = self._lib.lhandprolib_set_sensor_order(self._handle, order_array, 6)
        self._check_error(result, "设置传感器顺序")

    def get_finger_sensor_pos(self, sensor_id: int) -> Tuple[List[float], List[float]]:
        """获取手指传感器位置数据"""
        x_ptr = POINTER(c_float)()
        y_ptr = POINTER(c_float)()
        count = c_int()

        result = self._lib.lhandprolib_get_finger_sensor_pos(
            self._handle, sensor_id, byref(x_ptr), byref(y_ptr), byref(count)
        )
        self._check_error(result, "获取手指传感器位置")

        x_values = [x_ptr[i] for i in range(count.value)]
        y_values = [y_ptr[i] for i in range(count.value)]

        return x_values, y_values

    def get_finger_pressure(self, sensor_id: int) -> List[float]:
        """获取手指压力数据"""
        pressure_ptr = POINTER(c_float)()
        count = c_int()

        result = self._lib.lhandprolib_get_finger_pressure(
            self._handle, sensor_id, byref(pressure_ptr), byref(count)
        )
        self._check_error(result, "获取手指压力")

        return [pressure_ptr[i] for i in range(count.value)]

    def set_finger_pressure_reset(self) -> None:
        """重置手指压力"""
        result = self._lib.lhandprolib_set_finger_pressure_reset(self._handle)
        self._check_error(result, "重置手指压力")

    def get_finger_normal_force_ex(self, sensor_id: int) -> List[float]:
        """获取手指法向力数组"""
        force_ptr = POINTER(c_float)()
        count = c_int()

        result = self._lib.lhandprolib_get_finger_normal_force_ex(
            self._handle, sensor_id, byref(force_ptr), byref(count)
        )
        self._check_error(result, "获取手指法向力数组")

        return [force_ptr[i] for i in range(count.value)]

    def get_finger_tangential_force_ex(self, sensor_id: int) -> List[float]:
        """获取手指切向力数组"""
        force_ptr = POINTER(c_float)()
        count = c_int()

        result = self._lib.lhandprolib_get_finger_tangential_force_ex(
            self._handle, sensor_id, byref(force_ptr), byref(count)
        )
        self._check_error(result, "获取手指切向力数组")

        return [force_ptr[i] for i in range(count.value)]

    def get_finger_force_direction_ex(self, sensor_id: int) -> List[float]:
        """获取手指力方向数组"""
        direction_ptr = POINTER(c_float)()
        count = c_int()

        result = self._lib.lhandprolib_get_finger_force_direction_ex(
            self._handle, sensor_id, byref(direction_ptr), byref(count)
        )
        self._check_error(result, "获取手指力方向数组")

        return [direction_ptr[i] for i in range(count.value)]

    def get_finger_proximity_ex(self, sensor_id: int) -> List[float]:
        """获取手指接近度数组"""
        proximity_ptr = POINTER(c_float)()
        count = c_int()

        result = self._lib.lhandprolib_get_finger_proximity_ex(
            self._handle, sensor_id, byref(proximity_ptr), byref(count)
        )
        self._check_error(result, "获取手指接近度数组")

        return [proximity_ptr[i] for i in range(count.value)]

    def get_finger_normal_force(self, sensor_id: int) -> float:
        """获取手指法向力"""
        force = c_float()
        result = self._lib.lhandprolib_get_finger_normal_force(self._handle, sensor_id, byref(force))
        self._check_error(result, "获取手指法向力")
        return force.value

    def get_finger_tangential_force(self, sensor_id: int) -> float:
        """获取手指切向力"""
        force = c_float()
        result = self._lib.lhandprolib_get_finger_tangential_force(self._handle, sensor_id, byref(force))
        self._check_error(result, "获取手指切向力")
        return force.value

    def get_finger_force_direction(self, sensor_id: int) -> float:
        """获取手指力方向"""
        direction = c_float()
        result = self._lib.lhandprolib_get_finger_force_direction(self._handle, sensor_id, byref(direction))
        self._check_error(result, "获取手指力方向")
        return direction.value

    def get_finger_proximity(self, sensor_id: int) -> float:
        """获取手指接近度"""
        proximity = c_float()
        result = self._lib.lhandprolib_get_finger_proximity(self._handle, sensor_id, byref(proximity))
        self._check_error(result, "获取手指接近度")
        return proximity.value

    # 日志管理
    def log_on(self, enable: bool, max_size: int = 1024) -> None:
        """启用/禁用日志"""
        self._lib.lhandprolib_log_on(self._handle, c_bool(enable), max_size)

    def log_save(self, file_name: str) -> None:
        """保存日志到文件"""
        result = self._lib.lhandprolib_log_save(self._handle, file_name.encode('utf-8'))
        self._check_error(result, "保存日志")

    def log_clear(self) -> None:
        """清除日志"""
        self._lib.lhandprolib_log_clear(self._handle)
