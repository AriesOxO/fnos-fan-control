"""
配置管理 — 加载、校验、保存、线程安全读写

支持两种配置格式：
- v1 扁平格式（无 zones 字段）：向后兼容，运行时自动包装为单区域
- v2 zones 格式：多区域独立配置

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

# 绝对下限百分比，与 hardware.py 的 ABSOLUTE_MIN_PWM 对应
ABSOLUTE_MIN_PERCENT = 10


# ── 字段校验函数 ─────────────────────────────────────────

def _validate_mode(value) -> str:
    if isinstance(value, str) and value in VALID_MODES:
        return value
    logger.warning("mode 校验失败: %r，回退默认值", value)
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


# ── 区域校验 ─────────────────────────────────────────────

def _validate_zone(zone: dict, available_pwm: list[str] | None = None) -> dict | None:
    """校验单个风扇区域配置，返回校验后的 zone 或 None（跳过）"""
    if not isinstance(zone, dict):
        logger.warning("zone 格式错误，跳过")
        return None

    zone_id = zone.get("id")
    if not isinstance(zone_id, str) or not zone_id.strip():
        zone_id = "zone_{}".format(id(zone))

    # channels：至少 1 个，须匹配已探测通道
    channels = zone.get("channels", [])
    if not isinstance(channels, list) or len(channels) == 0:
        logger.warning("zone '%s' 无 channels，跳过", zone_id)
        return None

    if available_pwm is not None:
        valid_channels = [ch for ch in channels if ch in available_pwm]
        if not valid_channels:
            logger.warning("zone '%s' 所有 channels 无效，跳过", zone_id)
            return None
        channels = valid_channels

    name = zone.get("name")
    if not isinstance(name, str) or not name.strip():
        name = "风扇区域"

    return {
        "id": zone_id,
        "name": name,
        "channels": channels,
        "temp_source": _validate_temp_source(zone.get("temp_source")),
        "mode": _validate_mode(zone.get("mode")),
        "min_pwm_percent": _validate_int_range(
            zone.get("min_pwm_percent"), ABSOLUTE_MIN_PERCENT, 100,
            DEFAULT_CONFIG["min_pwm_percent"], "min_pwm_percent",
        ),
        "manual_pwm_percent": _validate_int_range(
            zone.get("manual_pwm_percent"), ABSOLUTE_MIN_PERCENT, 100,
            DEFAULT_CONFIG["manual_pwm_percent"], "manual_pwm_percent",
        ),
        "curve": _validate_curve(zone.get("curve", DEFAULT_CONFIG["curve"])),
    }


def _validate_zones(zones: list, available_pwm: list[str] | None = None) -> list[dict]:
    """校验 zones 列表，过滤无效区域，检测通道冲突"""
    if not isinstance(zones, list):
        return []

    validated = []
    claimed_channels = set()

    for raw_zone in zones:
        zone = _validate_zone(raw_zone, available_pwm)
        if zone is None:
            continue

        conflict = set(zone["channels"]) & claimed_channels
        if conflict:
            logger.warning("zone '%s' 的通道 %s 已被占用，跳过", zone["id"], conflict)
            continue

        claimed_channels.update(zone["channels"])
        validated.append(zone)

    return validated


def _make_default_zone(available_pwm: list[str] | None = None) -> dict:
    """创建默认单区域配置"""
    channel = "pwm2"
    if available_pwm and "pwm2" not in available_pwm:
        channel = available_pwm[0] if available_pwm else "pwm2"
    return {
        "id": "default",
        "name": "系统风扇",
        "channels": [channel],
        "temp_source": DEFAULT_CONFIG["temp_source"],
        "mode": DEFAULT_CONFIG["mode"],
        "min_pwm_percent": DEFAULT_CONFIG["min_pwm_percent"],
        "manual_pwm_percent": DEFAULT_CONFIG["manual_pwm_percent"],
        "curve": copy.deepcopy(DEFAULT_CONFIG["curve"]),
    }


# ── 配置校验 ─────────────────────────────────────────────

def validate_config(raw: dict, available_pwm: list[str] | None = None) -> dict:
    """逐字段校验配置

    支持两种格式：
    - v1 扁平格式（无 zones）：保留所有原有字段
    - v2 zones 格式：全局字段 + zones 列表
    """
    result = {
        "poll_interval": _validate_int_range(
            raw.get("poll_interval"), 1, 30, DEFAULT_CONFIG["poll_interval"], "poll_interval"
        ),
        "web_port": _validate_int_range(
            raw.get("web_port"), 1024, 65535, DEFAULT_CONFIG["web_port"], "web_port"
        ),
    }

    if "zones" in raw:
        result["zones"] = _validate_zones(raw["zones"], available_pwm)
        if not result["zones"]:
            logger.warning("所有 zones 校验失败，回退为默认配置")
            result["zones"] = [_make_default_zone(available_pwm)]
    else:
        # v1 扁平格式：保留原有字段
        result["mode"] = _validate_mode(raw.get("mode"))
        result["min_pwm_percent"] = _validate_int_range(
            raw.get("min_pwm_percent"), ABSOLUTE_MIN_PERCENT, 100,
            DEFAULT_CONFIG["min_pwm_percent"], "min_pwm_percent"
        )
        result["temp_source"] = _validate_temp_source(raw.get("temp_source"))
        result["manual_pwm_percent"] = _validate_int_range(
            raw.get("manual_pwm_percent"), ABSOLUTE_MIN_PERCENT, 100,
            DEFAULT_CONFIG["manual_pwm_percent"], "manual_pwm_percent"
        )
        result["curve"] = _validate_curve(raw.get("curve", DEFAULT_CONFIG["curve"]))
        result["fan_channel"] = _validate_fan_channel(raw.get("fan_channel"), available_pwm)

    return result


def normalize_config(config: dict) -> dict:
    """统一为带 zones 的内部格式（运行时调用，不改文件）

    v1 扁平配置 → 包装为单区域
    v2 zones 配置 → 原样返回
    """
    if "zones" in config:
        return config

    zone = {
        "id": "default",
        "name": "系统风扇",
        "channels": [config.get("fan_channel", "pwm2")],
        "temp_source": config.get("temp_source", "cpu"),
        "mode": config.get("mode", "default"),
        "min_pwm_percent": config.get("min_pwm_percent", 20),
        "manual_pwm_percent": config.get("manual_pwm_percent", 50),
        "curve": config.get("curve", copy.deepcopy(DEFAULT_CONFIG["curve"])),
    }
    return {**config, "zones": [zone]}


# ── ConfigManager ────────────────────────────────────────

class ConfigManager:
    """线程安全的配置管理器"""

    def __init__(self, config_dir: str, available_pwm: list[str] | None = None):
        self._config_path = os.path.join(config_dir, "config.json")
        self._available_pwm = available_pwm
        self._lock = threading.Lock()
        self._config: dict = copy.deepcopy(DEFAULT_CONFIG)

    def load(self) -> dict:
        """从文件加载配置，校验后返回。加载失败使用默认配置。"""
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
        """将当前配置保存到文件。"""
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

    def update_zone(self, zone_id: str, partial: dict) -> dict | None:
        """更新指定区域的配置

        Args:
            zone_id: 区域 ID
            partial: 要更新的字段子集

        Returns:
            更新后的区域配置，区域不存在返回 None
        """
        with self._lock:
            normalized = normalize_config(self._config)
            zones = normalized.get("zones", [])

            target_idx = None
            for i, z in enumerate(zones):
                if z["id"] == zone_id:
                    target_idx = i
                    break

            if target_idx is None:
                return None

            target = zones[target_idx]
            merged = {**target, **partial}
            merged["id"] = target["id"]
            merged["channels"] = target["channels"]
            validated = _validate_zone(merged, self._available_pwm)

            if validated is None:
                return None

            zones[target_idx] = validated

            # 写回配置
            if "zones" not in self._config:
                # 首次从 v1 升级到 v2
                self._config = {
                    "poll_interval": self._config.get("poll_interval", 2),
                    "web_port": self._config.get("web_port", 9511),
                    "zones": zones,
                }
            else:
                self._config["zones"] = zones

        self.save()
        return copy.deepcopy(validated)
