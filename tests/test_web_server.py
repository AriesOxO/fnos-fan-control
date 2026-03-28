"""Web 服务集成测试"""

import json
import os
import sys
import tempfile
import threading
import time
import unittest
from urllib.request import Request, urlopen
from urllib.error import HTTPError

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src", "app", "bin"))

from hardware import Hardware
from config_manager import ConfigManager
from fan_controller import FanController
from web_server import FanControlHTTPServer


class MockHardware(Hardware):
    """模拟硬件"""

    def __init__(self):
        super().__init__()
        self._mock_temp = 50.0
        self._mock_pwm = 128
        self._mock_enable = 2
        self._mock_rpm = 2500
        self.it8772_base = "/mock"
        self.coretemp_base = "/mock"
        self.available_pwm = ["pwm2"]
        self.available_fans = {"pwm2": "/mock/fan2"}

    def read_cpu_temp(self):
        self._read_fail_count = 0
        self._last_valid_temp = self._mock_temp
        return self._mock_temp

    def read_disk_temps(self):
        return {}

    def read_fan_rpm(self, channel="pwm2"):
        return self._mock_rpm

    def read_pwm(self, channel="pwm2"):
        return self._mock_pwm

    def read_pwm_enable(self, channel="pwm2"):
        return self._mock_enable

    def write_pwm(self, value, channel="pwm2", min_percent=25):
        from hardware import safe_pwm_value
        self._mock_pwm = safe_pwm_value(value, min_percent)
        return True

    def set_pwm_mode(self, mode, channel="pwm2"):
        self._mock_enable = mode
        return True

    def restore_safe_state(self):
        self._mock_enable = 2


class TestWebAPI(unittest.TestCase):
    """Web API 集成测试"""

    PORT = 19511  # 使用非常规端口避免冲突

    @classmethod
    def setUpClass(cls):
        cls.hw = MockHardware()
        cls.tmpdir = tempfile.mkdtemp()
        cls.cm = ConfigManager(cls.tmpdir, available_pwm=["pwm2"])
        cls.cm.load()
        cls.fc = FanController(cls.hw, cls.cm)
        cls.fc.start()
        time.sleep(0.3)
        cls.server = FanControlHTTPServer("127.0.0.1", cls.PORT, cls.fc, cls.cm)
        cls.server_thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.server_thread.start()
        time.sleep(0.5)

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.fc.stop()

    def _get(self, path):
        url = f"http://127.0.0.1:{self.PORT}{path}"
        with urlopen(url, timeout=5) as resp:
            return json.loads(resp.read())

    def _post(self, path, data):
        url = f"http://127.0.0.1:{self.PORT}{path}"
        body = json.dumps(data).encode()
        req = Request(url, data=body, headers={"Content-Type": "application/json"})
        with urlopen(req, timeout=5) as resp:
            return json.loads(resp.read())

    def _post_raw(self, path, body_bytes, content_type="application/json"):
        url = f"http://127.0.0.1:{self.PORT}{path}"
        req = Request(url, data=body_bytes, headers={"Content-Type": content_type})
        return urlopen(req, timeout=5)

    # ── GET 测试 ──

    def test_get_status(self):
        data = self._get("/api/status")
        self.assertIn("cpu_temp", data)
        self.assertIn("mode", data)
        self.assertIn("hw_detected", data)
        self.assertIn("fan_rpm", data)

    def test_get_config(self):
        data = self._get("/api/config")
        self.assertIn("mode", data)
        self.assertIn("poll_interval", data)
        self.assertIn("curve", data)

    def test_get_logs(self):
        data = self._get("/api/logs")
        self.assertIsInstance(data, list)

    def test_get_404(self):
        with self.assertRaises(HTTPError) as ctx:
            self._get("/api/nonexistent")
        self.assertEqual(ctx.exception.code, 404)

    # ── POST /api/mode ──

    def test_post_mode_auto(self):
        data = self._post("/api/mode", {"mode": "auto"})
        self.assertTrue(data["ok"])
        status = self._get("/api/status")
        self.assertEqual(status["mode"], "auto")

    def test_post_mode_default(self):
        data = self._post("/api/mode", {"mode": "default"})
        self.assertTrue(data["ok"])

    def test_post_mode_invalid(self):
        with self.assertRaises(HTTPError) as ctx:
            self._post("/api/mode", {"mode": "bad"})
        self.assertEqual(ctx.exception.code, 400)

    def test_post_mode_missing_field(self):
        with self.assertRaises(HTTPError) as ctx:
            self._post("/api/mode", {"wrong": "auto"})
        self.assertEqual(ctx.exception.code, 400)

    # ── POST /api/config ──

    def test_post_config_update(self):
        data = self._post("/api/config", {"poll_interval": 5})
        self.assertTrue(data["ok"])
        self.assertEqual(data["config"]["poll_interval"], 5)

    def test_post_config_validates(self):
        data = self._post("/api/config", {"min_pwm_percent": 3})
        self.assertTrue(data["ok"])
        # 应回退到默认值而非 3
        self.assertGreaterEqual(data["config"]["min_pwm_percent"], 10)

    def test_post_config_ignores_mode(self):
        self._post("/api/mode", {"mode": "default"})
        data = self._post("/api/config", {"mode": "full", "poll_interval": 3})
        self.assertTrue(data["ok"])
        # mode 不应被 config API 修改
        self.assertEqual(data["config"]["mode"], "default")

    # ── POST /api/logs/clear ──

    def test_post_clear_logs(self):
        self._post("/api/mode", {"mode": "auto"})
        data = self._post("/api/logs/clear", {})
        self.assertTrue(data["ok"])
        logs = self._get("/api/logs")
        self.assertEqual(len(logs), 0)

    # ── POST /api/curve/generate ──

    def test_post_curve_generate(self):
        data = self._post("/api/curve/generate", {"count": 6, "temp_min": 30, "temp_max": 80})
        self.assertTrue(data["ok"])
        self.assertEqual(len(data["curve"]), 6)
        # 温度递增
        temps = [n["temp"] for n in data["curve"]]
        self.assertEqual(temps, sorted(temps))
        # PWM 在范围内
        for n in data["curve"]:
            self.assertGreaterEqual(n["pwm_percent"], 10)
            self.assertLessEqual(n["pwm_percent"], 100)

    def test_post_curve_generate_boundaries(self):
        data = self._post("/api/curve/generate", {"count": 2})
        self.assertTrue(data["ok"])
        self.assertEqual(len(data["curve"]), 2)

        data = self._post("/api/curve/generate", {"count": 10})
        self.assertTrue(data["ok"])
        self.assertEqual(len(data["curve"]), 10)

    # ── 安全测试 ──

    def test_invalid_json_returns_400(self):
        with self.assertRaises(HTTPError) as ctx:
            self._post_raw("/api/mode", b"not json")
        self.assertEqual(ctx.exception.code, 400)

    def test_non_object_returns_400(self):
        with self.assertRaises(HTTPError) as ctx:
            self._post_raw("/api/mode", b'"just a string"')
        self.assertEqual(ctx.exception.code, 400)

    # ── CORS ──

    def test_cors_headers(self):
        url = f"http://127.0.0.1:{self.PORT}/api/status"
        with urlopen(url, timeout=5) as resp:
            self.assertEqual(resp.headers.get("Access-Control-Allow-Origin"), "*")


if __name__ == "__main__":
    unittest.main()
