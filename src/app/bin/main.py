"""
飞牛NAS 风扇控制器 — 应用入口

启动流程：
1. 探测硬件路径
2. 恢复安全状态
3. 加载配置
4. 注册信号和退出处理
5. 设置 OOM 保护
6. 启动风扇控制线程
7. 启动 HTTP 服务
"""

import atexit
import logging
import os
import signal
import sys

# 确保能导入同目录模块
sys.path.insert(0, os.path.dirname(__file__))

from hardware import Hardware
from config_manager import ConfigManager
from fan_controller import FanController
from web_server import FanControlHTTPServer

logger = logging.getLogger(__name__)


def setup_logging():
    """配置日志"""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


def set_oom_score_adj(score: int = -500):
    """降低 OOM Kill 优先级"""
    try:
        with open("/proc/self/oom_score_adj", "w") as f:
            f.write(str(score))
        logger.info("OOM score adj 设置为 %d", score)
    except (OSError, IOError) as e:
        logger.warning("设置 OOM score adj 失败: %s", e)


def main():
    setup_logging()
    logger.info("风扇控制器启动中...")

    # ── 环境变量 ──
    app_dest = os.environ.get("TRIM_APPDEST", os.path.dirname(__file__))
    config_dir = os.environ.get("TRIM_PKGETC", "/tmp/fan-control-etc")
    port = int(os.environ.get("TRIM_SERVICE_PORT") or "9511")
    bind_address = os.environ.get("FAN_CONTROL_BIND", "0.0.0.0")

    # ── 1. 硬件探测 ──
    hw = Hardware()
    hw_detected = hw.detect_hwmon_paths()
    if not hw_detected:
        logger.warning("硬件探测失败，将以仅监控模式运行")

    # ── 2. 恢复安全状态 ──
    hw.restore_safe_state()

    # ── 3. 加载配置 ──
    cm = ConfigManager(config_dir, available_pwm=hw.available_pwm)
    config = cm.load()
    logger.info("当前配置: mode=%s, port=%d", config["mode"], port)

    # ── 4. 创建风扇控制器 ──
    fc = FanController(hw, cm)

    # ── 5. 注册退出处理 ──
    cleanup_done = False

    def cleanup():
        nonlocal cleanup_done
        if cleanup_done:
            return
        cleanup_done = True
        logger.info("执行清理...")
        fc.stop()

    def signal_handler(signum, frame):
        logger.info("收到信号 %d，准备退出", signum)
        cleanup()
        sys.exit(0)

    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)
    atexit.register(cleanup)

    # ── 6. 设置 OOM 保护 ──
    set_oom_score_adj(-500)

    # ── 7. 启动风扇控制线程 ──
    fc.start()

    # ── 8. 启动 HTTP 服务（主线程阻塞） ──
    try:
        server = FanControlHTTPServer(bind_address, port, fc, cm)
        logger.info("风扇控制器已就绪")
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    except Exception as e:
        logger.error("HTTP 服务异常: %s", e)
    finally:
        cleanup()


if __name__ == "__main__":
    main()
