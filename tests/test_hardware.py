"""硬件抽象层单元测试（Mock，不依赖真实硬件）"""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src", "app", "bin"))

from hardware import Hardware, safe_pwm_value, ABSOLUTE_MIN_PWM, PWM_ENABLE_SAFE, CPU_TEMP_DRIVERS


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

    def test_detect_pwm_chip(self):
        """探测 IT8772 芯片（通用探测，不硬编码芯片名）"""
        self._create_hwmon("it8772", 0, {
            "pwm1": "128", "pwm1_enable": "2",
            "pwm2": "200", "pwm2_enable": "1",
            "fan1_input": "0", "fan2_input": "3000",
        })
        result = self.hw.detect_hwmon_paths()
        self.assertTrue(result)
        self.assertTrue(self.hw.hw_detected)
        self.assertEqual(len(self.hw.chips), 1)
        self.assertEqual(self.hw.chips[0]["name"], "it8772")
        self.assertEqual(self.hw.chips[0]["display_name"], "ITE IT8772E")
        self.assertIn("pwm1", self.hw.available_pwm)
        self.assertIn("pwm2", self.hw.available_pwm)
        # fan2 有读数，fan1 为 0
        self.assertIn("pwm2", self.hw.available_fans)
        self.assertNotIn("pwm1", self.hw.available_fans)

    def test_detect_known_chip(self):
        """探测已知芯片（NCT6776）"""
        self._create_hwmon("nct6776", 0, {
            "pwm1": "128", "pwm1_enable": "2",
            "fan1_input": "2000",
        })
        result = self.hw.detect_hwmon_paths()
        self.assertTrue(result)
        self.assertEqual(self.hw.chips[0]["name"], "nct6776")
        self.assertEqual(self.hw.chips[0]["display_name"], "Nuvoton NCT6776")

    def test_detect_unknown_chip(self):
        """探测未知芯片，使用芯片名作为 display_name"""
        self._create_hwmon("some_new_chip", 0, {
            "pwm1": "128", "pwm1_enable": "2",
            "fan1_input": "2000",
        })
        result = self.hw.detect_hwmon_paths()
        self.assertTrue(result)
        self.assertEqual(self.hw.chips[0]["display_name"], "some_new_chip")

    def test_detect_multiple_chips(self):
        """探测多个芯片"""
        self._create_hwmon("it8772", 0, {
            "pwm1": "128", "pwm1_enable": "1",
            "fan1_input": "2000",
        })
        self._create_hwmon("nct6776", 1, {
            "pwm1": "128", "pwm1_enable": "2",
            "fan1_input": "1500",
        })
        result = self.hw.detect_hwmon_paths()
        self.assertTrue(result)
        self.assertEqual(len(self.hw.chips), 2)

    def test_detect_coretemp(self):
        self._create_hwmon("coretemp", 0, {"temp1_input": "47000"})
        self._create_hwmon("it8772", 1, {"pwm1": "128", "pwm1_enable": "2", "fan1_input": "2000"})
        self.hw.detect_hwmon_paths()
        self.assertIsNotNone(self.hw.cpu_temp_base)

    def test_no_pwm_chip_returns_false(self):
        """无 PWM 芯片时返回 False"""
        self._create_hwmon("coretemp", 0, {"temp1_input": "47000"})
        result = self.hw.detect_hwmon_paths()
        self.assertFalse(result)
        self.assertFalse(self.hw.hw_detected)

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
        self.assertEqual(temp, 45.0)

    def test_read_cpu_temp_filters_over_120(self):
        self._create_hwmon("coretemp", 0, {"temp1_input": "125000"})
        self._create_hwmon("it8772", 1, {"pwm1": "128", "fan1_input": "2000"})
        self.hw.detect_hwmon_paths()
        self.hw._last_valid_temp = 60.0
        temp = self.hw.read_cpu_temp()
        self.assertEqual(temp, 60.0)

    def test_read_fail_count(self):
        self.hw.cpu_temp_file = "/nonexistent/temp1_input"
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

    def test_write_pwm_no_chip(self):
        """通道不存在时写入失败"""
        result = self.hw.write_pwm(200, "pwm99")
        self.assertFalse(result)

    def test_set_pwm_mode(self):
        self._create_hwmon("it8772", 0, {"pwm2_enable": "1", "fan2_input": "2000", "pwm2": "128"})
        self.hw.detect_hwmon_paths()
        self.hw.set_pwm_mode(2, "pwm2")
        self.assertEqual(self.hw.read_pwm_enable("pwm2"), 2)

    def test_restore_safe_state(self):
        """恢复所有芯片的安全状态"""
        self._create_hwmon("it8772", 0, {
            "pwm1_enable": "1", "pwm2_enable": "1",
            "fan1_input": "0", "fan2_input": "2000",
            "pwm1": "128", "pwm2": "128",
        })
        self.hw.detect_hwmon_paths()
        self.hw.restore_safe_state()
        self.assertEqual(self.hw.read_pwm_enable("pwm1"), PWM_ENABLE_SAFE)
        self.assertEqual(self.hw.read_pwm_enable("pwm2"), PWM_ENABLE_SAFE)

    def test_restore_safe_state_multiple_chips(self):
        """多芯片时全部恢复"""
        self._create_hwmon("it8772", 0, {
            "pwm1_enable": "1", "pwm1": "128", "fan1_input": "2000",
        })
        self._create_hwmon("nct6776", 1, {
            "pwm1_enable": "1", "pwm1": "128", "fan1_input": "1500",
        })
        self.hw.detect_hwmon_paths()
        self.hw.restore_safe_state()
        for chip in self.hw.chips:
            for ch in chip["pwm_channels"]:
                enable_file = os.path.join(chip["hwmon_path"], f"{ch}_enable")
                val = self.hw._read_int_file(enable_file)
                self.assertEqual(val, PWM_ENABLE_SAFE)

    def test_hw_detected_property(self):
        """hw_detected 属性正确反映探测结果"""
        self.assertFalse(self.hw.hw_detected)
        self._create_hwmon("it8772", 0, {"pwm1": "128", "fan1_input": "2000"})
        self.hw.detect_hwmon_paths()
        self.assertTrue(self.hw.hw_detected)

    def test_get_hardware_info(self):
        """get_hardware_info 返回正确结构"""
        self._create_hwmon("it8772", 0, {"pwm1": "128", "pwm2": "200", "fan2_input": "3000"})
        self._create_hwmon("coretemp", 1, {"temp1_input": "47000"})
        self.hw.detect_hwmon_paths()
        info = self.hw.get_hardware_info()
        self.assertEqual(len(info["chips"]), 1)
        self.assertEqual(info["chips"][0]["name"], "it8772")
        self.assertIn("cpu", info["temp_sensors"])

    # ── AMD CPU 温度测试 ──

    def test_detect_k10temp(self):
        """探测 AMD k10temp"""
        self._create_hwmon("k10temp", 0, {"temp1_input": "55000"})
        self._create_hwmon("it8772", 1, {"pwm1": "128", "fan1_input": "2000"})
        self.hw.detect_hwmon_paths()
        self.assertIsNotNone(self.hw.cpu_temp_base)
        self.assertEqual(self.hw.cpu_temp_driver, "k10temp")
        self.assertEqual(self.hw.read_cpu_temp(), 55.0)

    def test_detect_zenpower(self):
        """探测 AMD zenpower"""
        self._create_hwmon("zenpower", 0, {"temp1_input": "62000"})
        self._create_hwmon("it8772", 1, {"pwm1": "128", "fan1_input": "2000"})
        self.hw.detect_hwmon_paths()
        self.assertEqual(self.hw.cpu_temp_driver, "zenpower")

    def test_coretemp_priority_over_k10temp(self):
        """Intel coretemp 优先级高于 AMD k10temp"""
        self._create_hwmon("coretemp", 0, {"temp1_input": "50000"})
        self._create_hwmon("k10temp", 1, {"temp1_input": "60000"})
        self._create_hwmon("it8772", 2, {"pwm1": "128", "fan1_input": "2000"})
        self.hw.detect_hwmon_paths()
        self.assertEqual(self.hw.cpu_temp_driver, "coretemp")
        self.assertEqual(self.hw.read_cpu_temp(), 50.0)

    def test_no_cpu_temp_sensor(self):
        """无 CPU 温度传感器时返回 None"""
        self._create_hwmon("it8772", 0, {"pwm1": "128", "fan1_input": "2000"})
        self.hw.detect_hwmon_paths()
        self.assertIsNone(self.hw.cpu_temp_base)
        self.assertIsNone(self.hw.cpu_temp_file)
        self.assertIsNone(self.hw.read_cpu_temp())

    def test_cpu_temp_driver_in_info(self):
        """get_hardware_info 返回正确的驱动名"""
        self._create_hwmon("k10temp", 0, {"temp1_input": "55000"})
        self._create_hwmon("it8772", 1, {"pwm1": "128", "fan1_input": "2000"})
        self.hw.detect_hwmon_paths()
        info = self.hw.get_hardware_info()
        self.assertEqual(info["temp_sensors"]["cpu"]["type"], "k10temp")

    # ── CPU 温度 label 匹配测试 ──

    def test_coretemp_package_label(self):
        """Intel coretemp 匹配 Package id 0 label"""
        self._create_hwmon("coretemp", 0, {
            "temp1_input": "45000", "temp1_label": "Core 0",
            "temp2_input": "47000", "temp2_label": "Core 1",
            "temp3_input": "50000", "temp3_label": "Package id 0",
        })
        self._create_hwmon("it8772", 1, {"pwm1": "128", "fan1_input": "2000"})
        self.hw.detect_hwmon_paths()
        # 应该选择 Package id 0 对应的 temp3
        self.assertEqual(self.hw.read_cpu_temp(), 50.0)

    def test_k10temp_tdie_label(self):
        """AMD k10temp 优先匹配 Tdie"""
        self._create_hwmon("k10temp", 0, {
            "temp1_input": "70000", "temp1_label": "Tctl",
            "temp2_input": "60000", "temp2_label": "Tdie",
        })
        self._create_hwmon("it8772", 1, {"pwm1": "128", "fan1_input": "2000"})
        self.hw.detect_hwmon_paths()
        # 应该选择 Tdie 而非 Tctl
        self.assertEqual(self.hw.read_cpu_temp(), 60.0)

    def test_k10temp_fallback_tctl(self):
        """AMD k10temp 无 Tdie 时回退 Tctl"""
        self._create_hwmon("k10temp", 0, {
            "temp1_input": "65000", "temp1_label": "Tctl",
        })
        self._create_hwmon("it8772", 1, {"pwm1": "128", "fan1_input": "2000"})
        self.hw.detect_hwmon_paths()
        self.assertEqual(self.hw.read_cpu_temp(), 65.0)

    def test_no_label_fallback_temp1(self):
        """无 label 文件时回退 temp1_input"""
        self._create_hwmon("coretemp", 0, {"temp1_input": "48000"})
        self._create_hwmon("it8772", 1, {"pwm1": "128", "fan1_input": "2000"})
        self.hw.detect_hwmon_paths()
        self.assertEqual(self.hw.read_cpu_temp(), 48.0)

    # ── 多芯片通道冲突测试 ──

    def test_multi_chip_channel_prefix(self):
        """多芯片通道名冲突时加前缀"""
        self._create_hwmon("it8772", 0, {
            "pwm1": "128", "fan1_input": "2000",
        })
        self._create_hwmon("nct6776", 1, {
            "pwm1": "200", "fan1_input": "1500",
        })
        self.hw.detect_hwmon_paths()
        # 两个芯片都有 pwm1，应该加前缀
        self.assertIn("chip0_pwm1", self.hw.available_pwm)
        self.assertIn("chip1_pwm1", self.hw.available_pwm)
        self.assertEqual(len(self.hw.available_pwm), 2)

    def test_multi_chip_no_conflict_no_prefix(self):
        """多芯片无冲突时不加前缀"""
        self._create_hwmon("it8772", 0, {
            "pwm1": "128", "fan1_input": "2000",
        })
        self._create_hwmon("nct6776", 1, {
            "pwm2": "200", "fan2_input": "1500",
        })
        self.hw.detect_hwmon_paths()
        # 无冲突，保持原名
        self.assertIn("pwm1", self.hw.available_pwm)
        self.assertIn("pwm2", self.hw.available_pwm)

    def test_multi_chip_read_write_with_prefix(self):
        """带前缀的通道能正确读写"""
        self._create_hwmon("it8772", 0, {
            "pwm1": "100", "pwm1_enable": "1", "fan1_input": "2000",
        })
        self._create_hwmon("nct6776", 1, {
            "pwm1": "200", "pwm1_enable": "2", "fan1_input": "1500",
        })
        self.hw.detect_hwmon_paths()
        # 读取各自芯片的值
        self.assertEqual(self.hw.read_pwm("chip0_pwm1"), 100)
        self.assertEqual(self.hw.read_pwm("chip1_pwm1"), 200)
        self.assertEqual(self.hw.read_pwm_enable("chip0_pwm1"), 1)
        self.assertEqual(self.hw.read_pwm_enable("chip1_pwm1"), 2)
        # 写入
        self.hw.write_pwm(150, "chip0_pwm1", min_percent=10)
        self.assertEqual(self.hw.read_pwm("chip0_pwm1"), 150)
        self.assertEqual(self.hw.read_pwm("chip1_pwm1"), 200)  # 不受影响

    def test_single_chip_no_prefix(self):
        """单芯片始终不加前缀"""
        self._create_hwmon("it8772", 0, {
            "pwm1": "128", "pwm2": "200",
            "fan1_input": "0", "fan2_input": "3000",
        })
        self.hw.detect_hwmon_paths()
        self.assertIn("pwm1", self.hw.available_pwm)
        self.assertIn("pwm2", self.hw.available_pwm)
        self.assertNotIn("chip0_pwm1", self.hw.available_pwm)

    def test_find_chip_nonexistent(self):
        """查找不存在的通道返回 (None, None)"""
        self._create_hwmon("it8772", 0, {"pwm1": "128", "fan1_input": "2000"})
        self.hw.detect_hwmon_paths()
        chip, local = self.hw._find_chip_for_channel("pwm99")
        self.assertIsNone(chip)
        self.assertIsNone(local)


if __name__ == "__main__":
    unittest.main()
