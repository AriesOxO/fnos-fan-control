"""共享 MockHardware，供所有测试文件使用"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src", "app", "bin"))

from hardware import Hardware, safe_pwm_value


class MockHardware(Hardware):
    """模拟硬件，不访问 /sys"""

    def __init__(self):
        super().__init__()
        self._mock_temp = 50.0
        self._mock_pwm = 128
        self._mock_enable = 1
        self._mock_rpm = 2500
        self._mock_disk_temps = {}

        self.chips = [{
            "name": "mock",
            "display_name": "Mock Chip",
            "hwmon_path": "/mock/hwmon",
            "pwm_channels": ["pwm1", "pwm2"],
            "fan_inputs": {"pwm2": "/mock/fan2"},
            "global_pwm_channels": ["pwm1", "pwm2"],
            "global_fan_inputs": {"pwm2": "/mock/fan2"},
        }]
        self.cpu_temp_base = "/mock/coretemp"
        self.cpu_temp_driver = "coretemp"
        self.cpu_temp_file = "/mock/coretemp/temp1_input"
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

    def get_hardware_info(self):
        return {
            "chips": [{"name": "mock", "display_name": "Mock Chip",
                        "pwm_channels": ["pwm2"], "fan_inputs": ["pwm2"]}],
            "temp_sensors": {"cpu": {"type": "coretemp", "current": self._mock_temp}},
        }
