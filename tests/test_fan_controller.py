"""风扇控制核心单元测试（Mock 硬件）"""

import json
import os
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src", "app", "bin"))

from hardware import Hardware, safe_pwm_value
from config_manager import ConfigManager, DEFAULT_SAFE_CURVE
from fan_controller import FanController


class MockHardware(Hardware):
    """模拟硬件，不访问 /sys"""

    def __init__(self):
        super().__init__()
        self._mock_temp = 50.0
        self._mock_pwm = 128
        self._mock_enable = 1
        self._mock_rpm = 2500
        self._mock_disk_temps = {}

        # 模拟芯片探测结果
        self.chips = [{
            "name": "mock",
            "display_name": "Mock Chip",
            "hwmon_path": "/mock/hwmon",
            "pwm_channels": ["pwm1", "pwm2"],
            "fan_inputs": {"pwm2": "/mock/fan2"},
        }]
        self.cpu_temp_base = "/mock/coretemp"
        self.cpu_temp_driver = "coretemp"
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
        time.sleep(0.5)
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
        self.assertEqual(self.hw._mock_enable, 1)
        self._stop(fc)

    def test_invalid_mode_rejected(self):
        fc = self._make_controller()
        self.assertFalse(fc.set_mode("bad"))
        self.assertEqual(fc.get_status()["mode"], "default")
        self._stop(fc)

    def test_no_hw_rejects_non_default(self):
        self.hw.chips = []
        fc = self._make_controller()
        self.assertFalse(fc.set_mode("auto"))
        self.assertEqual(fc.get_status()["mode"], "default")
        self._stop(fc)

    def test_mode_switch_clears_degraded(self):
        fc = self._make_controller()
        fc._degraded_zones.add("default")
        fc.set_mode("auto")
        status = fc.get_status()
        self.assertFalse(status["degraded"])
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
        # 模拟降级
        zone = {"id": "default", "name": "系统风扇", "channels": ["pwm2"],
                "temp_source": "cpu", "mode": "auto"}
        fc._degrade_zone(zone, "test reason")
        self.assertIn("default", fc._degraded_zones)
        self.assertEqual(self.hw._mock_enable, 1)  # 保持手动模式，靠保守曲线
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
        zone = {"id": "default", "name": "系统风扇", "channels": ["pwm2"],
                "temp_source": "cpu", "mode": "auto"}
        fc._degrade_zone(zone, "test error")
        logs = fc.get_logs()
        error_logs = [l for l in logs if l.get("level") == "error"]
        self.assertGreater(len(error_logs), 0)
        fc.stop()


class TestFanControllerStatus(unittest.TestCase):
    """状态输出测试"""

    def setUp(self):
        self.hw = MockHardware()
        self.tmpdir = tempfile.mkdtemp()
        self.cm = ConfigManager(self.tmpdir, available_pwm=["pwm1", "pwm2"])
        self.cm.load()

    def test_status_has_zones(self):
        fc = FanController(self.hw, self.cm)
        fc.start()
        time.sleep(0.5)
        status = fc.get_status()
        self.assertIn("zones", status)
        self.assertIn("hw_detected", status)
        self.assertTrue(status["hw_detected"])
        fc.stop()

    def test_status_backward_compatible(self):
        """单区域时顶层字段应保持兼容"""
        fc = FanController(self.hw, self.cm)
        fc.start()
        time.sleep(0.5)
        status = fc.get_status()
        # 顶层仍有这些字段
        self.assertIn("cpu_temp", status)
        self.assertIn("fan_rpm", status)
        self.assertIn("pwm_percent", status)
        self.assertIn("mode", status)
        self.assertIn("degraded", status)
        fc.stop()


class TestMultiZoneControl(unittest.TestCase):
    """多区域控制测试"""

    def setUp(self):
        self.hw = MockHardware()
        self.tmpdir = tempfile.mkdtemp()
        config = {
            "poll_interval": 2,
            "web_port": 9511,
            "zones": [
                {
                    "id": "cpu", "name": "CPU 风扇",
                    "channels": ["pwm1"], "temp_source": "cpu",
                    "mode": "auto", "min_pwm_percent": 20,
                    "manual_pwm_percent": 50,
                    "curve": [{"temp": 30, "pwm_percent": 20}, {"temp": 80, "pwm_percent": 100}],
                },
                {
                    "id": "hdd", "name": "硬盘风扇",
                    "channels": ["pwm2"], "temp_source": "disk",
                    "mode": "manual", "min_pwm_percent": 25,
                    "manual_pwm_percent": 40,
                    "curve": [{"temp": 30, "pwm_percent": 25}, {"temp": 60, "pwm_percent": 100}],
                },
            ],
        }
        with open(os.path.join(self.tmpdir, "config.json"), "w") as f:
            json.dump(config, f)
        self.cm = ConfigManager(self.tmpdir, available_pwm=["pwm1", "pwm2"])
        self.cm.load()

    def test_multi_zone_status(self):
        fc = FanController(self.hw, self.cm)
        fc.start()
        time.sleep(1)
        status = fc.get_status()
        self.assertIn("zones", status)
        self.assertIn("cpu", status["zones"])
        self.assertIn("hdd", status["zones"])
        fc.stop()

    def test_switch_single_zone_mode(self):
        fc = FanController(self.hw, self.cm)
        fc.start()
        time.sleep(0.5)
        self.assertTrue(fc.set_mode("full", zone_id="cpu"))
        time.sleep(0.5)
        status = fc.get_status()
        self.assertEqual(status["zones"]["cpu"]["mode"], "full")
        # hdd 区域不受影响
        self.assertEqual(status["zones"]["hdd"]["mode"], "manual")
        fc.stop()

    def test_switch_all_zones_mode(self):
        fc = FanController(self.hw, self.cm)
        fc.start()
        time.sleep(0.5)
        self.assertTrue(fc.set_mode("default"))
        time.sleep(0.5)
        status = fc.get_status()
        self.assertEqual(status["zones"]["cpu"]["mode"], "default")
        self.assertEqual(status["zones"]["hdd"]["mode"], "default")
        fc.stop()

    def test_invalid_zone_id(self):
        fc = FanController(self.hw, self.cm)
        fc.start()
        time.sleep(0.3)
        self.assertFalse(fc.set_mode("auto", zone_id="nonexistent"))
        fc.stop()


if __name__ == "__main__":
    unittest.main()
