"""配置管理单元测试"""

import json
import os
import sys
import tempfile
import threading
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src", "app", "bin"))

from config_manager import (
    ConfigManager, DEFAULT_CONFIG, validate_config,
    ABSOLUTE_MIN_PERCENT, _validate_curve, _validate_mode,
)


class TestValidateMode(unittest.TestCase):
    """模式校验测试"""

    def test_valid_modes(self):
        for mode in ("default", "auto", "manual", "full"):
            self.assertEqual(_validate_mode(mode), mode)

    def test_invalid_mode_returns_default(self):
        self.assertEqual(_validate_mode("invalid"), "default")
        self.assertEqual(_validate_mode(""), "default")
        self.assertEqual(_validate_mode(None), "default")
        self.assertEqual(_validate_mode(123), "default")


class TestValidateCurve(unittest.TestCase):
    """温控曲线校验测试"""

    def test_valid_curve(self):
        curve = [{"temp": 30, "pwm_percent": 20}, {"temp": 70, "pwm_percent": 100}]
        result = _validate_curve(curve)
        self.assertEqual(result, curve)

    def test_max_10_nodes(self):
        curve = [{"temp": i * 10, "pwm_percent": 10 + i * 9} for i in range(10)]
        result = _validate_curve(curve)
        self.assertEqual(len(result), 10)

    def test_over_10_nodes_fallback(self):
        curve = [{"temp": i * 5, "pwm_percent": 10 + i * 4} for i in range(11)]
        result = _validate_curve(curve)
        self.assertEqual(result, DEFAULT_CONFIG["curve"])

    def test_less_than_2_nodes_fallback(self):
        result = _validate_curve([{"temp": 50, "pwm_percent": 50}])
        self.assertEqual(result, DEFAULT_CONFIG["curve"])

    def test_empty_fallback(self):
        self.assertEqual(_validate_curve([]), DEFAULT_CONFIG["curve"])
        self.assertEqual(_validate_curve(None), DEFAULT_CONFIG["curve"])
        self.assertEqual(_validate_curve("bad"), DEFAULT_CONFIG["curve"])

    def test_temp_not_ascending_fallback(self):
        curve = [{"temp": 50, "pwm_percent": 50}, {"temp": 40, "pwm_percent": 75}]
        self.assertEqual(_validate_curve(curve), DEFAULT_CONFIG["curve"])

    def test_temp_equal_fallback(self):
        curve = [{"temp": 50, "pwm_percent": 50}, {"temp": 50, "pwm_percent": 75}]
        self.assertEqual(_validate_curve(curve), DEFAULT_CONFIG["curve"])

    def test_temp_out_of_range_fallback(self):
        curve = [{"temp": -1, "pwm_percent": 50}, {"temp": 70, "pwm_percent": 100}]
        self.assertEqual(_validate_curve(curve), DEFAULT_CONFIG["curve"])

        curve = [{"temp": 30, "pwm_percent": 50}, {"temp": 121, "pwm_percent": 100}]
        self.assertEqual(_validate_curve(curve), DEFAULT_CONFIG["curve"])

    def test_pwm_below_absolute_min_fallback(self):
        curve = [{"temp": 30, "pwm_percent": 5}, {"temp": 70, "pwm_percent": 100}]
        self.assertEqual(_validate_curve(curve), DEFAULT_CONFIG["curve"])

    def test_pwm_over_100_fallback(self):
        curve = [{"temp": 30, "pwm_percent": 50}, {"temp": 70, "pwm_percent": 110}]
        self.assertEqual(_validate_curve(curve), DEFAULT_CONFIG["curve"])

    def test_missing_fields_fallback(self):
        curve = [{"temp": 30}, {"temp": 70, "pwm_percent": 100}]
        self.assertEqual(_validate_curve(curve), DEFAULT_CONFIG["curve"])


class TestValidateConfig(unittest.TestCase):
    """完整配置校验测试"""

    def test_valid_config(self):
        cfg = {
            "mode": "auto",
            "poll_interval": 5,
            "min_pwm_percent": 30,
            "temp_source": "max",
            "manual_pwm_percent": 60,
            "curve": [{"temp": 30, "pwm_percent": 20}, {"temp": 80, "pwm_percent": 100}],
            "fan_channel": "pwm2",
            "web_port": 8080,
        }
        result = validate_config(cfg)
        self.assertEqual(result["mode"], "auto")
        self.assertEqual(result["poll_interval"], 5)
        self.assertEqual(result["min_pwm_percent"], 30)

    def test_empty_dict_uses_defaults(self):
        result = validate_config({})
        self.assertEqual(result["mode"], DEFAULT_CONFIG["mode"])
        self.assertEqual(result["poll_interval"], DEFAULT_CONFIG["poll_interval"])

    def test_invalid_fields_fallback_individually(self):
        cfg = {
            "mode": "bad",
            "poll_interval": 999,
            "min_pwm_percent": 5,
            "temp_source": "bad",
            "manual_pwm_percent": -1,
            "web_port": 80,
        }
        result = validate_config(cfg)
        self.assertEqual(result["mode"], "default")
        self.assertEqual(result["poll_interval"], DEFAULT_CONFIG["poll_interval"])
        self.assertEqual(result["min_pwm_percent"], DEFAULT_CONFIG["min_pwm_percent"])
        self.assertEqual(result["temp_source"], "cpu")
        self.assertEqual(result["manual_pwm_percent"], DEFAULT_CONFIG["manual_pwm_percent"])
        self.assertEqual(result["web_port"], DEFAULT_CONFIG["web_port"])

    def test_min_pwm_percent_absolute_floor(self):
        result = validate_config({"min_pwm_percent": ABSOLUTE_MIN_PERCENT})
        self.assertEqual(result["min_pwm_percent"], ABSOLUTE_MIN_PERCENT)

        result = validate_config({"min_pwm_percent": ABSOLUTE_MIN_PERCENT - 1})
        self.assertEqual(result["min_pwm_percent"], DEFAULT_CONFIG["min_pwm_percent"])

    def test_poll_interval_boundary(self):
        self.assertEqual(validate_config({"poll_interval": 1})["poll_interval"], 1)
        self.assertEqual(validate_config({"poll_interval": 30})["poll_interval"], 30)
        self.assertEqual(validate_config({"poll_interval": 0})["poll_interval"], DEFAULT_CONFIG["poll_interval"])
        self.assertEqual(validate_config({"poll_interval": 31})["poll_interval"], DEFAULT_CONFIG["poll_interval"])


class TestConfigManager(unittest.TestCase):
    """ConfigManager 测试"""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()

    def test_load_missing_file_uses_defaults(self):
        cm = ConfigManager(self.tmpdir)
        cfg = cm.load()
        self.assertEqual(cfg["mode"], DEFAULT_CONFIG["mode"])

    def test_save_and_reload(self):
        cm = ConfigManager(self.tmpdir)
        cm.load()
        cm.save()

        cm2 = ConfigManager(self.tmpdir)
        cfg2 = cm2.load()
        self.assertEqual(cfg2, cm.get())

    def test_corrupted_json_uses_defaults(self):
        with open(os.path.join(self.tmpdir, "config.json"), "w") as f:
            f.write("{invalid json!!!")

        cm = ConfigManager(self.tmpdir)
        cfg = cm.load()
        self.assertEqual(cfg["mode"], DEFAULT_CONFIG["mode"])

    def test_partial_update(self):
        cm = ConfigManager(self.tmpdir)
        cm.load()
        updated = cm.update({"poll_interval": 10, "mode": "auto"})
        self.assertEqual(updated["poll_interval"], 10)
        self.assertEqual(updated["mode"], "auto")
        # 其他字段保持默认
        self.assertEqual(updated["min_pwm_percent"], DEFAULT_CONFIG["min_pwm_percent"])

    def test_update_persists_to_file(self):
        cm = ConfigManager(self.tmpdir)
        cm.load()
        cm.update({"poll_interval": 7})

        cm2 = ConfigManager(self.tmpdir)
        cfg2 = cm2.load()
        self.assertEqual(cfg2["poll_interval"], 7)

    def test_update_validates(self):
        cm = ConfigManager(self.tmpdir)
        cm.load()
        updated = cm.update({"min_pwm_percent": 3})
        self.assertEqual(updated["min_pwm_percent"], DEFAULT_CONFIG["min_pwm_percent"])

    def test_get_returns_copy(self):
        cm = ConfigManager(self.tmpdir)
        cm.load()
        cfg1 = cm.get()
        cfg2 = cm.get()
        self.assertEqual(cfg1, cfg2)
        cfg1["mode"] = "modified"
        self.assertNotEqual(cfg1["mode"], cm.get()["mode"])

    def test_thread_safety(self):
        cm = ConfigManager(self.tmpdir)
        cm.load()
        errors = []

        def reader():
            for _ in range(50):
                try:
                    cm.get()
                except Exception as e:
                    errors.append(e)

        def writer():
            for i in range(50):
                try:
                    cm.update({"poll_interval": (i % 30) + 1})
                except Exception as e:
                    errors.append(e)

        threads = [threading.Thread(target=reader) for _ in range(4)]
        threads += [threading.Thread(target=writer) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(len(errors), 0, f"并发错误: {errors}")


if __name__ == "__main__":
    unittest.main()
