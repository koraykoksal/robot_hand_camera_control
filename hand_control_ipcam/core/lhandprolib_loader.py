"""
LHandProLib库加载器和函数原型定义
"""

import ctypes
import sys
from pathlib import Path
from typing import Optional
from ctypes import c_void_p, c_int, c_float, c_bool, c_uint, c_char, c_char_p, POINTER

# 错误码定义
LER_NONE = 0
LER_PARAMETER = 1
LER_KEY_FUNC_UNINIT = 2
LER_GET_CONFIGURATION = 3
LER_DATA_ANOMALY = 4
LER_COMM_CONNECT = 5
LER_COMM_SEND = 6
LER_COMM_RECV = 7
LER_COMM_DATA_FORMAT = 8
LER_INVALID_PATH = 9
LER_LOG_SAVE_FAIL = 10
LER_UNKNOWN = 999

# 自由度枚举
LAC_DOF_6 = 6
LAC_DOF_15 = 15

# 通讯类型枚举
LCN_ECAT = 0
LCN_CAN = 1
LCN_CANFD = 2
LCN_RS485 = 3

# 控制模式枚举
LCM_POSITION = 0
LCM_VELOCITY = 1
LCM_TORQUE = 2
LCM_VEL_TOR = 3
LCM_POS_TOR = 4
LCM_HOME = 5

# 运行状态枚举
LST_STOPPED = 0
LST_RUNNING = 1
LST_ALARM = 2
LST_POS_LIMIT = 3
LST_NEG_LIMIT = 4
LST_BOTH_LIMIT = 5
LST_EMG_STOP = 6
LST_HOMING = 7

# 传感器ID枚举
LSS_FINGER_1_1 = 1
LSS_FINGER_1_2 = 2
LSS_FINGER_2_1 = 3
LSS_FINGER_2_2 = 4
LSS_FINGER_3_1 = 5
LSS_FINGER_3_2 = 6
LSS_FINGER_4_1 = 7
LSS_FINGER_4_2 = 8
LSS_FINGER_5_1 = 9
LSS_FINGER_5_2 = 10
LSS_HAND_PALM = 11
LSS_MAX_COUNT = 12

# 左右手枚举
LDR_HAND_RIGHT = 0
LDR_HAND_LEFT = 1

# 回调函数类型定义
LogAddCallbackWrapper = ctypes.CFUNCTYPE(None, c_char_p)
SendDataCallbackWrapper = ctypes.CFUNCTYPE(c_bool, c_uint, POINTER(c_char), c_uint)
ECSendDataCallbackWrapper = ctypes.CFUNCTYPE(c_bool, POINTER(c_char), c_uint)


class LHandProLibLoader:
    """LHandProLib库加载器"""

    def __init__(self, lib_path: Optional[str] = None):
        """
        初始化库加载器

        Args:
            lib_path: 可选的库文件路径，如果为None则自动查找
        """
        self._lib = None
        self._load_library(lib_path)
        self._define_function_prototypes()

    def _find_library(self) -> Path:
        """查找库文件"""
        # 根据平台确定库文件名
        if sys.platform == "win32":
            lib_name = "LHandProLib.dll"
        elif sys.platform == "darwin":
            lib_name = "libLHandProLib.dylib"
        else:
            lib_name = "libLHandProLib.so"

        # 尝试在常见位置查找库文件
        lib_paths = [
            Path(__file__).parent / lib_name,
            Path(__file__).parent / "thirdParty/bin" / lib_name,
            Path(__file__).parent / "thirdParty/lib" / lib_name,
            Path(__file__).parent / "../../../../bin" / lib_name,
            Path(__file__).parent / "../../../../lib" / lib_name,
            Path(__file__).parent / "../install/bin" / lib_name,
            Path(__file__).parent / "../install/lib" / lib_name,
            Path("/usr/local/lib") / lib_name,
            Path("/usr/lib") / lib_name,
        ]

        for path in lib_paths:
            if path.exists():
                return path

        raise FileNotFoundError(f"无法找到库文件 {lib_name}")

    def _load_library(self, lib_path: Optional[str] = None):
        """加载动态库"""
        if lib_path is None:
            lib_path = self._find_library()
        else:
            lib_path = Path(lib_path)

        if not lib_path.exists():
            raise FileNotFoundError(f"指定的库文件不存在: {lib_path}")

        try:
            self._lib = ctypes.CDLL(str(lib_path))
        except Exception as e:
            raise RuntimeError(f"加载库失败: {e}") from e

    def _define_function_prototypes(self):
        """定义C函数的原型"""
        # 创建和销毁
        self._lib.lhandprolib_create.restype = c_void_p
        self._lib.lhandprolib_create.argtypes = []

        self._lib.lhandprolib_destroy.restype = None
        self._lib.lhandprolib_destroy.argtypes = [c_void_p]

        # 初始化和关闭
        self._lib.lhandprolib_initial.restype = c_int
        self._lib.lhandprolib_initial.argtypes = [c_void_p, c_int]

        self._lib.lhandprolib_close.restype = None
        self._lib.lhandprolib_close.argtypes = [c_void_p]

        # 回调设置
        self._lib.lhandprolib_set_send_rpdo_callback.restype = None
        self._lib.lhandprolib_set_send_rpdo_callback.argtypes = [c_void_p, ECSendDataCallbackWrapper]

        self._lib.lhandprolib_set_log_callback.restype = None
        self._lib.lhandprolib_set_log_callback.argtypes = [c_void_p, LogAddCallbackWrapper]

        # 数据接收处理
        self._lib.lhandprolib_set_tpdo_data_decode.restype = c_int
        self._lib.lhandprolib_set_tpdo_data_decode.argtypes = [c_void_p, POINTER(c_char), c_int]

        # RPDO数据处理
        self._lib.lhandprolib_get_pre_send_rpdo_data.restype = c_int
        self._lib.lhandprolib_get_pre_send_rpdo_data.argtypes = [c_void_p, POINTER(c_char), POINTER(c_int)]

        # 配置相关
        self._lib.lhandprolib_set_dof_type.restype = c_int
        self._lib.lhandprolib_set_dof_type.argtypes = [c_void_p, c_int]

        self._lib.lhandprolib_get_dof.restype = c_int
        self._lib.lhandprolib_get_dof.argtypes = [c_void_p, POINTER(c_int), POINTER(c_int)]

        self._lib.lhandprolib_set_hand_direction.restype = c_int
        self._lib.lhandprolib_set_hand_direction.argtypes = [c_void_p, c_int]

        self._lib.lhandprolib_get_hand_direction.restype = c_int
        self._lib.lhandprolib_get_hand_direction.argtypes = [c_void_p, POINTER(c_int)]

        # 电机控制相关函数
        self._define_motor_control_prototypes()
        # 目标设置相关函数
        self._define_target_setting_prototypes()
        # 运动控制相关函数
        self._define_motion_control_prototypes()
        # 状态获取相关函数
        self._define_status_getting_prototypes()
        # 触觉传感器相关函数
        self._define_tactile_sensor_prototypes()
        # 日志管理相关函数
        self._define_log_management_prototypes()

    def _define_motor_control_prototypes(self):
        """定义电机控制相关函数原型"""
        functions = [
            ('lhandprolib_set_control_mode', [c_void_p, c_int, c_int]),
            ('lhandprolib_get_control_mode', [c_void_p, c_int, POINTER(c_int)]),
            ('lhandprolib_set_enable', [c_void_p, c_int, c_int]),
            ('lhandprolib_get_enable', [c_void_p, c_int, POINTER(c_int)]),
            ('lhandprolib_get_position_reached', [c_void_p, c_int, POINTER(c_int)]),
            ('lhandprolib_set_clear_alarm', [c_void_p, c_int]),
            ('lhandprolib_get_now_alarm', [c_void_p, c_int, POINTER(c_int)]),
            ('lhandprolib_home_motors', [c_void_p, c_int]),
        ]

        for func_name, argtypes in functions:
            func = getattr(self._lib, func_name)
            func.restype = c_int
            func.argtypes = argtypes

    def _define_target_setting_prototypes(self):
        """定义目标设置相关函数原型"""
        functions = [
            ('lhandprolib_set_target_angle', [c_void_p, c_int, c_float]),
            ('lhandprolib_get_target_angle', [c_void_p, c_int, POINTER(c_float)]),
            ('lhandprolib_set_target_position', [c_void_p, c_int, c_int]),
            ('lhandprolib_get_target_position', [c_void_p, c_int, POINTER(c_int)]),
            ('lhandprolib_set_angular_velocity', [c_void_p, c_int, c_float]),
            ('lhandprolib_get_angular_velocity', [c_void_p, c_int, POINTER(c_float)]),
            ('lhandprolib_set_position_velocity', [c_void_p, c_int, c_int]),
            ('lhandprolib_get_position_velocity', [c_void_p, c_int, POINTER(c_int)]),
            ('lhandprolib_set_max_current', [c_void_p, c_int, c_int]),
            ('lhandprolib_get_max_current', [c_void_p, c_int, POINTER(c_int)]),
        ]

        for func_name, argtypes in functions:
            func = getattr(self._lib, func_name)
            func.restype = c_int
            func.argtypes = argtypes

    def _define_motion_control_prototypes(self):
        """定义运动控制相关函数原型"""
        functions = [
            ('lhandprolib_move_motors', [c_void_p, c_int]),
            ('lhandprolib_stop_motors', [c_void_p, c_int]),
        ]

        for func_name, argtypes in functions:
            func = getattr(self._lib, func_name)
            func.restype = c_int
            func.argtypes = argtypes

    def _define_status_getting_prototypes(self):
        """定义状态获取相关函数原型"""
        functions = [
            ('lhandprolib_get_now_status', [c_void_p, c_int, POINTER(c_int)]),
            ('lhandprolib_get_now_angle', [c_void_p, c_int, POINTER(c_float)]),
            ('lhandprolib_get_now_position', [c_void_p, c_int, POINTER(c_int)]),
            ('lhandprolib_get_now_angular_velocity', [c_void_p, c_int, POINTER(c_float)]),
            ('lhandprolib_get_now_position_velocity', [c_void_p, c_int, POINTER(c_int)]),
            ('lhandprolib_get_now_current', [c_void_p, c_int, POINTER(c_int)]),
        ]

        for func_name, argtypes in functions:
            func = getattr(self._lib, func_name)
            func.restype = c_int
            func.argtypes = argtypes

    def _define_tactile_sensor_prototypes(self):
        """定义触觉传感器相关函数原型"""
        # 注意：这些函数返回数组，需要特殊处理
        self._lib.lhandprolib_get_finger_sensor_pos.restype = c_int
        self._lib.lhandprolib_get_finger_sensor_pos.argtypes = [
            c_void_p, c_int, POINTER(POINTER(c_float)), POINTER(POINTER(c_float)), POINTER(c_int)
        ]

        self._lib.lhandprolib_get_finger_pressure.restype = c_int
        self._lib.lhandprolib_get_finger_pressure.argtypes = [
            c_void_p, c_int, POINTER(POINTER(c_float)), POINTER(c_int)
        ]

        functions = [
            ('lhandprolib_set_finger_pressure_reset', [c_void_p]),
            ('lhandprolib_get_finger_normal_force', [c_void_p, c_int, POINTER(c_float)]),
            ('lhandprolib_get_finger_tangential_force', [c_void_p, c_int, POINTER(c_float)]),
            ('lhandprolib_get_finger_force_direction', [c_void_p, c_int, POINTER(c_float)]),
            ('lhandprolib_get_finger_proximity', [c_void_p, c_int, POINTER(c_float)]),
        ]

        for func_name, argtypes in functions:
            func = getattr(self._lib, func_name)
            func.restype = c_int
            func.argtypes = argtypes

    def _define_log_management_prototypes(self):
        """定义日志管理相关函数原型"""
        self._lib.lhandprolib_log_on.restype = None
        self._lib.lhandprolib_log_on.argtypes = [c_void_p, c_bool, c_int]

        self._lib.lhandprolib_log_save.restype = c_int
        self._lib.lhandprolib_log_save.argtypes = [c_void_p, c_char_p]

        self._lib.lhandprolib_log_clear.restype = None
        self._lib.lhandprolib_log_clear.argtypes = [c_void_p]

    @property
    def lib(self):
        """获取底层的ctypes库对象"""
        return self._lib


# 全局单例实例
_global_lhandpro_lib = None


def get_global_lhandpro_lib(lib_path: Optional[str] = None) -> LHandProLibLoader:
    """
    获取全局单例LHandProLib实例

    Args:
        lib_path: 可选的库文件路径，仅在第一次调用时有效

    Returns:
        全局LHandProLibLoader实例
    """
    global _global_lhandpro_lib
    if _global_lhandpro_lib is None:
        _global_lhandpro_lib = LHandProLibLoader(lib_path)
    return _global_lhandpro_lib