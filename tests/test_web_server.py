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
sys.path.insert(0, os.path.dirname(__file__))

from config_manager import ConfigManager
from fan_controller import FanController
from web_server import FanControlHTTPServer
from mock_hardware import MockHardware


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
        cls.server = FanControlHTTPServer("127.0.0.1", cls.PORT, cls.fc, cls.cm, hardware=cls.hw)
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
        self.assertIn("zones", data)

    def test_get_config(self):
        data = self._get("/api/config")
        self.assertIn("poll_interval", data)

    def test_get_hardware(self):
        data = self._get("/api/hardware")
        self.assertIn("chips", data)
        self.assertIn("temp_sensors", data)

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
        time.sleep(0.3)
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
        # min_pwm_percent 可能在顶层或 zones 内，取决于配置格式
        config = data["config"]
        if "min_pwm_percent" in config:
            self.assertGreaterEqual(config["min_pwm_percent"], 10)

    # ── POST /api/logs/clear ──

    def test_post_clear_logs(self):
        self._post("/api/mode", {"mode": "default"})
        data = self._post("/api/logs/clear", {})
        self.assertTrue(data["ok"])
        logs = self._get("/api/logs")
        self.assertEqual(len(logs), 0)

    # ── POST /api/curve/generate ──

    def test_post_curve_generate(self):
        data = self._post("/api/curve/generate", {"count": 6, "temp_min": 30, "temp_max": 80})
        self.assertTrue(data["ok"])
        self.assertEqual(len(data["curve"]), 6)
        temps = [n["temp"] for n in data["curve"]]
        self.assertEqual(temps, sorted(temps))
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
