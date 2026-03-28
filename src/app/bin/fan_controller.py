"""
风扇控制核心 — 温控线程、模式管理、异常降级

支持多风扇区域独立控制。每个区域绑定独立的 PWM 通道、温度来源和温控曲线。
单区域时行为与之前完全一致。

运行模式（每个区域独立）：
- default: 使用保守温控曲线，pwm_enable=1
- auto: 按自定义温控曲线调节 PWM
- manual: 固定 PWM 值
- full: PWM=255 全速
"""

import collections
import logging
import threading
import time

from hardware import Hardware, PWM_ENABLE_MANUAL
from config_manager import ConfigManager, DEFAULT_SAFE_CURVE, normalize_config

logger = logging.getLogger(__name__)

LOG_BUFFER_SIZE = 100


class FanController(threading.Thread):
    """风扇控制守护线程，遍历所有区域执行控制周期"""

    MODE_DEFAULT = "default"
    MODE_AUTO = "auto"
    MODE_MANUAL = "manual"
    MODE_FULL = "full"

    def __init__(self, hardware: Hardware, config_manager: ConfigManager):
        super().__init__(daemon=True, name="FanController")
        self._hw = hardware
        self._cfg = config_manager
        self._lock = threading.Lock()
        self._stop_event = threading.Event()

        self._start_time: float = time.time()
        self._logs = collections.deque(maxlen=LOG_BUFFER_SIZE)

        # 模式切换时通知守护线程立即执行一次控制周期
        self._mode_changed = threading.Event()

        # 区域级状态
        self._zone_states: dict[str, dict] = {}
        self._degraded_zones: set[str] = set()
        self._write_fail_counts: dict[str, int] = {}
        self._max_write_failures: int = 3

    def run(self):
        """主控制循环"""
        config = self._cfg.get()
        normalized = normalize_config(config)
        zones = normalized["zones"]

        # 初始化所有区域的 pwm_enable=1（手动模式）
        for zone in zones:
            for ch in zone["channels"]:
                self._hw.set_pwm_mode(PWM_ENABLE_MANUAL, ch)

        logger.info("风扇控制线程启动，区域数: %d", len(zones))
        zone_desc = ", ".join(
            "{}({})".format(z["name"], "+".join(z["channels"])) for z in zones
        )
        self._add_log("info", "服务启动，{} 个区域: {}".format(len(zones), zone_desc))

        while not self._stop_event.is_set():
            try:
                config = self._cfg.get()
                normalized = normalize_config(config)
                self._control_all_zones(normalized)
            except Exception as e:
                logger.error("控制循环异常: %s", e)
                self._add_log("error", "控制循环异常: {}".format(e))

            poll_interval = config.get("poll_interval", 2)
            self._mode_changed.wait(timeout=poll_interval)
            self._mode_changed.clear()

        logger.info("风扇控制线程退出")

    def _control_all_zones(self, config: dict):
        """遍历所有区域执行控制周期（单线程，一次读温多次写 PWM）"""
        cpu_temp = self._hw.read_cpu_temp()
        disk_temps = self._hw.read_disk_temps()

        for zone in config["zones"]:
            self._control_zone(zone, cpu_temp, disk_temps)

    def _control_zone(self, zone: dict, cpu_temp, disk_temps):
        """单个区域的控制逻辑"""
        zone_id = zone["id"]
        mode = zone["mode"]
        channels = zone["channels"]

        # 1. 计算有效温度
        effective_temp = self._get_effective_temp(cpu_temp, disk_temps, zone["temp_source"])

        # 2. 检查 pwm_enable 一致性（自愈）
        for ch in channels:
            current_enable = self._hw.read_pwm_enable(ch)
            if current_enable is not None and current_enable != PWM_ENABLE_MANUAL:
                logger.warning(
                    "区域 '%s' %s pwm_enable=%s，修正为 %d",
                    zone["name"], ch, current_enable, PWM_ENABLE_MANUAL,
                )
                self._add_log("warn", "区域 {} pwm_enable 自动修正".format(zone["name"]))
                if not self._hw.set_pwm_mode(PWM_ENABLE_MANUAL, ch):
                    self._degrade_zone(zone, "无法恢复 PWM 手动模式")
                    return

        # 3. 温度读取临界检测
        if self._hw.is_read_failure_critical:
            if self._hw.read_fail_count >= 5:
                self._degrade_zone(zone, "温度连续读取失败 5 次")
                return
            msg = "温度读取连续失败 {} 次，全速保护".format(self._hw.read_fail_count)
            logger.warning(msg)
            self._add_log("warn", msg)
            for ch in channels:
                self._hw.write_pwm(255, ch, min_percent=0)
            self._update_zone_status(zone, cpu_temp, disk_temps, 255)
            return

        # 4. 计算目标 PWM
        target_pwm = self._calculate_target_pwm(mode, effective_temp, zone)

        # 5. 写入所有绑定通道
        if target_pwm is not None:
            for ch in channels:
                ok = self._hw.write_pwm(target_pwm, ch, zone["min_pwm_percent"])
                if not ok:
                    count = self._write_fail_counts.get(zone_id, 0) + 1
                    self._write_fail_counts[zone_id] = count
                    self._add_log("warn", "区域 {} PWM 写入失败 (连续 {} 次)".format(
                        zone["name"], count))
                    if count >= self._max_write_failures:
                        self._degrade_zone(zone, "PWM 连续写入失败 {} 次".format(count))
                        return
                else:
                    self._write_fail_counts[zone_id] = 0

        # 6. 更新区域状态
        actual_pwm = self._hw.read_pwm(channels[0]) if channels else None
        self._update_zone_status(zone, cpu_temp, disk_temps, actual_pwm)

    def _get_effective_temp(self, cpu_temp, disk_temps, source):
        """根据温度来源获取有效温度"""
        if source == "cpu":
            return cpu_temp
        elif source == "disk":
            return max(disk_temps.values()) if disk_temps else cpu_temp
        elif source == "max":
            temps = []
            if cpu_temp is not None:
                temps.append(cpu_temp)
            temps.extend(disk_temps.values())
            return max(temps) if temps else None
        return cpu_temp

    def _calculate_target_pwm(self, mode, temp, zone):
        """根据模式计算目标 PWM 值"""
        if mode == self.MODE_DEFAULT:
            if temp is None:
                return None
            return self._interpolate_curve(temp, DEFAULT_SAFE_CURVE)
        if mode == self.MODE_FULL:
            return 255
        if mode == self.MODE_MANUAL:
            return int(255 * zone["manual_pwm_percent"] / 100)
        if mode == self.MODE_AUTO:
            if temp is None:
                return None
            return self._interpolate_curve(temp, zone["curve"])
        return None

    @staticmethod
    def _interpolate_curve(temp, curve):
        """线性插值计算温控曲线对应的 PWM 值"""
        if not curve:
            return 128

        if temp <= curve[0]["temp"]:
            return int(255 * curve[0]["pwm_percent"] / 100)

        if temp >= curve[-1]["temp"]:
            return int(255 * curve[-1]["pwm_percent"] / 100)

        for i in range(len(curve) - 1):
            t1, p1 = curve[i]["temp"], curve[i]["pwm_percent"]
            t2, p2 = curve[i + 1]["temp"], curve[i + 1]["pwm_percent"]
            if t1 <= temp <= t2:
                ratio = (temp - t1) / (t2 - t1)
                pwm_percent = p1 + (p2 - p1) * ratio
                return int(255 * pwm_percent / 100)

        return int(255 * curve[-1]["pwm_percent"] / 100)

    def _degrade_zone(self, zone: dict, reason: str):
        """区域级异常降级：恢复该区域为默认模式（保守曲线）"""
        zone_id = zone["id"]
        logger.error("区域 '%s' 异常降级: %s", zone["name"], reason)
        self._hw.reset_read_fail_count()

        with self._lock:
            self._degraded_zones.add(zone_id)
            self._write_fail_counts[zone_id] = 0

        self._add_log("error", "区域 {} 降级: {}".format(zone["name"], reason))
        self._cfg.update_zone(zone_id, {"mode": "default"})

    def _update_zone_status(self, zone: dict, cpu_temp, disk_temps, pwm_value):
        """更新指定区域的状态快照"""
        zone_id = zone["id"]
        channels = zone["channels"]
        rpm = self._hw.read_fan_rpm(channels[0]) if channels else None
        pwm_percent = round(pwm_value / 255 * 100) if pwm_value is not None else None

        zone_status = {
            "name": zone["name"],
            "channels": channels,
            "temp_source": zone["temp_source"],
            "temp": self._get_effective_temp(cpu_temp, disk_temps, zone["temp_source"]),
            "fan_rpm": rpm,
            "pwm_value": pwm_value,
            "pwm_percent": pwm_percent,
            "mode": zone["mode"],
            "degraded": zone_id in self._degraded_zones,
        }

        with self._lock:
            self._zone_states[zone_id] = zone_status

    def _add_log(self, level, message):
        """记录事件日志（仅操作、告警、错误）"""
        entry = {
            "time": time.strftime("%m-%d %H:%M:%S"),
            "level": level,
            "message": message,
        }
        with self._lock:
            self._logs.append(entry)

    # ── 公共接口（Web API 调用）──────────────────────────

    def set_mode(self, mode: str, zone_id: str | None = None) -> bool:
        """切换运行模式（线程安全）

        Args:
            mode: 目标模式
            zone_id: 指定区域 ID，None 表示所有区域
        """
        if mode not in (self.MODE_DEFAULT, self.MODE_AUTO, self.MODE_MANUAL, self.MODE_FULL):
            logger.warning("无效模式: %s", mode)
            return False

        if not self._hw.hw_detected and mode != self.MODE_DEFAULT:
            logger.warning("硬件未探测到，仅允许默认模式")
            return False

        config = self._cfg.get()
        normalized = normalize_config(config)

        target_zones = normalized["zones"]
        if zone_id is not None:
            target_zones = [z for z in target_zones if z["id"] == zone_id]
            if not target_zones:
                logger.warning("区域 %s 不存在", zone_id)
                return False

        for zone in target_zones:
            # 设置 pwm_enable=1
            for ch in zone["channels"]:
                if not self._hw.set_pwm_mode(PWM_ENABLE_MANUAL, ch):
                    logger.error("set_pwm_mode 失败，模式切换中止")
                    return False

            # 立即写入对应 PWM
            cpu_temp = self._hw.read_cpu_temp()
            disk_temps = self._hw.read_disk_temps()
            effective_temp = self._get_effective_temp(
                cpu_temp, disk_temps, zone["temp_source"])

            if mode == self.MODE_DEFAULT and effective_temp is not None:
                pwm_val = self._interpolate_curve(effective_temp, DEFAULT_SAFE_CURVE)
                for ch in zone["channels"]:
                    self._hw.write_pwm(pwm_val, ch, zone["min_pwm_percent"])
            elif mode == self.MODE_FULL:
                for ch in zone["channels"]:
                    self._hw.write_pwm(255, ch, min_percent=0)
            elif mode == self.MODE_MANUAL:
                pwm_val = int(255 * zone["manual_pwm_percent"] / 100)
                for ch in zone["channels"]:
                    self._hw.write_pwm(pwm_val, ch, zone["min_pwm_percent"])
            elif mode == self.MODE_AUTO and effective_temp is not None:
                pwm_val = self._interpolate_curve(effective_temp, zone["curve"])
                for ch in zone["channels"]:
                    self._hw.write_pwm(pwm_val, ch, zone["min_pwm_percent"])

            # 更新配置
            self._cfg.update_zone(zone["id"], {"mode": mode})

            # 清除降级状态
            with self._lock:
                self._degraded_zones.discard(zone["id"])
                self._write_fail_counts[zone["id"]] = 0

        self._hw.reset_read_fail_count()

        names = {"default": "默认模式", "auto": "自动模式", "manual": "手动模式", "full": "全速模式"}
        scope = "区域 {}".format(zone_id) if zone_id else "所有区域"
        self._add_log("info", "{} 切换到{}".format(scope, names.get(mode, mode)))
        logger.info("%s 切换到 %s", scope, mode)

        self._mode_changed.set()
        return True

    def get_status(self):
        """获取状态快照（兼容单区域和多区域）"""
        with self._lock:
            zone_states = dict(self._zone_states)
            degraded_zones = set(self._degraded_zones)

        cpu_temp = self._hw.read_cpu_temp()
        disk_temps = self._hw.read_disk_temps()

        status = {
            "cpu_temp": cpu_temp,
            "disk_temps": disk_temps or {},
            "hw_detected": self._hw.hw_detected,
            "uptime": int(time.time() - self._start_time),
            "zones": zone_states,
        }

        # 兼容单区域：提取第一个区域的字段到顶层
        if zone_states:
            first = next(iter(zone_states.values()))
            status["fan_rpm"] = first.get("fan_rpm")
            status["pwm_value"] = first.get("pwm_value")
            status["pwm_percent"] = first.get("pwm_percent")
            status["mode"] = first.get("mode", "default")
            status["degraded"] = bool(degraded_zones)
            status["degrade_reason"] = ""
            if degraded_zones:
                status["degrade_reason"] = "{} 个区域已降级".format(len(degraded_zones))
        else:
            status.update({
                "fan_rpm": None, "pwm_value": None, "pwm_percent": None,
                "mode": "default", "degraded": False, "degrade_reason": "",
            })

        return status

    def get_logs(self, count=20):
        """获取最近的运行日志"""
        with self._lock:
            return list(self._logs)[-count:]

    def clear_logs(self):
        """清空运行日志"""
        with self._lock:
            self._logs.clear()
        logger.info("运行日志已清空")

    def cleanup(self):
        """退出清理：恢复所有 PWM 为安全状态"""
        logger.info("执行退出清理...")
        self._hw.restore_safe_state()

    def stop(self):
        """停止控制线程"""
        self._stop_event.set()
        self._mode_changed.set()
        self.join(timeout=10)
        self.cleanup()
