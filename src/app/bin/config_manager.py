"""
配置管理 — 加载、校验、保存、线程安全读写

配置文件路径：$TRIM_PKGETC/config.json
"""

import copy
import json
import logging
import os
import threading

logger = logging.getLogger(__name__)

DEFAULT_CONFIG = {
    "mode": "default",
    "poll_interval": 2,
    "min_pwm_percent": 20,
    "temp_source": "cpu",
    "manual_pwm_percent": 50,
    "curve": [
        {"temp": 30, "pwm_percent": 20},
        {"temp": 40, "pwm_percent": 30},
        {"temp": 50, "pwm_percent": 45},
        {"temp": 60, "pwm_percent": 65},
        {"temp": 70, "pwm_percent": 85},
        {"temp": 80, "pwm_percent": 100},
    ],
    "fan_channel": "pwm2",
    "web_port": 9511,
}

VALID_MODES = ("default", "auto", "manual", "full")
VALID_TEMP_SOURCES = ("cpu", "disk", "max")

# 默认模式使用的保守曲线（不可用户编辑，比自动模式更宽松）
DEFAULT_SAFE_CURVE = [
    {"temp": 30, "pwm_percent": 25},
    {"temp": 45, "pwm_percent": 35},
    {"temp": 55, "pwm_percent": 50},
    {"temp": 65, "pwm_percent": 70},
    {"temp": 75, "pwm_percent": 90},
    {"temp": 80, "pwm_percent": 100},
]

# 绝对下限���分比，与 hardware.py 的 ABSOLUTE_MIN_PWM 对应
ABSOLUTE_MIN_PERCENT = 10


def _validate_mode(value) -> str:
    if isinstance(value, str) and value in VALID_MODES:
        return value
    logger.warning("mode 校验失败: %r，回���默认值", value)
    return DEFAULT_CONFIG["mode"]


def _validate_int_range(value, min_val: int, max_val: int, default: int, name: str) -> int:
    try:
        v = int(value)
        if min_val <= v <= max_val:
            return v
    except (TypeError, ValueError):
        pass
    logger.warning("%s 校验失败: %r (范围 %d-%d)，回退默认值 %d", name, value, min_val, max_val, default)
    return default


def _validate_curve(value) -> list[dict]:
    """校验温控曲线：2-10 个节点，temp 0-120 递增不重复，pwm_percent 10-100"""
    default = DEFAULT_CONFIG["curve"]

    if not isinstance(value, list) or not (2 <= len(value) <= 10):
        logger.warning("curve 校验失败: 节点数量不合法 (%r)，回退默认曲线", type(value).__name__)
        return copy.deepcopy(default)

    validated = []
    prev_temp = -1

    for i, node in enumerate(value):
        if not isinstance(node, dict):
            logger.warning("curve 节点 %d 格式错误，回退默认曲线", i)
            return copy.deepcopy(default)

        try:
            temp = int(node.get("temp", -1))
            pwm_percent = int(node.get("pwm_percent", -1))
        except (TypeError, ValueError):
            logger.warning("curve 节点 %d 值类型错误，回退默认曲线", i)
            return copy.deepcopy(default)

        if not (0 <= temp <= 120):
            logger.warning("curve 节点 %d 温度越界: %d，回退默认曲线", i, temp)
            return copy.deepcopy(default)

        if temp <= prev_temp:
            logger.warning("curve 节点 %d 温度未递增: %d <= %d，回退默认曲线", i, temp, prev_temp)
            return copy.deepcopy(default)

        if not (ABSOLUTE_MIN_PERCENT <= pwm_percent <= 100):
            logger.warning("curve 节点 %d pwm_percent 越界: %d，回退默认曲线", i, pwm_percent)
            return copy.deepcopy(default)

        prev_temp = temp
        validated.append({"temp": temp, "pwm_percent": pwm_percent})

    return validated


def _validate_temp_source(value) -> str:
    if isinstance(value, str) and value in VALID_TEMP_SOURCES:
        return value
    logger.warning("temp_source 校验失败: %r，回退默认值", value)
    return DEFAULT_CONFIG["temp_source"]


def _validate_fan_channel(value, available_pwm: list[str] | None = None) -> str:
    if isinstance(value, str) and value.startswith("pwm"):
        if available_pwm is None or value in available_pwm:
            return value
    logger.warning("fan_channel 校验失败: %r，回退默认值", value)
    return DEFAULT_CONFIG["fan_channel"]


def validate_config(raw: dict, available_pwm: list[str] | None = None) -> dict:
    """逐字段校验配置，无效字段回退默认值"""
    return {
        "mode": _validate_mode(raw.get("mode")),
        "poll_interval": _validate_int_range(
            raw.get("poll_interval"), 1, 30, DEFAULT_CONFIG["poll_interval"], "poll_interval"
        ),
        "min_pwm_percent": _validate_int_range(
            raw.get("min_pwm_percent"), ABSOLUTE_MIN_PERCENT, 100,
            DEFAULT_CONFIG["min_pwm_percent"], "min_pwm_percent"
        ),
        "temp_source": _validate_temp_source(raw.get("temp_source")),
        "manual_pwm_percent": _validate_int_range(
            raw.get("manual_pwm_percent"), ABSOLUTE_MIN_PERCENT, 100,
            DEFAULT_CONFIG["manual_pwm_percent"], "manual_pwm_percent"
        ),
        "curve": _validate_curve(raw.get("curve", DEFAULT_CONFIG["curve"])),
        "fan_channel": _validate_fan_channel(raw.get("fan_channel"), available_pwm),
        "web_port": _validate_int_range(
            raw.get("web_port"), 1024, 65535, DEFAULT_CONFIG["web_port"], "web_port"
        ),
    }


class ConfigManager:
    """线程安全的配置管理器"""

    def __init__(self, config_dir: str, available_pwm: list[str] | None = None):
        """
        Args:
            config_dir: 配置文件所在目录（对应 $TRIM_PKGETC）
            available_pwm: 已探测到的可用 PWM 通道列表
        """
        self._config_path = os.path.join(config_dir, "config.json")
        self._available_pwm = available_pwm
        self._lock = threading.Lock()
        self._config: dict = copy.deepcopy(DEFAULT_CONFIG)

    def load(self) -> dict:
        """从文件加载配置，校验后返回。加载失败使用默认配���。"""
        with self._lock:
            try:
                with open(self._config_path, "r") as f:
                    raw = json.load(f)
                if not isinstance(raw, dict):
                    raise ValueError("配置文件根元素不是对象")
                self._config = validate_config(raw, self._available_pwm)
                logger.info("配置已加载: %s", self._config_path)
            except FileNotFoundError:
                self._config = copy.deepcopy(DEFAULT_CONFIG)
                logger.info("配置文件不存在，使用默认配置")
            except (json.JSONDecodeError, ValueError) as e:
                self._config = copy.deepcopy(DEFAULT_CONFIG)
                logger.warning("配置文件解析失败 (%s)，使用默认配置", e)

            return copy.deepcopy(self._config)

    def save(self) -> bool:
        """将当前配置保存到文件。失败��影响运行。"""
        with self._lock:
            try:
                os.makedirs(os.path.dirname(self._config_path), exist_ok=True)
                with open(self._config_path, "w") as f:
                    json.dump(self._config, f, indent=2, ensure_ascii=False)
                return True
            except (OSError, IOError) as e:
                logger.error("配置保存失败: %s", e)
                return False

    def get(self) -> dict:
        """获取当前配置的深拷贝（线程安全）"""
        with self._lock:
            return copy.deepcopy(self._config)

    def update(self, partial: dict) -> dict:
        """部分更新配置，校验后保存

        Args:
            partial: 要更新的字段子集

        Returns:
            更新后的完整配置
        """
        with self._lock:
            merged = {**self._config, **partial}
            self._config = validate_config(merged, self._available_pwm)

        self.save()
        return self.get()
