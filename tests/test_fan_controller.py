"""风扇控制核心单元测试（Mock 硬件）"""

import os
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src", "app", "bin"))

from hardware import Hardware
from config_manager import ConfigManager
from fan_controller import FanController
from config_manager import DEFAULT_SAFE_CURVE


class MockHardware(Hardware):
    """模拟硬件，不访问 /sys"""

    def __init__(self):
        super().__init__()
        self._mock_temp = 50.0
        self._mock_pwm = 128
        self._mock_enable = 1
        self._mock_rpm = 2500
        self._mock_disk_temps = {}
        self.it8772_base = "/mock/hwmon"
        self.coretemp_base = "/mock/coretemp"
        self.available_pwm = ["pwm1", "pwm2"]
        self.available_fans = {"pwm2": "/mock/fan2"}
        self._write_history = []

    def read_cpu_temp(self):
        if self._mock_temp is None:
            self._read_fail_count += 1
            return self._last_valid_temp
        self._read_fail_count = 0
        self._last_valid_temp = self._mock_temp
        return self._mock_temp

    def read_disk_temps(self):
        return dict(self._mock_disk_temps)

    def read_fan_rpm(self, channel="pwm2"):
        return self._mock_rpm

    def read_pwm(self, channel="pwm2"):
        return self._mock_pwm

    def read_pwm_enable(self, channel="pwm2"):
        return self._mock_enable

    def write_pwm(self, value, channel="pwm2", min_percent=25):
        from hardware import safe_pwm_value
        self._mock_pwm = safe_pwm_value(value, min_percent)
        self._write_history.append(("pwm", self._mock_pwm))
        return True

    def set_pwm_mode(self, mode, channel="pwm2"):
        self._mock_enable = mode
        self._write_history.append(("enable", mode))
        return True

    def restore_safe_state(self):
        self._mock_enable = 2
        self._write_history.append(("restore", 2))


class TestInterpolateCurve(unittest.TestCase):
    """线性插值测试"""

    def setUp(self):
        self.curve = [
            {"temp": 30, "pwm_percent": 20},
            {"temp": 50, "pwm_percent": 50},
            {"temp": 70, "pwm_percent": 85},
            {"temp": 80, "pwm_percent": 100},
        ]

    def test_below_min(self):
        result = FanController._interpolate_curve(20, self.curve)
        self.assertEqual(result, int(255 * 20 / 100))

    def test_above_max(self):
        result = FanController._interpolate_curve(90, self.curve)
        self.assertEqual(result, 255)

    def test_exact_node(self):
        result = FanController._interpolate_curve(50, self.curve)
        self.assertEqual(result, int(255 * 50 / 100))

    def test_midpoint_interpolation(self):
        # 40°C 在 30-50 之间的中点，PWM 应在 20%-50% 之间中点 = 35%
        result = FanController._interpolate_curve(40, self.curve)
        expected = int(255 * 35 / 100)
        self.assertAlmostEqual(result, expected, delta=2)

    def test_empty_curve(self):
        self.assertEqual(FanController._interpolate_curve(50, []), 128)

    def test_two_nodes(self):
        curve = [{"temp": 30, "pwm_percent": 20}, {"temp": 80, "pwm_percent": 100}]
        result = FanController._interpolate_curve(55, curve)
        # 55 在 30-80 之间，比例 25/50 = 0.5，PWM = 20 + 80*0.5 = 60%
        expected = int(255 * 60 / 100)
        self.assertAlmostEqual(result, expected, delta=2)


class TestFanControllerModes(unittest.TestCase):
    """模式切换测试"""

    def setUp(self):
        self.hw = MockHardware()
        self.tmpdir = tempfile.mkdtemp()
        self.cm = ConfigManager(self.tmpdir, available_pwm=["pwm1", "pwm2"])
        self.cm.load()

    def _make_controller(self):
        fc = FanController(self.hw, self.cm)
        fc.start()
        time.sleep(0.5)
        return fc

    def _stop(self, fc):
        fc.stop()

    def test_starts_in_default(self):
        fc = self._make_controller()
        status = fc.get_status()
        self.assertEqual(status["mode"], "default")
        self._stop(fc)

    def test_switch_to_auto(self):
        fc = self._make_controller()
        self.assertTrue(fc.set_mode("auto"))
        time.sleep(0.5)  # 等待控制线程更新状态
        status = fc.get_status()
        self.assertEqual(status["mode"], "auto")
        self.assertEqual(self.hw._mock_enable, 1)
        self._stop(fc)

    def test_switch_to_manual(self):
        self.cm.update({"manual_pwm_percent": 60})
        fc = self._make_controller()
        fc.set_mode("manual")
        time.sleep(0.3)
        self.assertEqual(self.hw._mock_enable, 1)
        # 60% of 255 = 153
        self.assertAlmostEqual(self.hw._mock_pwm, 153, delta=2)
        self._stop(fc)

    def test_switch_to_full(self):
        fc = self._make_controller()
        fc.set_mode("full")
        self.assertEqual(self.hw._mock_pwm, 255)
        self.assertEqual(self.hw._mock_enable, 1)
        self._stop(fc)

    def test_switch_back_to_default(self):
        fc = self._make_controller()
        fc.set_mode("auto")
        self.assertEqual(self.hw._mock_enable, 1)
        fc.set_mode("default")
        # 默认模式也是 pwm_enable=1（芯片自动=全速不可用）
        self.assertEqual(self.hw._mock_enable, 1)
        self._stop(fc)

    def test_invalid_mode_rejected(self):
        fc = self._make_controller()
        self.assertFalse(fc.set_mode("bad"))
        self.assertEqual(fc.get_status()["mode"], "default")
        self._stop(fc)

    def test_no_hw_rejects_non_default(self):
        self.hw.it8772_base = None
        fc = self._make_controller()
        self.assertFalse(fc.set_mode("auto"))
        self.assertEqual(fc.get_status()["mode"], "default")
        self._stop(fc)

    def test_mode_switch_clears_degraded(self):
        fc = self._make_controller()
        fc._degraded = True
        fc._degrade_reason = "test"
        fc.set_mode("auto")
        status = fc.get_status()
        self.assertFalse(status["degraded"])
        self.assertEqual(status["degrade_reason"], "")
        self._stop(fc)


class TestFanControllerSafety(unittest.TestCase):
    """安全机制测试"""

    def setUp(self):
        self.hw = MockHardware()
        self.tmpdir = tempfile.mkdtemp()
        self.cm = ConfigManager(self.tmpdir, available_pwm=["pwm1", "pwm2"])
        self.cm.load()

    def test_cleanup_restores_safe_state(self):
        fc = FanController(self.hw, self.cm)
        fc.start()
        time.sleep(0.3)
        fc.set_mode("auto")
        fc.stop()
        self.assertEqual(self.hw._mock_enable, 2)

    def test_degrade_restores_default(self):
        fc = FanController(self.hw, self.cm)
        fc.start()
        time.sleep(0.5)
        fc.set_mode("auto")
        time.sleep(0.3)
        fc._degrade("test reason")
        # 验证内部状态（_degrade 是同步的）
        self.assertEqual(fc._mode, "default")
        self.assertTrue(fc._degraded)
        self.assertEqual(fc._degrade_reason, "test reason")
        # 降级后仍然 pwm_enable=1，靠保守曲线控制
        self.assertEqual(self.hw._mock_enable, 1)
        fc.stop()


class TestFanControllerLogs(unittest.TestCase):
    """事件日志测试"""

    def setUp(self):
        self.hw = MockHardware()
        self.tmpdir = tempfile.mkdtemp()
        self.cm = ConfigManager(self.tmpdir, available_pwm=["pwm1", "pwm2"])
        self.cm.load()

    def test_startup_log(self):
        fc = FanController(self.hw, self.cm)
        fc.start()
        time.sleep(0.5)
        logs = fc.get_logs()
        self.assertTrue(any("服务启动" in l.get("message", "") for l in logs))
        fc.stop()

    def test_mode_switch_log(self):
        fc = FanController(self.hw, self.cm)
        fc.start()
        time.sleep(0.3)
        fc.set_mode("auto")
        logs = fc.get_logs()
        self.assertTrue(any("自动模式" in l.get("message", "") for l in logs))
        fc.stop()

    def test_clear_logs(self):
        fc = FanController(self.hw, self.cm)
        fc.start()
        time.sleep(0.3)
        fc.set_mode("auto")
        self.assertGreater(len(fc.get_logs()), 0)
        fc.clear_logs()
        self.assertEqual(len(fc.get_logs()), 0)
        fc.stop()

    def test_degrade_log(self):
        fc = FanController(self.hw, self.cm)
        fc.start()
        time.sleep(0.3)
        fc._degrade("test error")
        logs = fc.get_logs()
        error_logs = [l for l in logs if l.get("level") == "error"]
        self.assertGreater(len(error_logs), 0)
        fc.stop()


if __name__ == "__main__":
    unittest.main()
