"""
硬件抽象层 — 与 /sys/class/hwmon 交互

职责：
- 动态探测 hwmon 路径（it8772、coretemp、drivetemp）
- 读取 CPU 温度、硬盘温度、风扇转速、PWM 值
- 写入 PWM 值（含下限保护）
- 设置 PWM 控制模式
- 恢复安全状态
"""

import glob
import logging
import os

logger = logging.getLogger(__name__)

# PWM 绝对下限：约 10%，即使用户配置 min=0 也不低于此值
ABSOLUTE_MIN_PWM = 26


def safe_pwm_value(target: int, min_percent: int) -> int:
    """计算安全的 PWM 值，强制不低于下限

    Args:
        target: 目标 PWM 值 (0-255)
        min_percent: 用户配置的最低转速百分比 (0-100)

    Returns:
        经过下限保护的 PWM 值 (ABSOLUTE_MIN_PWM-255)
    """
    min_value = int(255 * min_percent / 100)
    return max(target, min_value, ABSOLUTE_MIN_PWM)


class Hardware:
    """硬件抽象层，封装所有 /sys/class/hwmon 读写操作"""

    HWMON_BASE = "/sys/class/hwmon"

    def __init__(self):
        # 动态探测的路径（启动时填充）
        self.it8772_base: str | None = None
        self.coretemp_base: str | None = None
        self.drivetemp_paths: dict[str, str] = {}  # {"sda": "/sys/class/hwmon/hwmonX"}

        # 可用的 PWM 和风扇通道（探测时填充）
        self.available_pwm: list[str] = []    # ["pwm1", "pwm2", "pwm3"]
        self.available_fans: dict[str, str] = {}  # {"pwm2": "fan2_input"}

        # 安全状态追踪
        self._last_valid_temp: float = 50.0  # 上次有效温度（异常回退用）
        self._read_fail_count: int = 0       # 连续读取失败计数
        self._max_read_failures: int = 3     # 触发全速的失败次数

    def detect_hwmon_paths(self) -> bool:
        """动态探测 hwmon 路径，通过 name 文件匹配芯片类型

        Returns:
            True 表示关键传感器 (it8772) 探测成功，可接管风扇控制
            False 表示探测失败，应保持默认模式
        """
        self.it8772_base = None
        self.coretemp_base = None
        self.drivetemp_paths = {}
        self.available_pwm = []
        self.available_fans = {}

        hwmon_dirs = sorted(glob.glob(os.path.join(self.HWMON_BASE, "hwmon*")))

        for hwmon_dir in hwmon_dirs:
            name_file = os.path.join(hwmon_dir, "name")
            name = self._read_file(name_file)
            if name is None:
                continue

            if name == "it8772":
                self.it8772_base = hwmon_dir
                self._detect_pwm_channels(hwmon_dir)
                logger.info("探测到 IT8772: %s", hwmon_dir)

            elif name == "coretemp":
                self.coretemp_base = hwmon_dir
                logger.info("探测到 coretemp: %s", hwmon_dir)

            elif name == "drivetemp":
                self._detect_drivetemp(hwmon_dir)

        if self.it8772_base is None:
            logger.warning("未探测到 IT8772 传感器芯片，无法接管风扇控制")
            return False

        if self.coretemp_base is None:
            logger.warning("未探测到 coretemp，CPU 温度不可用")

        logger.info(
            "硬件探测完成: PWM 通道=%s, 风扇=%s, 硬盘温度=%s",
            self.available_pwm,
            list(self.available_fans.keys()),
            list(self.drivetemp_paths.keys()),
        )
        return True

    def _detect_pwm_channels(self, hwmon_dir: str):
        """扫描 IT8772 下可用的 PWM 和风扇通道"""
        for i in range(1, 4):
            pwm_file = os.path.join(hwmon_dir, f"pwm{i}")
            if os.path.exists(pwm_file):
                pwm_name = f"pwm{i}"
                self.available_pwm.append(pwm_name)

                # 检查对应风扇是否有读数
                fan_file = os.path.join(hwmon_dir, f"fan{i}_input")
                if os.path.exists(fan_file):
                    rpm = self._read_int_file(fan_file)
                    if rpm is not None and rpm > 0:
                        self.available_fans[pwm_name] = fan_file
                        logger.info("  %s → fan%d_input (%d RPM)", pwm_name, i, rpm)
                    else:
                        logger.info("  %s → fan%d_input (无读数或为 0)", pwm_name, i)

    def _detect_drivetemp(self, hwmon_dir: str):
        """探测 drivetemp 硬盘温度传感器，关联磁盘名"""
        # 通过 device 符号链接反查磁盘名
        device_link = os.path.join(hwmon_dir, "device")
        if os.path.islink(device_link):
            device_path = os.path.realpath(device_link)
            # 尝试从 block 子目录获取磁盘名
            block_dir = os.path.join(device_path, "block")
            if os.path.isdir(block_dir):
                disks = os.listdir(block_dir)
                if disks:
                    disk_name = disks[0]
                    temp_file = os.path.join(hwmon_dir, "temp1_input")
                    if os.path.exists(temp_file):
                        self.drivetemp_paths[disk_name] = temp_file
                        logger.info("探测到 drivetemp: %s → %s", disk_name, hwmon_dir)
                        return

        # 回退：无法关联磁盘名时用 hwmon 编号命名
        temp_file = os.path.join(hwmon_dir, "temp1_input")
        if os.path.exists(temp_file):
            key = os.path.basename(hwmon_dir)
            self.drivetemp_paths[key] = temp_file
            logger.info("探测到 drivetemp: %s → %s（无法关联磁盘名）", key, hwmon_dir)

    # ── 温度读取 ─────────────────────────────────────────────

    def read_cpu_temp(self) -> float | None:
        """读取 CPU Package 温度

        Returns:
            摄氏度浮点数，异常时返回上次有效值，
            传感器路径不存在返回 None
        """
        if self.coretemp_base is None:
            return None

        temp_file = os.path.join(self.coretemp_base, "temp1_input")
        raw = self._read_int_file(temp_file)

        if raw is None:
            self._read_fail_count += 1
            logger.warning("CPU 温度读取失败 (连续 %d 次)", self._read_fail_count)
            return self._last_valid_temp

        temp = raw / 1000.0

        # 异常值过滤
        if temp < 0 or temp > 120:
            logger.warning("CPU 温度异常值 %.1f°C，丢弃，使用上次有效值 %.1f°C", temp, self._last_valid_temp)
            return self._last_valid_temp

        # 有效读取，重置计数器
        self._read_fail_count = 0
        self._last_valid_temp = temp
        return temp

    def read_disk_temps(self) -> dict[str, float]:
        """读取所有硬盘温度

        Returns:
            {"sda": 35.0, "sdb": 38.0}，读取失败的磁盘不包含在内
        """
        temps = {}
        for disk_name, temp_file in self.drivetemp_paths.items():
            raw = self._read_int_file(temp_file)
            if raw is not None:
                temp = raw / 1000.0
                if 0 <= temp <= 120:
                    temps[disk_name] = temp
        return temps

    # ── 风扇读取 ─────────────────────────────────────────────

    def read_fan_rpm(self, fan_channel: str = "pwm2") -> int | None:
        """读取指定通道对应风扇的转速

        Args:
            fan_channel: PWM 通道名（如 "pwm2"），自动映射到 fan_input

        Returns:
            RPM 整数，失败返回 None
        """
        fan_file = self.available_fans.get(fan_channel)
        if fan_file is None:
            return None
        return self._read_int_file(fan_file)

    def read_pwm(self, channel: str = "pwm2") -> int | None:
        """读取指定 PWM 通道的当前值

        Args:
            channel: PWM 通道名（如 "pwm2"）

        Returns:
            PWM 值 (0-255)，失败返回 None
        """
        if self.it8772_base is None:
            return None
        pwm_file = os.path.join(self.it8772_base, channel)
        return self._read_int_file(pwm_file)

    def read_pwm_enable(self, channel: str = "pwm2") -> int | None:
        """读取指定 PWM 通道的控制模式

        Returns:
            0=全速, 1=手动, 2=自动，失败返回 None
        """
        if self.it8772_base is None:
            return None
        enable_file = os.path.join(self.it8772_base, f"{channel}_enable")
        return self._read_int_file(enable_file)

    @property
    def read_fail_count(self) -> int:
        """当前连续读取失败次数"""
        return self._read_fail_count

    @property
    def is_read_failure_critical(self) -> bool:
        """连续读取失败次数是否达到临界值"""
        return self._read_fail_count >= self._max_read_failures

    def reset_read_fail_count(self):
        """重置读取失败计数器，在模式切换或降级后调用"""
        self._read_fail_count = 0

    # ── PWM 写入 ─────────────────────────────────────────────

    def write_pwm(self, value: int, channel: str = "pwm2", min_percent: int = 25) -> bool:
        """写入 PWM 值，强制经过下限保护

        Args:
            value: 目标 PWM 值 (0-255)
            channel: PWM 通道名
            min_percent: 最低转速百分比

        Returns:
            True 写入成功，False 写入失败
        """
        if self.it8772_base is None:
            logger.error("IT8772 未探测到，无法写入 PWM")
            return False

        safe_value = safe_pwm_value(value, min_percent)
        pwm_file = os.path.join(self.it8772_base, channel)
        return self._write_file(pwm_file, str(safe_value))

    def set_pwm_mode(self, mode: int, channel: str = "pwm2") -> bool:
        """设置 PWM 控制模式

        Args:
            mode: 0=全速, 1=手动, 2=自动
            channel: PWM 通道名

        Returns:
            True 写入成功，False 写入失败
        """
        if self.it8772_base is None:
            logger.error("IT8772 未探测到，无法设置 PWM 模式")
            return False

        if mode not in (0, 1, 2):
            logger.error("无效的 PWM 模式: %d", mode)
            return False

        enable_file = os.path.join(self.it8772_base, f"{channel}_enable")
        return self._write_file(enable_file, str(mode))

    def restore_safe_state(self):
        """恢复所有 IT8772 PWM 通道为自动模式 (pwm_enable=2)

        在以下时机调用：
        - 启动前（清除上次崩溃残留）
        - 正常退出时
        - 异常降级时
        - 卸载时
        """
        # 不依赖 self.it8772_base，直接扫描所有 hwmon
        hwmon_dirs = glob.glob(os.path.join(self.HWMON_BASE, "hwmon*"))
        restored = False

        for hwmon_dir in hwmon_dirs:
            name = self._read_file(os.path.join(hwmon_dir, "name"))
            if name != "it8772":
                continue

            for i in range(1, 4):
                enable_file = os.path.join(hwmon_dir, f"pwm{i}_enable")
                if os.path.exists(enable_file):
                    if self._write_file(enable_file, "2"):
                        logger.info("已恢复 %s/pwm%d_enable = 2", hwmon_dir, i)
                        restored = True
                    else:
                        logger.error("恢复 %s/pwm%d_enable 失败", hwmon_dir, i)

        if not restored:
            logger.warning("restore_safe_state: 未找到任何 IT8772 PWM 通道")

    # ── 底层文件读写 ─────────────────────────────────────────

    @staticmethod
    def _read_file(path: str) -> str | None:
        """读取 sysfs 文件内容，返回去除空白的字符串，失败返回 None"""
        try:
            with open(path, "r") as f:
                return f.read().strip()
        except (OSError, IOError):
            return None

    @staticmethod
    def _read_int_file(path: str) -> int | None:
        """读取 sysfs 文件内容并解析为整数，失败返回 None"""
        try:
            with open(path, "r") as f:
                return int(f.read().strip())
        except (OSError, IOError, ValueError):
            return None

    @staticmethod
    def _write_file(path: str, value: str) -> bool:
        """写入 sysfs 文件，返回是否成功"""
        try:
            with open(path, "w") as f:
                f.write(value)
            return True
        except (OSError, IOError) as e:
            logger.error("写入 %s 失败: %s", path, e)
            return False
