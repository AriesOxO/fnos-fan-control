"""硬件抽象层单元测试（Mock，不依赖真实硬件）"""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src", "app", "bin"))

from hardware import Hardware, safe_pwm_value, ABSOLUTE_MIN_PWM


class TestSafePwmValue(unittest.TestCase):
    """PWM 下限保护测试"""

    def test_normal_value(self):
        self.assertEqual(safe_pwm_value(200, 25), 200)

    def test_below_min_percent(self):
        # 25% of 255 = 63
        self.assertEqual(safe_pwm_value(0, 25), 63)

    def test_absolute_minimum(self):
        # 即使 min_percent=0，也不低于 ABSOLUTE_MIN_PWM
        self.assertEqual(safe_pwm_value(0, 0), ABSOLUTE_MIN_PWM)

    def test_max_value(self):
        self.assertEqual(safe_pwm_value(255, 50), 255)

    def test_min_percent_10(self):
        # 10% of 255 = 25, 但绝对下限 26
        result = safe_pwm_value(0, 10)
        self.assertGreaterEqual(result, ABSOLUTE_MIN_PWM)

    def test_target_above_min(self):
        self.assertEqual(safe_pwm_value(100, 25), 100)


class TestHardwareDetection(unittest.TestCase):
    """硬件探测测试（使用模拟 hwmon 目录）"""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.hw = Hardware()
        self.hw.HWMON_BASE = self.tmpdir

    def _create_hwmon(self, name, idx, files=None):
        """创建模拟 hwmon 目录"""
        hwmon_dir = os.path.join(self.tmpdir, f"hwmon{idx}")
        os.makedirs(hwmon_dir, exist_ok=True)
        with open(os.path.join(hwmon_dir, "name"), "w") as f:
            f.write(name)
        if files:
            for fname, content in files.items():
                fpath = os.path.join(hwmon_dir, fname)
                os.makedirs(os.path.dirname(fpath), exist_ok=True)
                with open(fpath, "w") as f:
                    f.write(str(content))
        return hwmon_dir

    def test_detect_it8772(self):
        self._create_hwmon("it8772", 0, {
            "pwm1": "128", "pwm1_enable": "2",
            "pwm2": "200", "pwm2_enable": "1",
            "fan1_input": "0", "fan2_input": "3000",
        })
        result = self.hw.detect_hwmon_paths()
        self.assertTrue(result)
        self.assertIsNotNone(self.hw.it8772_base)
        self.assertIn("pwm1", self.hw.available_pwm)
        self.assertIn("pwm2", self.hw.available_pwm)
        # fan2 有读数，fan1 为 0
        self.assertIn("pwm2", self.hw.available_fans)
        self.assertNotIn("pwm1", self.hw.available_fans)

    def test_detect_coretemp(self):
        self._create_hwmon("coretemp", 0, {"temp1_input": "47000"})
        self._create_hwmon("it8772", 1, {"pwm1": "128", "pwm1_enable": "2", "fan1_input": "2000"})
        self.hw.detect_hwmon_paths()
        self.assertIsNotNone(self.hw.coretemp_base)

    def test_no_it8772_returns_false(self):
        self._create_hwmon("coretemp", 0, {"temp1_input": "47000"})
        result = self.hw.detect_hwmon_paths()
        self.assertFalse(result)
        self.assertIsNone(self.hw.it8772_base)

    def test_read_cpu_temp(self):
        self._create_hwmon("coretemp", 0, {"temp1_input": "59000"})
        self._create_hwmon("it8772", 1, {"pwm1": "128", "fan1_input": "2000"})
        self.hw.detect_hwmon_paths()
        temp = self.hw.read_cpu_temp()
        self.assertEqual(temp, 59.0)

    def test_read_cpu_temp_filters_negative(self):
        self._create_hwmon("coretemp", 0, {"temp1_input": "-5000"})
        self._create_hwmon("it8772", 1, {"pwm1": "128", "fan1_input": "2000"})
        self.hw.detect_hwmon_paths()
        self.hw._last_valid_temp = 45.0
        temp = self.hw.read_cpu_temp()
        self.assertEqual(temp, 45.0)  # 回退上次有效值

    def test_read_cpu_temp_filters_over_120(self):
        self._create_hwmon("coretemp", 0, {"temp1_input": "125000"})
        self._create_hwmon("it8772", 1, {"pwm1": "128", "fan1_input": "2000"})
        self.hw.detect_hwmon_paths()
        self.hw._last_valid_temp = 60.0
        temp = self.hw.read_cpu_temp()
        self.assertEqual(temp, 60.0)

    def test_read_fail_count(self):
        self.hw.coretemp_base = "/nonexistent"
        self.hw._last_valid_temp = 50.0
        for i in range(1, 4):
            self.hw.read_cpu_temp()
            self.assertEqual(self.hw.read_fail_count, i)
        self.assertTrue(self.hw.is_read_failure_critical)

    def test_reset_read_fail_count(self):
        self.hw._read_fail_count = 5
        self.hw.reset_read_fail_count()
        self.assertEqual(self.hw.read_fail_count, 0)

    def test_read_pwm(self):
        self._create_hwmon("it8772", 0, {"pwm2": "180", "fan2_input": "3000"})
        self.hw.detect_hwmon_paths()
        self.assertEqual(self.hw.read_pwm("pwm2"), 180)

    def test_read_fan_rpm(self):
        self._create_hwmon("it8772", 0, {"pwm2": "128", "fan2_input": "2500"})
        self.hw.detect_hwmon_paths()
        self.assertEqual(self.hw.read_fan_rpm("pwm2"), 2500)

    def test_write_pwm(self):
        self._create_hwmon("it8772", 0, {"pwm2": "128", "fan2_input": "2000"})
        self.hw.detect_hwmon_paths()
        result = self.hw.write_pwm(200, "pwm2", min_percent=25)
        self.assertTrue(result)
        self.assertEqual(self.hw.read_pwm("pwm2"), 200)

    def test_write_pwm_enforces_min(self):
        self._create_hwmon("it8772", 0, {"pwm2": "128", "fan2_input": "2000"})
        self.hw.detect_hwmon_paths()
        self.hw.write_pwm(0, "pwm2", min_percent=25)
        self.assertGreaterEqual(self.hw.read_pwm("pwm2"), 63)

    def test_write_pwm_no_it8772(self):
        result = self.hw.write_pwm(200, "pwm2")
        self.assertFalse(result)

    def test_set_pwm_mode(self):
        self._create_hwmon("it8772", 0, {"pwm2_enable": "1", "fan2_input": "2000", "pwm2": "128"})
        self.hw.detect_hwmon_paths()
        self.hw.set_pwm_mode(2, "pwm2")
        self.assertEqual(self.hw.read_pwm_enable("pwm2"), 2)

    def test_set_pwm_mode_invalid(self):
        self._create_hwmon("it8772", 0, {"pwm2_enable": "1", "fan2_input": "2000", "pwm2": "128"})
        self.hw.detect_hwmon_paths()
        result = self.hw.set_pwm_mode(5, "pwm2")
        self.assertFalse(result)

    def test_restore_safe_state(self):
        self._create_hwmon("it8772", 0, {
            "pwm1_enable": "1", "pwm2_enable": "1", "pwm3_enable": "1",
            "fan1_input": "0", "fan2_input": "2000", "fan3_input": "0",
            "pwm1": "128", "pwm2": "128", "pwm3": "128",
        })
        self.hw.detect_hwmon_paths()
        self.hw.restore_safe_state()
        self.assertEqual(self.hw.read_pwm_enable("pwm1"), 2)
        self.assertEqual(self.hw.read_pwm_enable("pwm2"), 2)
        self.assertEqual(self.hw.read_pwm_enable("pwm3"), 2)


if __name__ == "__main__":
    unittest.main()
