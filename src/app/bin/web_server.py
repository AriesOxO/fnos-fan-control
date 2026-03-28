"""
Web 服务 — 基于 http.server 的 REST API

端点：
  GET  /                        → index.html
  GET  /api/status              → 实时状态
  GET  /api/config              → 当前配置
  GET  /api/hardware            → 硬件探测结果
  GET  /api/logs                → 运行日志
  POST /api/config              → 更新配置
  POST /api/mode                → 切换模式（可指定区域）
  POST /api/logs/clear          → 清空日志
  POST /api/curve/generate      → 自动生成温控曲线
  POST /api/zones/{id}/mode     → 切换指定区域模式
  POST /api/zones/{id}/config   → 更新指定区域配置
"""

import json
import logging
import os
from http.server import HTTPServer, BaseHTTPRequestHandler

logger = logging.getLogger(__name__)

MAX_POST_BODY = 4096  # 4KB


class FanControlHandler(BaseHTTPRequestHandler):
    """HTTP 请求处理器，通过 server 引用访问 fan_controller 和 config_manager"""

    # 静默日志（不在 stderr 打印每个请求）
    def log_message(self, format, *args):
        pass

    def end_headers(self):
        # 允许被 iframe 嵌入 + 跨域访问
        self.send_header("X-Frame-Options", "ALLOWALL")
        self.send_header("Content-Security-Policy", "frame-ancestors *")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        super().end_headers()

    def do_OPTIONS(self):
        """处理 CORS 预检请求"""
        self.send_response(200)
        self.end_headers()

    def do_HEAD(self):
        """支持 HEAD 请求"""
        if self.path == "/" or self.path == "/index.html":
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
        else:
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.end_headers()

    def do_GET(self):
        try:
            if self.path == "/" or self.path == "/index.html":
                self._serve_static()
            elif self.path == "/api/status":
                self._json_response(self.server.fan_controller.get_status())
            elif self.path == "/api/config":
                self._json_response(self.server.config_manager.get())
            elif self.path == "/api/hardware":
                self._json_response(self.server.hardware.get_hardware_info())
            elif self.path == "/api/logs":
                self._json_response(self.server.fan_controller.get_logs())
            else:
                self._error_response(404, "Not Found")
        except Exception as e:
            logger.error("GET %s 处理异常: %s", self.path, e)
            self._error_response(500, "Internal Server Error")

    def do_POST(self):
        try:
            # 检查 body 大小
            content_length = int(self.headers.get("Content-Length", 0))
            if content_length > MAX_POST_BODY:
                self._error_response(413, "Request body too large")
                return
            if content_length == 0:
                self._error_response(400, "Empty request body")
                return

            # 读取并解析 JSON
            body = self.rfile.read(content_length)
            try:
                data = json.loads(body)
            except (json.JSONDecodeError, UnicodeDecodeError):
                self._error_response(400, "Invalid JSON")
                return

            if not isinstance(data, dict):
                self._error_response(400, "Request body must be a JSON object")
                return

            if self.path == "/api/config":
                self._handle_config_update(data)
            elif self.path == "/api/mode":
                self._handle_mode_switch(data)
            elif self.path == "/api/logs/clear":
                self.server.fan_controller.clear_logs()
                self._json_response({"ok": True, "message": "日志已清空"})
            elif self.path == "/api/curve/generate":
                self._handle_curve_generate(data)
            elif self.path.startswith("/api/zones/") and self.path.endswith("/mode"):
                zone_id = self.path.split("/")[3]
                self._handle_zone_mode_switch(zone_id, data)
            elif self.path.startswith("/api/zones/") and self.path.endswith("/config"):
                zone_id = self.path.split("/")[3]
                self._handle_zone_config_update(zone_id, data)
            else:
                self._error_response(404, "Not Found")

        except Exception as e:
            logger.error("POST %s 处理异常: %s", self.path, e)
            self._error_response(500, "Internal Server Error")

    def _handle_config_update(self, data: dict):
        """处理配置更新请求"""
        # 不允许通过 API 修改 mode（用 /api/mode 端点）和 web_port
        data.pop("mode", None)
        data.pop("web_port", None)

        updated = self.server.config_manager.update(data)
        changed = [k for k in data if k not in ("mode", "web_port")]
        if changed:
            self.server.fan_controller._add_log("info", "配置更新: " + ", ".join(changed))
        self._json_response({"ok": True, "config": updated})

    def _handle_mode_switch(self, data: dict):
        """处理模式切换请求（支持可选 zone_id）"""
        mode = data.get("mode")
        if not isinstance(mode, str):
            self._error_response(400, "Missing or invalid 'mode' field")
            return

        zone_id = data.get("zone_id")
        ok = self.server.fan_controller.set_mode(mode, zone_id=zone_id)
        if ok:
            self._json_response({"ok": True, "mode": mode})
        else:
            self._error_response(400, "Invalid mode or hardware not detected")

    def _handle_zone_mode_switch(self, zone_id: str, data: dict):
        """处理区域模式切换"""
        mode = data.get("mode")
        if not isinstance(mode, str):
            self._error_response(400, "Missing or invalid 'mode' field")
            return

        ok = self.server.fan_controller.set_mode(mode, zone_id=zone_id)
        if ok:
            self._json_response({"ok": True, "zone_id": zone_id, "mode": mode})
        else:
            self._error_response(400, "Invalid mode or zone not found")

    def _handle_zone_config_update(self, zone_id: str, data: dict):
        """处理区域配置更新"""
        data.pop("id", None)
        data.pop("channels", None)
        data.pop("mode", None)

        result = self.server.config_manager.update_zone(zone_id, data)
        if result is not None:
            self.server.fan_controller._add_log(
                "info", "区域 {} 配置更新: {}".format(zone_id, ", ".join(data.keys()))
            )
            self._json_response({"ok": True, "zone": result})
        else:
            self._error_response(404, "Zone '{}' not found".format(zone_id))

    def _handle_curve_generate(self, data: dict):
        """自动生成温控曲线节点"""
        try:
            count = max(2, min(10, int(data.get("count", 6))))
            temp_min = max(0, min(119, int(data.get("temp_min", 30))))
            temp_max = max(temp_min + count, min(120, int(data.get("temp_max", 80))))
            pwm_min = max(10, min(99, int(data.get("pwm_min", 20))))
            pwm_max = max(pwm_min + 1, min(100, int(data.get("pwm_max", 100))))
        except (TypeError, ValueError):
            self._error_response(400, "Invalid parameters")
            return

        curve = []
        for i in range(count):
            ratio = i / (count - 1) if count > 1 else 1
            temp = round(temp_min + (temp_max - temp_min) * ratio)
            pwm_ratio = ratio ** 1.3
            pwm = round(pwm_min + (pwm_max - pwm_min) * pwm_ratio)
            curve.append({"temp": temp, "pwm_percent": max(10, min(100, pwm))})

        self._json_response({"ok": True, "curve": curve})

    def _serve_static(self):
        """返回 index.html"""
        static_dir = os.path.join(os.path.dirname(__file__), "static")
        index_path = os.path.join(static_dir, "index.html")

        try:
            with open(index_path, "r", encoding="utf-8") as f:
                content = f.read()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(content.encode("utf-8"))))
            self.end_headers()
            self.wfile.write(content.encode("utf-8"))
        except FileNotFoundError:
            self._error_response(404, "index.html not found")

    def _json_response(self, data, status_code: int = 200):
        """发送 JSON 响应"""
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _error_response(self, status_code: int, message: str):
        """发送错误 JSON 响应"""
        self._json_response({"ok": False, "error": message}, status_code)


class FanControlHTTPServer(HTTPServer):
    """扩展 HTTPServer，持有 fan_controller、config_manager 和 hardware 引用"""

    def __init__(self, bind_address: str, port: int, fan_controller, config_manager, hardware=None):
        self.fan_controller = fan_controller
        self.config_manager = config_manager
        self.hardware = hardware
        super().__init__((bind_address, port), FanControlHandler)
        logger.info("Web 服务启动: http://%s:%d", bind_address, port)
