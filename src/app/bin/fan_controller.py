"""
风扇控制核心 — 温控线程、模式管理、异常降级

运行模式：
- default: 不干预硬件，pwm_enable=2，仅监控温度
- auto: 按自定义温控曲线调节 PWM
- manual: 固定 PWM 值
- full: PWM=255 全速
"""

import collections
import logging
import threading
import time

from hardware import Hardware
from config_manager import ConfigManager

logger = logging.getLogger(__name__)

LOG_BUFFER_SIZE = 100


class FanController(threading.Thread):
    """风扇控制守护线程"""

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

        self._mode: str = self.MODE_DEFAULT
        self._degraded: bool = False
        self._degrade_reason: str = ""
        self._start_time: float = time.time()

        self._status: dict = {}
        self._logs = collections.deque(maxlen=LOG_BUFFER_SIZE)

        self._write_fail_count: int = 0
        self._max_write_failures: int = 3

        # 模式切换时通知守护线程立即执行一次控制周期
        self._mode_changed = threading.Event()

    def run(self):
        """主控制循环"""
        config = self._cfg.get()
        with self._lock:
            self._mode = config["mode"]

        if self._mode != self.MODE_DEFAULT:
            self._hw.set_pwm_mode(1, config["fan_channel"])

        logger.info("风扇控制线程启动，模式: %s", self._mode)
        names = {"default": "默认模式", "auto": "自动模式", "manual": "手动模式", "full": "全速模式"}
        self._add_log("info", "服务启动，当前模式: " + names.get(self._mode, self._mode))

        while not self._stop_event.is_set():
            try:
                config = self._cfg.get()
                self._control_cycle(config)
            except Exception as e:
                logger.error("控制循环异常: %s", e)
                self._degrade("控制循环异常: {}".format(e))

            poll_interval = self._cfg.get().get("poll_interval", 2)
            # 等待轮询间隔，但模式切换时立即唤醒
            self._mode_changed.wait(timeout=poll_interval)
            self._mode_changed.clear()

        logger.info("风扇控制线程退出")

    def _control_cycle(self, config: dict):
        """单次控制循环：读温度 → 计算 PWM → 写硬件 → 记日志"""
        channel = config["fan_channel"]

        # 加锁读取当前模式，保证一致性
        with self._lock:
            mode = self._mode

        # 1. 读取温度
        cpu_temp = self._hw.read_cpu_temp()
        disk_temps = self._hw.read_disk_temps()
        effective_temp = self._get_effective_temp(cpu_temp, disk_temps, config["temp_source"])

        # 2. 非默认模式：每个周期确认 pwm_enable=1（自愈机制）
        if mode != self.MODE_DEFAULT:
            current_enable = self._hw.read_pwm_enable(channel)
            if current_enable != 1:
                logger.warning("pwm_enable=%s 不一致，重新设置为 1", current_enable)
                self._add_log("warn", "pwm_enable 不一致，自动修正")
                if not self._hw.set_pwm_mode(1, channel):
                    self._degrade("无法恢复 PWM 手动模式")
                    return

        # 3. 检查温度读取是否临界（仅非默认模式）
        if self._hw.is_read_failure_critical and mode != self.MODE_DEFAULT:
            if self._hw.read_fail_count >= 5:
                self._degrade("温度连续读取失败 5 次")
                return
            msg = "温度读取连续失败 {} 次，全速保护".format(self._hw.read_fail_count)
            logger.warning(msg)
            self._add_log("warn", msg)
            self._hw.write_pwm(255, channel, min_percent=0)
            self._update_status(cpu_temp, disk_temps, 255, channel)
            return

        # 3. 根据模式计算目标 PWM
        target_pwm = self._calculate_target_pwm(mode, effective_temp, config)

        # 4. 写入硬件（仅非默认模式）
        if mode != self.MODE_DEFAULT and target_pwm is not None:
            ok = self._hw.write_pwm(target_pwm, channel, config["min_pwm_percent"])
            if not ok:
                self._write_fail_count += 1
                self._add_log("warn", "PWM 写入失败 (连续 {} 次)".format(self._write_fail_count))
                if self._write_fail_count >= self._max_write_failures:
                    self._degrade("PWM 连续写入失败 {} 次".format(self._write_fail_count))
                    return
            else:
                self._write_fail_count = 0

        # 5. 更新状态和日志
        actual_pwm = self._hw.read_pwm(channel)
        self._update_status(cpu_temp, disk_temps, actual_pwm, channel)

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

    def _calculate_target_pwm(self, mode, temp, config):
        """根据模式计算目标 PWM 值"""
        if mode == self.MODE_DEFAULT:
            return None
        if mode == self.MODE_FULL:
            return 255
        if mode == self.MODE_MANUAL:
            return int(255 * config["manual_pwm_percent"] / 100)
        if mode == self.MODE_AUTO:
            if temp is None:
                return None
            return self._interpolate_curve(temp, config["curve"])
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

    def _degrade(self, reason):
        """异常降级：恢复默认模式"""
        logger.error("异常降级: %s", reason)
        self._hw.restore_safe_state()
        self._hw.reset_read_fail_count()
        with self._lock:
            self._mode = self.MODE_DEFAULT
            self._degraded = True
            self._degrade_reason = reason
            self._write_fail_count = 0
        self._add_log("error", "异常降级: " + reason)

    def _update_status(self, cpu_temp, disk_temps, pwm_value, channel):
        """更新实时状态快照（每个控制周期调用，不写日志）"""
        rpm = self._hw.read_fan_rpm(channel)
        pwm_percent = round(pwm_value / 255 * 100) if pwm_value is not None else None

        with self._lock:
            mode = self._mode

        status = {
            "cpu_temp": cpu_temp,
            "disk_temps": disk_temps or {},
            "fan_rpm": rpm,
            "pwm_value": pwm_value,
            "pwm_percent": pwm_percent,
            "mode": mode,
            "degraded": self._degraded,
            "degrade_reason": self._degrade_reason,
            "hw_detected": self._hw.it8772_base is not None,
            "uptime": int(time.time() - self._start_time),
        }

        with self._lock:
            self._status = status

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

    def set_mode(self, mode: str) -> bool:
        """切换运行模式（线程安全）"""
        if mode not in (self.MODE_DEFAULT, self.MODE_AUTO, self.MODE_MANUAL, self.MODE_FULL):
            logger.warning("无效模式: %s", mode)
            return False

        if self._hw.it8772_base is None and mode != self.MODE_DEFAULT:
            logger.warning("硬件未探测到，仅允许默认模式")
            return False

        config = self._cfg.get()
        channel = config["fan_channel"]

        # 1. 先操作硬件（在修改 _mode 之前）
        #    这样守护线程不会在硬件状态不一致时读到新模式
        if mode == self.MODE_DEFAULT:
            if not self._hw.set_pwm_mode(2, channel):
                logger.error("set_pwm_mode(2) 失败，模式切换中止")
                return False
        else:
            if not self._hw.set_pwm_mode(1, channel):
                logger.error("set_pwm_mode(1) 失败，模式切换中止")
                return False
            if mode == self.MODE_FULL:
                self._hw.write_pwm(255, channel, min_percent=0)
            elif mode == self.MODE_MANUAL:
                pwm_val = int(255 * config["manual_pwm_percent"] / 100)
                self._hw.write_pwm(pwm_val, channel, config["min_pwm_percent"])
            elif mode == self.MODE_AUTO:
                cpu_temp = self._hw.read_cpu_temp()
                disk_temps = self._hw.read_disk_temps()
                effective_temp = self._get_effective_temp(cpu_temp, disk_temps, config["temp_source"])
                if effective_temp is not None:
                    pwm_val = self._interpolate_curve(effective_temp, config["curve"])
                    self._hw.write_pwm(pwm_val, channel, config["min_pwm_percent"])

        # 2. 硬件就绪后再修改模式（锁内）
        with self._lock:
            self._mode = mode
            self._degraded = False
            self._degrade_reason = ""
            self._write_fail_count = 0

        self._hw.reset_read_fail_count()

        names = {"default": "默认模式", "auto": "自动模式", "manual": "手动模式", "full": "全速模式"}
        self._add_log("info", "切换到" + names.get(mode, mode))

        if mode == self.MODE_DEFAULT:
            logger.info("切换到默认模式，释放硬件控制")
        else:
            logger.info("切换到 %s 模式", mode)

        # 3. 通知守护线程立即执行一次控制周期
        self._mode_changed.set()

        # 4. 持久化配置
        self._cfg.update({"mode": mode})
        return True

    def get_status(self):
        """获取当前状态快照"""
        with self._lock:
            return dict(self._status) if self._status else {
                "cpu_temp": None,
                "disk_temps": {},
                "fan_rpm": None,
                "pwm_value": None,
                "pwm_percent": None,
                "mode": self._mode,
                "degraded": self._degraded,
                "degrade_reason": self._degrade_reason,
                "hw_detected": self._hw.it8772_base is not None,
                "uptime": int(time.time() - self._start_time),
            }

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
        """退出清理：恢复 pwm_enable=2"""
        logger.info("执行退出清理...")
        self._hw.restore_safe_state()

    def stop(self):
        """停止控制线程"""
        self._stop_event.set()
        self._mode_changed.set()  # 唤醒等待中的线程
        self.join(timeout=10)
        self.cleanup()
