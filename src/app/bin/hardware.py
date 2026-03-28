"""
硬件抽象层 — 与 /sys/class/hwmon 交互

职责：
- 动态探测 hwmon 路径（通用扫描，不绑定特定芯片）
- 读取 CPU 温度、硬盘温度、风扇转速、PWM 值
- 写入 PWM 值（含下限保护）
- 设置 PWM 控制模式
- 恢复安全状态

通用控制原则（Linux hwmon 内核规范）：
- pwm_enable=1 — 手动模式，所有芯片一致
- pwm_enable=2 — 安全恢复值（芯片自动或全速，均安全）
"""

import glob
import logging
import os

logger = logging.getLogger(__name__)

# PWM 绝对下限：约 10%，即使用户配置 min=0 也不低于此值
ABSOLUTE_MIN_PWM = 26

# pwm_enable 通用值（Linux hwmon 内核规范，所有芯片一致）
PWM_ENABLE_MANUAL = 1   # 手动控制：软件写 pwm 值
PWM_ENABLE_SAFE = 2     # 安全恢复：归还芯片自动控制

# 已知芯片的显示名称（仅用于 UI 展示，不影响控制逻辑）
CHIP_DISPLAY_NAMES = {
    "it8772": "ITE IT8772E",
    "it8786": "ITE IT8786E",
    "nct6776": "Nuvoton NCT6776",
    "nct6687": "Nuvoton NCT6687",
    "nct6775": "Nuvoton NCT6775",
    "f71882fg": "Fintek F71882FG",
}


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
    """硬件抽象层，封装所有 /sys/class/hwmon 读写操作

    通用设计：不依赖特定芯片型号，只要有 pwm 文件就能控制。
    """

    HWMON_BASE = "/sys/class/hwmon"

    def __init__(self):
        # 探测到的 PWM 芯片列表
        # 每项结构：{
        #   "name": "it8772",              # hwmon/name 原始值
        #   "display_name": "ITE IT8772E", # UI 友好名称
        #   "hwmon_path": "/sys/class/hwmon/hwmon3",
        #   "pwm_channels": ["pwm1", "pwm2"],
        #   "fan_inputs": {"pwm2": "/sys/.../fan2_input"},
        # }
        self.chips: list[dict] = []

        # 温度传感器路径
        self.coretemp_base: str | None = None
        self.drivetemp_paths: dict[str, str] = {}  # {"sda": "/sys/.../temp1_input"}

        # 汇总所有芯片的通道（方便外部访问）
        self.available_pwm: list[str] = []
        self.available_fans: dict[str, str] = {}

        # 安全状态追踪
        self._last_valid_temp: float = 50.0
        self._read_fail_count: int = 0
        self._max_read_failures: int = 3

    @property
    def hw_detected(self) -> bool:
        """是否探测到可控制的 PWM 芯片"""
        return len(self.chips) > 0

    def detect_hwmon_paths(self) -> bool:
        """动态探测 hwmon 路径

        通用扫描：不匹配芯片型号，任何有 pwm 文件的 hwmon 设备都可控制。

        Returns:
            True 表示探测到至少一个 PWM 芯片，可接管风扇控制
            False 表示未找到 PWM 芯片，仅温度监控
        """
        self.chips = []
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

            # 温度传感器探测
            if name == "coretemp":
                self.coretemp_base = hwmon_dir
                logger.info("探测到 coretemp: %s", hwmon_dir)
                continue

            if name == "drivetemp":
                self._detect_drivetemp(hwmon_dir)
                continue

            # PWM 芯片探测：有 pwm 文件就能控制
            pwm_channels, fan_map = self._detect_pwm_channels(hwmon_dir)
            if pwm_channels:
                display_name = CHIP_DISPLAY_NAMES.get(name, name)
                chip_info = {
                    "name": name,
                    "display_name": display_name,
                    "hwmon_path": hwmon_dir,
                    "pwm_channels": pwm_channels,
                    "fan_inputs": fan_map,
                }
                self.chips.append(chip_info)
                self.available_pwm.extend(pwm_channels)
                self.available_fans.update(fan_map)

                logger.info(
                    "探测到 %s (%s): %s, PWM=%s",
                    display_name, name, hwmon_dir, pwm_channels,
                )

        if not self.chips:
            logger.warning("未探测到 PWM 控制芯片，无法接管风扇控制")
            return False

        if self.coretemp_base is None:
            logger.warning("未探测到 coretemp，CPU 温度不可用")

        logger.info(
            "硬件探测完成: 芯片=%d, PWM 通道=%s, 硬盘温度=%s",
            len(self.chips), self.available_pwm,
            list(self.drivetemp_paths.keys()),
        )
        return True

    def _detect_pwm_channels(self, hwmon_dir: str) -> tuple[list[str], dict[str, str]]:
        """扫描 hwmon 目录下可用的 PWM 和风扇通道

        Returns:
            (pwm_channels, fan_map)
        """
        pwm_channels = []
        fan_map = {}

        for i in range(1, 9):
            pwm_file = os.path.join(hwmon_dir, f"pwm{i}")
            if not os.path.exists(pwm_file):
                continue

            pwm_name = f"pwm{i}"
            pwm_channels.append(pwm_name)

            fan_file = os.path.join(hwmon_dir, f"fan{i}_input")
            if os.path.exists(fan_file):
                rpm = self._read_int_file(fan_file)
                if rpm is not None and rpm > 0:
                    fan_map[pwm_name] = fan_file
                    logger.info("  %s → fan%d_input (%d RPM)", pwm_name, i, rpm)
                else:
                    logger.info("  %s → fan%d_input (无读数或为 0)", pwm_name, i)

        return pwm_channels, fan_map

    def _detect_drivetemp(self, hwmon_dir: str):
        """探测 drivetemp 硬盘温度传感器，关联磁盘名"""
        device_link = os.path.join(hwmon_dir, "device")
        if os.path.islink(device_link):
            device_path = os.path.realpath(device_link)
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

        temp_file = os.path.join(hwmon_dir, "temp1_input")
        if os.path.exists(temp_file):
            key = os.path.basename(hwmon_dir)
            self.drivetemp_paths[key] = temp_file
            logger.info("探测到 drivetemp: %s → %s（无法关联磁盘名）", key, hwmon_dir)

    # ── 芯片路由 ─────────────────────────────────────────────

    def _find_chip_for_channel(self, channel: str) -> dict | None:
        """根据 PWM 通道名找到所属芯片信息"""
        for chip in self.chips:
            if channel in chip["pwm_channels"]:
                return chip
        return None

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

        if temp < 0 or temp > 120:
            logger.warning("CPU 温度异常值 %.1f°C，丢弃，使用上次有效值 %.1f°C", temp, self._last_valid_temp)
            return self._last_valid_temp

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

    # ── 风扇 / PWM 读取 ─────────────────────────────────────

    def read_fan_rpm(self, fan_channel: str = "pwm2") -> int | None:
        """读取指定通道对应风扇的转速"""
        fan_file = self.available_fans.get(fan_channel)
        if fan_file is None:
            return None
        return self._read_int_file(fan_file)

    def read_pwm(self, channel: str = "pwm2") -> int | None:
        """读取指定 PWM 通道的当前值 (0-255)"""
        chip = self._find_chip_for_channel(channel)
        if chip is None:
            return None
        pwm_file = os.path.join(chip["hwmon_path"], channel)
        return self._read_int_file(pwm_file)

    def read_pwm_enable(self, channel: str = "pwm2") -> int | None:
        """读取指定 PWM 通道的控制模式"""
        chip = self._find_chip_for_channel(channel)
        if chip is None:
            return None
        enable_file = os.path.join(chip["hwmon_path"], f"{channel}_enable")
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
        """重置读取失败计数器"""
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
        chip = self._find_chip_for_channel(channel)
        if chip is None:
            logger.error("通道 %s 无对应芯片，无法写入 PWM", channel)
            return False

        safe_value = safe_pwm_value(value, min_percent)
        pwm_file = os.path.join(chip["hwmon_path"], channel)
        return self._write_file(pwm_file, str(safe_value))

    def set_pwm_mode(self, mode: int, channel: str = "pwm2") -> bool:
        """设置 PWM 控制模式

        Args:
            mode: pwm_enable 值（通常 1=手动, 2=安全恢复）
            channel: PWM 通道名

        Returns:
            True 写入成功，False 写入失败
        """
        chip = self._find_chip_for_channel(channel)
        if chip is None:
            logger.error("通道 %s 无对应芯片，无法设置 PWM 模式", channel)
            return False

        enable_file = os.path.join(chip["hwmon_path"], f"{channel}_enable")
        return self._write_file(enable_file, str(mode))

    def restore_safe_state(self):
        """恢复所有 PWM 通道为安全状态 (pwm_enable=2)

        通用逻辑：遍历所有 hwmon 设备，对每个可写的 pwm*_enable
        写入 2（芯片自动控制或全速，均安全）。

        在以下时机调用：
        - 启动前（清除上次崩溃残留）
        - 正常退出时
        - 异常降级时
        - 卸载时
        """
        hwmon_dirs = sorted(glob.glob(os.path.join(self.HWMON_BASE, "hwmon*")))
        restored = False

        for hwmon_dir in hwmon_dirs:
            enable_files = sorted(glob.glob(os.path.join(hwmon_dir, "pwm*_enable")))
            for enable_file in enable_files:
                if self._write_file(enable_file, str(PWM_ENABLE_SAFE)):
                    logger.info("已恢复 %s = %d", enable_file, PWM_ENABLE_SAFE)
                    restored = True

        if not restored:
            logger.warning("restore_safe_state: 未找到任何 PWM 通道")

    # ── 硬件信息（供 API 使用）────────────────────────────────

    def get_hardware_info(self) -> dict:
        """返回硬件探测结果摘要"""
        chips_info = []
        for chip in self.chips:
            chips_info.append({
                "name": chip["name"],
                "display_name": chip["display_name"],
                "hwmon_path": chip["hwmon_path"],
                "pwm_channels": chip["pwm_channels"],
                "fan_inputs": list(chip["fan_inputs"].keys()),
            })

        temp_sensors = {}
        if self.coretemp_base:
            cpu_temp = self.read_cpu_temp()
            temp_sensors["cpu"] = {"type": "coretemp", "current": cpu_temp}
        for disk_name, temp_file in self.drivetemp_paths.items():
            raw = self._read_int_file(temp_file)
            current = raw / 1000.0 if raw is not None else None
            temp_sensors[f"disk_{disk_name}"] = {"type": "drivetemp", "current": current}

        return {"chips": chips_info, "temp_sensors": temp_sensors}

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
